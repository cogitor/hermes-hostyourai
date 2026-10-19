"""``/hyai`` — HostYourAI (hyai/auto) task/policy routing hints.

Mirrors the ``/fast``/``/reasoning`` UX (status/set/off, ``--global`` for a
persisted default) but a general slash-command plugin has no public handle on
``agent.request_overrides`` (that channel is core-CLI-only, used by the
built-in ``/fast``/``/reasoning`` commands which run as CLI methods, not
plugin hooks). Instead this command persists sticky task/policy state to this
plugin's own config namespace via ``ctx.get_config``/``set_config``
(``plugins.entries.hyai-router.settings.hyai_routing`` in config.yaml) — a
process-independent read, so the companion ``hostyourai`` provider-profile
plugin's ``build_extra_body``/``build_api_kwargs_extras`` hooks (which DO run
on every real request, in-process, regardless of CLI vs gateway) read the
same value and apply it to the wire request. No session-only state: every
set is effectively ``--global`` today, since there is no plugin-visible
per-session store wired to the request path. ``--global`` is accepted and
kept as a no-op/compat flag in case a session-scoped channel is added later.

``argument_mode="options"`` tells CLI/TUI/Desktop surfaces this command has a
finite completion list rather than free text, so typing ``/hyai `` offers
``task``/``policy``/``status``/``off`` and, once a sub-command is picked,
their own enum values — the closest thing to a fill-in dialog a general
slash-command plugin can drive (there is no plugin-facing modal/picker hook;
see ``plugins/AGENTS.md`` "Plugins never touch core"). Surfaces that don't
implement options-mode completion (plain text adapters) still work — the
handler accepts typed text identically either way.

Usage:
    /hyai                      show current task/policy
    /hyai status               same as above
    /hyai task <value>         set sticky task (see HYAI_TASKS)
    /hyai policy <value>       set sticky policy (see HYAI_POLICIES)
    /hyai off | clear          remove the override, let hyai/auto decide alone

Future extension point: HostYourAI has floated a Jev-like auto-classify mode
(not shipped as of 2026-10-18) that would pick ``task`` per-turn from the
prompt instead of a fixed sticky value. When that ships: add ``task auto`` as
a third state, have the provider profile call the classifier before building
the request (in ``build_extra_body``), default to "chat" on any classifier
failure/timeout, and keep it opt-in (extra latency/cost per turn) — never the
default. No structural change needed here beyond accepting "auto" as a value.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

HYAI_TASKS = ("chat", "code", "summarise", "classify", "extract", "rag", "legal", "reason")
HYAI_POLICIES = ("balanced", "speed", "cost", "quality")

CONFIG_KEY = "hyai_routing"  # read by the hostyourai provider profile too
_SUBCOMMANDS = ("status", "task", "policy", "off", "clear")
_USAGE = (
    "Usage: /hyai [status|task <value>|policy <value>|off|clear]\n"
    f"  task:   {'|'.join(HYAI_TASKS)}\n"
    f"  policy: {'|'.join(HYAI_POLICIES)}"
)


def _format_status(state: dict) -> str:
    if not state.get("task") and not state.get("policy"):
        return "HostYourAI routing: not set (hyai/auto decides per request)"
    return (
        f"HostYourAI routing: task={state.get('task') or '(unset)'} policy={state.get('policy') or '(unset)'}"
        " (applies to every chat on this Hermes profile)"
    )


def _config_route() -> tuple[str, str]:
    """Configured main (provider, model): only the fallback for a chat with no /model override."""
    from hermes_cli.config import load_config_readonly

    model = (load_config_readonly() or {}).get("model")
    if isinstance(model, str):  # legacy plain-string form
        return "", model
    if not isinstance(model, dict):
        return "", ""
    return str(model.get("provider") or ""), str(model.get("default") or "")


def _routing_override(db_path, scope: str, session_key: str) -> tuple[str, str]:
    """(provider, model) of the chat's persisted /model override, read straight from state.db.

    Read-only, short-lived connection (no schema init/migration, no contention with the gateway's
    writer). Rows are keyed (scope, session_key); scope is the gateway's resolved sessions dir, so
    another profile's row for the same key can never match.
    """
    import json
    import sqlite3
    from urllib.parse import quote

    conn = sqlite3.connect(f"file:{quote(str(db_path))}?mode=ro", uri=True, timeout=2)
    try:
        row = conn.execute(
            "SELECT entry_json FROM gateway_routing WHERE scope = ? AND session_key = ?",
            (scope, session_key),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        # Every chat that has messaged the gateway has a routing row. A missing one means our
        # scope guess is wrong (e.g. a custom gateway.sessions_dir): refuse, don't guess.
        raise LookupError(f"no routing row for session {session_key!r} in scope {scope!r}")
    override = json.loads(row[0]).get("model_override") or {}
    return str(override.get("provider") or ""), str(override.get("model") or "")


def _session_route() -> tuple[str, str]:
    """(provider, model) from this chat's persisted /model override; ("", "") if none.

    The gateway stores every /model switch on the chat's routing entry and binds
    HERMES_SESSION_KEY while a plugin command runs, so this is the user's real selection, not
    the config default. A one-turn /model --once override is memory-only and not visible here.
    """
    from gateway.session_context import get_session_env
    from hermes_constants import get_hermes_home

    session_key = get_session_env("HERMES_SESSION_KEY")
    if not session_key:
        return "", ""
    home = get_hermes_home()
    return _routing_override(home / "state.db", str((home / "sessions").resolve()), session_key)


def _active_model() -> tuple[str, str]:
    """(provider, model) in effect for this chat; ("", "") if undeterminable (fails closed)."""
    try:
        provider, model = _session_route()
        if not model:
            return _config_route()
        return provider, model
    except Exception as exc:
        logger.debug("hyai: could not determine active model, refusing: %r", exc)
        return "", ""


def _not_applicable() -> str | None:
    provider, model = _active_model()
    # An unrecorded provider is fine: the model id hyai/auto is itself HostYourAI-specific.
    if model == "hyai/auto" and provider in ("", "hostyourai"):
        return None
    shown = "/".join(x for x in (provider, model) if x) or "unknown"
    return f"/hyai only applies to hostyourai hyai/auto. Current model: {shown}. Nothing changed."


def register(ctx) -> None:
    def _get_state() -> dict:
        value = ctx.get_config(CONFIG_KEY, {})
        return dict(value) if isinstance(value, dict) else {}

    def _handle_hyai(raw_args: str):
        blocked = _not_applicable()
        if blocked:
            return blocked
        parts = (raw_args or "").split()
        state = _get_state()

        if not parts:
            # Bare /hyai: no picker exists on text surfaces (Telegram), so show state + valid choices.
            return f"{_format_status(state)}\n\n{_USAGE}"
        if parts[0].lower() == "status":
            return _format_status(state)

        cmd = parts[0].lower()
        if cmd in ("off", "clear"):
            # Keep any hand-set provider option (e.g. transport); only clear task/policy.
            ctx.set_config(CONFIG_KEY, {"transport": state["transport"]} if state.get("transport") else {})
            return "HostYourAI routing cleared — hyai/auto decides per request."

        if cmd == "task":
            if len(parts) < 2 or parts[1].lower() not in HYAI_TASKS:
                return f"Unknown task.\n{_USAGE}"
            state = {**state, "task": parts[1].lower()}
            ctx.set_config(CONFIG_KEY, state)
            return _format_status(state)

        if cmd == "policy":
            if len(parts) < 2 or parts[1].lower() not in HYAI_POLICIES:
                return f"Unknown policy.\n{_USAGE}"
            state = {**state, "policy": parts[1].lower()}
            ctx.set_config(CONFIG_KEY, state)
            return _format_status(state)

        return _USAGE

    ctx.register_command(
        "hyai",
        handler=_handle_hyai,
        description="Set HostYourAI (hyai/auto) task/policy routing hints",
        # args_hint must NOT start with "<": Hermes hides such plugin commands from the Telegram menu
        args_hint="[status|task|policy|off] [value]",
        argument_mode="options",
    )
