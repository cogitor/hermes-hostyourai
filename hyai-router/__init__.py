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

Usage:
    /hyai                      show current task/policy/transport
    /hyai status               same as above
    /hyai task <value>         set sticky task (see HYAI_TASKS)
    /hyai policy <value>       set sticky policy (see HYAI_POLICIES)
    /hyai transport <body|headers>   choose body vs X-HYAI-* headers
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

HYAI_TASKS = ("chat", "code", "summarise", "classify", "extract", "rag", "legal", "reason")
HYAI_POLICIES = ("balanced", "speed", "cost", "quality")
HYAI_TRANSPORTS = ("body", "headers")

CONFIG_KEY = "hyai_routing"  # read by the hostyourai provider profile too
_USAGE = (
    "Usage: /hyai [status|task <value>|policy <value>|transport <body|headers>|off|clear]\n"
    f"  task:      {'|'.join(HYAI_TASKS)}\n"
    f"  policy:    {'|'.join(HYAI_POLICIES)}\n"
    f"  transport: {'|'.join(HYAI_TRANSPORTS)}"
)


def _format_status(state: dict) -> str:
    if not state.get("task") and not state.get("policy"):
        return "HostYourAI routing: not set (hyai/auto decides per request)"
    return (
        f"HostYourAI routing: task={state.get('task') or '(unset)'} "
        f"policy={state.get('policy') or '(unset)'} "
        f"transport={state.get('transport', 'body')}"
    )


def register(ctx) -> None:
    def _get_state() -> dict:
        value = ctx.get_config(CONFIG_KEY, {})
        return dict(value) if isinstance(value, dict) else {}

    def _handle_hyai(raw_args: str):
        parts = (raw_args or "").split()
        state = _get_state()

        if not parts or parts[0].lower() == "status":
            return _format_status(state)

        cmd = parts[0].lower()
        if cmd in ("off", "clear"):
            ctx.set_config(CONFIG_KEY, {})
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

        if cmd == "transport":
            if len(parts) < 2 or parts[1].lower() not in HYAI_TRANSPORTS:
                return f"Unknown transport.\n{_USAGE}"
            state = {**state, "transport": parts[1].lower()}
            ctx.set_config(CONFIG_KEY, state)
            return _format_status(state)

        return _USAGE

    ctx.register_command(
        "hyai",
        handler=_handle_hyai,
        description="Set HostYourAI (hyai/auto) task/policy routing hints",
    )
