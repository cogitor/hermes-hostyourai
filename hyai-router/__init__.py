"""``/hyai``: HostYourAI task/policy routing hints for ``hyai/auto``, and account balance.

The task/policy choice is saved in this plugin's config (``CONFIG_KEY``). The companion
``hostyourai`` provider profile reads it on every request, so it applies to every chat on the
profile. ``status`` and ``usage`` work on any model; changing the routing only applies to ``hyai/auto``.

    /hyai                      show current task/policy
    /hyai status               same as above
    /hyai task <value>         set sticky task (see HYAI_TASKS)
    /hyai policy <value>       set sticky policy (see HYAI_POLICIES)
    /hyai code cost            shorthand: task and/or policy in any order
    /hyai off | clear          remove the override, let hyai/auto decide alone
    /hyai usage                balance and this month's spend (private chat only)
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

logger = logging.getLogger(__name__)

HYAI_TASKS = ("chat", "code", "summarise", "classify", "extract", "rag", "legal", "reason")
HYAI_POLICIES = ("balanced", "speed", "cost", "quality")

CONFIG_KEY = "hyai_routing"  # read by the hostyourai provider profile too
_USAGE = (
    "Usage: /hyai [status|task <value>|policy <value>|off|clear|usage]\n"
    f"  task:   {'|'.join(HYAI_TASKS)}\n"
    f"  policy: {'|'.join(HYAI_POLICIES)}\n"
    "  shorthand: /hyai code cost (a task and/or a policy, in any order)"
)

API_BASE = "https://hostyourai.com/api/v1"  # the only host the API key is ever sent to
_BALANCE_TTL = 60.0
_HTTP_TIMEOUT = 5.0
_LOCAL_SOURCES = ("cli", "tui", "desktop", "local")
_PRIVATE_ONLY = "Balance and spend are only shown in a private chat (or the local CLI). Nothing was fetched."
_HTTP_ERRORS = {401: "key rejected by HostYourAI", 403: "this endpoint needs a personal key, not a team key"}


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
    return f"Routing hints only apply to hostyourai hyai/auto. Current model: {shown}. Nothing changed."


def _private_chat() -> bool:
    """True for a DM or the local CLI/TUI; anything else, including any error, is refused."""
    try:
        from gateway.session_context import get_session_env

        chat_type = (get_session_env("HERMES_SESSION_CHAT_TYPE") or "").strip().lower()
        if chat_type:
            return chat_type == "dm"
        if get_session_env("HERMES_SESSION_PLATFORM") or get_session_env("HERMES_SESSION_KEY"):
            return False  # gateway session of unknown type
        source = (get_session_env("HERMES_SESSION_SOURCE") or "").strip().lower()
        if source:
            return source in _LOCAL_SOURCES
        # Nothing bound is the plain CLI, but also a gateway command that lost its context.
        return "gateway.run" not in sys.modules
    except Exception as exc:
        logger.debug("hyai: chat type undeterminable, refusing usage: %r", exc)
        return False


class _ApiError(Exception):
    """A one-line message that is safe to show the user (never contains the key)."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a redirect would forward the key to another host


_OPENER = urllib.request.build_opener(_NoRedirect)


def _http_get(path: str) -> tuple[int, str, str]:
    """GET API_BASE+path with the key. Returns (status, retry-after header, body)."""
    key = os.environ.get("HOSTYOURAI_API_KEY", "").strip()
    if not key:
        raise _ApiError("no HOSTYOURAI_API_KEY set")
    req = urllib.request.Request(API_BASE + path, headers={"Authorization": f"Bearer {key}"})
    try:
        with _OPENER.open(req, timeout=_HTTP_TIMEOUT) as resp:
            return resp.status, "", resp.read(2_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        exc.close()
        return exc.code, (exc.headers or {}).get("Retry-After", ""), ""
    except Exception as exc:  # network, timeout, TLS; the text could hold the key, so log the type only
        logger.debug("hyai: request failed: %s", type(exc).__name__)
        raise _ApiError("HostYourAI unreachable") from None


_cache: tuple[str, float, dict] | None = None  # (key, fetched_at, balance)
_cache_lock = threading.Lock()


def _fetch_balance() -> dict:
    global _cache
    key = os.environ.get("HOSTYOURAI_API_KEY", "")
    with _cache_lock:
        if _cache and _cache[0] == key and time.monotonic() - _cache[1] < _BALANCE_TTL:
            return _cache[2]
        status, retry_after, body = _http_get("/balance")
        if status == 429:
            raise _ApiError(
                f"rate limited, retry in {retry_after}s" if retry_after.isdigit() else "rate limited"
            )
        if status in _HTTP_ERRORS:
            raise _ApiError(_HTTP_ERRORS[status])
        if status != 200:
            raise _ApiError(f"HostYourAI returned HTTP {status}")
        try:
            balance = json.loads(body)
        except (ValueError, RecursionError):
            balance = None
        if not isinstance(balance, dict):
            raise _ApiError("unexpected response from HostYourAI")
        _cache = (key, time.monotonic(), balance)
        return balance


def _num(value, default=0.0) -> float:
    """value as a finite, non-negative float, else default."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) and number >= 0 else default


def _eur(value: float) -> str:
    return f"€{value:,.2f}"


def _when(value) -> str:
    """An ISO timestamp as local 'Oct 19, 10:42'; empty if it is not one."""
    try:
        # Python before 3.11 does not accept a trailing "Z".
        stamp = value[:-1] + "+00:00" if value.endswith("Z") else value
        return datetime.fromisoformat(stamp).astimezone().strftime("%b %d, %H:%M")
    except (AttributeError, TypeError, ValueError, OverflowError):
        return ""


def _report_usage(ctx) -> str:
    if not _private_chat():
        return _PRIVATE_ONLY
    try:
        data = _fetch_balance()
    except _ApiError as exc:
        return f"HostYourAI balance unavailable: {exc}."
    balance = _num(data.get("balance"))
    unlimited = bool(data.get("unlimited"))
    lines = [
        "Balance: unlimited" if unlimited else f"Balance: {_eur(balance)}",
        f"Spent this month: {_eur(_num(data.get('spend_month')))}",
        f"Auto top-up: {'on' if data.get('auto_topup') else 'off'}",
    ]
    as_of = _when(data.get("as_of"))
    if as_of:
        lines.append(f"As of: {as_of}")
    warn = _num(ctx.get_config("usage_warn_eur", 5), 5.0)
    if not unlimited and warn > 0 and balance < warn:
        lines.append(f"Low balance (below {_eur(warn)}).")
    return "\n".join(lines)


def register(ctx) -> None:
    def _get_state() -> dict:
        value = ctx.get_config(CONFIG_KEY, {})
        return dict(value) if isinstance(value, dict) else {}

    def _handle_hyai(raw_args: str):
        parts = (raw_args or "").split()
        first = parts[0].lower() if parts else ""
        # usage is about the account, not routing, so it skips the hyai/auto gate (it has its own).
        if first == "usage":
            return _report_usage(ctx)
        if first == "help":
            return _USAGE
        state = _get_state()

        # Reading the setting works on any model; only changing it needs hyai/auto.
        if not parts or first == "status":
            status = _format_status(state)
            note = _not_applicable()
            if note:
                status += f"\n{note}"
            # No picker exists on text surfaces (Telegram), so bare /hyai also lists the choices.
            return status if parts else f"{status}\n\n{_USAGE}"

        blocked = _not_applicable()
        if blocked:
            return blocked

        if first in ("off", "clear"):
            # Keep any hand-set provider option (e.g. transport); only clear task/policy.
            ctx.set_config(CONFIG_KEY, {"transport": state["transport"]} if state.get("transport") else {})
            return "HostYourAI routing cleared — hyai/auto decides per request."

        if first == "task":
            if len(parts) < 2 or parts[1].lower() not in HYAI_TASKS:
                return f"Unknown task.\n{_USAGE}"
            state = {**state, "task": parts[1].lower()}
            ctx.set_config(CONFIG_KEY, state)
            return _format_status(state)

        if first == "policy":
            if len(parts) < 2 or parts[1].lower() not in HYAI_POLICIES:
                return f"Unknown policy.\n{_USAGE}"
            state = {**state, "policy": parts[1].lower()}
            ctx.set_config(CONFIG_KEY, state)
            return _format_status(state)

        # Shorthand: bare values in any order, at most one task and one policy.
        words = [p.lower() for p in parts]
        tasks = [w for w in words if w in HYAI_TASKS]
        policies = [w for w in words if w in HYAI_POLICIES]
        if len(tasks) + len(policies) == len(words) and len(tasks) <= 1 and len(policies) <= 1:
            state = {
                **state,
                **({"task": tasks[0]} if tasks else {}),
                **({"policy": policies[0]} if policies else {}),
            }
            ctx.set_config(CONFIG_KEY, state)
            return _format_status(state)

        return _USAGE

    ctx.register_command(
        "hyai",
        handler=_handle_hyai,
        description="HostYourAI: routing hints and balance",
        # must not start with "<": Hermes hides such commands from the Telegram menu
        args_hint="[status|task|policy|usage|off|help] [value]",
        argument_mode="options",
    )
