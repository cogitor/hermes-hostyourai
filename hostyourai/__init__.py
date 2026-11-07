"""HostYourAI: provider profile for ``hyai/auto`` plus the ``/hyai`` routing-hint command.

One plugin, two roles that only make sense together: the ``hostyourai`` provider profile
(``hyai/auto`` router) and the ``/hyai`` slash command that sets a sticky task/policy hint
for it. Both read/write the same in-process plugin config (``CONFIG_KEY``) via the plugin
``ctx`` captured at ``register(ctx)`` time — no cross-plugin config-key coupling, because
there is now only one plugin.

The task/policy choice is saved in this plugin's config (``CONFIG_KEY``). The provider
profile reads it on every request and applies it via ``build_extra_body``/
``build_api_kwargs_extras``, which the transport
(``agent/transports/chat_completions.py::_build_kwargs_from_profile``) always calls for the
active provider. The setting is always accepted regardless of the chat's current model; it
has no effect unless that chat is actually on ``hyai/auto`` — the provider profile is the
real and only gate (checked at request time, not in ``/hyai``).

Wire shape:
  - transport "body" (default): ``extra_body["hyai"] = {"task": ..., "policy": ...}``
  - transport "headers": ``X-HYAI-Task`` / ``X-HYAI-Policy`` request headers

Only applied when the active model is exactly ``hyai/auto`` — a pinned concrete model has
no router to hint.

    /hyai                      show current task/policy
    /hyai status               same as above
    /hyai task <value>         set sticky task (see HYAI_TASKS), or `auto` to let a small model pick
    /hyai policy <value>       set sticky policy (see HYAI_POLICIES)
    /hyai code cost            shorthand: task and/or policy in any order
    /hyai off | clear          remove the override, let hyai/auto decide alone
    /hyai usage                balance and this month's spend (private chat, CLI, or dashboard only)

Experimental: the bundled ``hyai-decide`` skill is off by default. Enable it with
``hermes config set plugins.entries.hostyourai.settings.hyai_decide_enabled true``; it is
registered (or not) once, at plugin load time.
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from providers import register_provider
from providers.base import ProviderProfile

logger = logging.getLogger(__name__)

HYAI_TASKS = (
    "chat",
    "code",
    "summarise",
    "classify",
    "extract",
    "rag",
    "legal",
    "reason",
)
HYAI_POLICIES = ("balanced", "speed", "cost", "quality")
HYAI_DEFAULT_MODEL = "hyai/auto"

CONFIG_KEY = "hyai_routing"  # shared by the /hyai command and the provider profile below
_USAGE = (
    "Usage: /hyai [status|task <value>|policy <value>|off|clear|usage]\n"
    f"  task:   {'|'.join(HYAI_TASKS)}|auto\n"
    f"  policy: {'|'.join(HYAI_POLICIES)}\n"
    "  shorthand: /hyai code cost (a task and/or a policy, in any order)"
)

API_BASE = "https://hostyourai.com/api/v1"  # the only host the API key is ever sent to
AUTO_BASE = "https://hostyourai.com/api/decision"  # same host: the System One API used by auto
AUTO_TASK = "auto"
AUTO_KEY = "hyai_auto"  # this plugin's settings for auto: model, threshold, timeout_ms, min_chars
_AUTO_CHARS = 200  # how much of each message is sent
_AUTO_MAX_ANSWER = 65_536
_COMPACTION_PREFIX = "[CONTEXT "  # how Hermes starts the summary it puts in place of old turns
_BALANCE_TTL = 60.0
_HTTP_TIMEOUT = 5.0
_LOCAL_SOURCES = ("cli", "tui", "desktop", "local")
_DASHBOARD_PLATFORM = "api_server"
_PRIVATE_ONLY = (
    "Balance and spend are only shown in a private chat, the local CLI, or the Hermes dashboard. "
    "Nothing was fetched."
)
_HTTP_ERRORS = {401: "key rejected by HostYourAI", 403: "this endpoint needs a personal key, not a team key"}


def _format_status(state: dict) -> str:
    if not state.get("task") and not state.get("policy"):
        return "HostYourAI routing: not set (hyai/auto decides per request)"
    text = (
        f"HostYourAI routing: task={state.get('task') or '(unset)'} policy={state.get('policy') or '(unset)'}"
        " (applies to every chat on this Hermes profile; only hyai/auto requests use it)"
    )
    if state.get("task") == AUTO_TASK and _last_auto:
        task, fallback = _last_auto
        text += f"\nAuto: last message got {task}" + (" (fallback)" if fallback else "")
    return text


def _private_chat() -> bool:
    """True for a DM, local interactive surface, or the dashboard; refuse all other contexts."""
    try:
        from gateway.session_context import get_session_env, session_context_engaged

        chat_type = (get_session_env("HERMES_SESSION_CHAT_TYPE") or "").strip().lower()
        if chat_type:
            return chat_type == "dm"
        platform = (get_session_env("HERMES_SESSION_PLATFORM") or "").strip().lower()
        if platform:
            return platform == _DASHBOARD_PLATFORM
        source = (get_session_env("HERMES_SESSION_SOURCE") or "").strip().lower()
        if source:
            # Local surfaces bind a source (cli/tui/desktop/local) — that is proof of a
            # non-messaging surface even when a session key is also bound (TUI in the
            # dashboard binds both). Check it before the key guard so the dashboard and
            # embedded TUI are not treated as an unknown messaging session.
            return source in _LOCAL_SOURCES
        if get_session_env("HERMES_SESSION_KEY"):
            return False  # messaging session of unknown type
        # A plain CLI binds no gateway session. Do not infer otherwise from imported modules:
        # the CLI may import gateway helpers while starting plugins.
        return not session_context_engaged()
    except Exception as exc:
        logger.debug("hyai: chat type undeterminable, refusing usage: %r", exc)
        return False


class _ApiError(Exception):
    """The service failed or answered badly. The message never contains the key or a prompt."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a redirect would forward the key to another host


_OPENER = urllib.request.build_opener(_NoRedirect)


def _api_key() -> str:
    return os.environ.get("HOSTYOURAI_API_KEY", "").strip()


def _http_get(path: str) -> tuple[int, str, str]:
    """GET API_BASE+path with the key. Returns (status, retry-after header, body)."""
    key = _api_key()
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
    key = _api_key()
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


_AUTO_DEFAULTS = {"model": "hyai/decider-2b", "threshold": 0.8, "timeout_ms": 1000, "min_chars": 20}
_AUTO_CRITERIA = {
    "chat": "Casual conversation, advice, brainstorming, opinions, or general questions.",
    "code": "Writing, fixing, explaining or reviewing source code, scripts, or shell commands.",
    "summarise": "Condensing a given text, article, thread or meeting into a shorter version.",
    "classify": "Assigning a given item to a category or label, or sorting items into groups.",
    "extract": "Pulling specific fields or facts out of a given text into structured data.",
    "rag": "Answering a question from provided documents or notes, or searching a knowledge base.",
    "legal": "Contracts, regulations, compliance, terms of service, or legal interpretation.",
    "reason": "Multi-step maths, logic puzzles, planning or analysis that needs careful step-by-step thinking.",
}
_AUTO_QUESTION = (
    "`prompt` is the start of the latest message a user sent to an AI assistant. "
    "`previous_user` (if present) is the user message before it. Which kind of task "
    "is the user asking the assistant to do in `prompt`? Use the earlier message only to "
    "resolve vague replies such as 'ok', 'do that' or 'and the other one'."
)

_PAUSE_SECONDS = 60  # after a service failure auto sends nothing for this long, plus up to a quarter
_backoff_until = 0.0  # time.monotonic() value when auto may try again
_MAX_TURNS = 64  # results kept for the model calls of recent messages

_last_auto: tuple[str, bool] | None = None  # (task, was a fallback) of the latest message
_turns: dict[str, str] = {}  # turn id -> task picked for that message, oldest first
_auto_lock = threading.Lock()  # guards _turns, _last_auto and _backoff_until


class _Unusable(Exception):
    """Not a service failure (no key, or low confidence): the answer just cannot be used."""


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _is_whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _saved_state(ctx) -> dict:
    value = ctx.get_config(CONFIG_KEY, {})
    return value if isinstance(value, dict) else {}


def _auto_settings(ctx) -> dict:
    """The auto settings, with the default for anything missing or not valid."""
    saved = ctx.get_config(AUTO_KEY, {})
    saved = saved if isinstance(saved, dict) else {}
    out = dict(_AUTO_DEFAULTS)
    if isinstance(saved.get("model"), str) and saved["model"].strip():
        out["model"] = saved["model"].strip()
    if _is_number(saved.get("threshold")) and 0 <= saved["threshold"] <= 1:
        out["threshold"] = float(saved["threshold"])
    if _is_whole(saved.get("timeout_ms")) and 0 < saved["timeout_ms"] <= 5000:
        out["timeout_ms"] = saved["timeout_ms"]
    if _is_whole(saved.get("min_chars")) and saved["min_chars"] >= 0:
        out["min_chars"] = saved["min_chars"]
    return out


def _cut(text: str) -> str:
    return text.strip()[:_AUTO_CHARS]


def _texts(user_message, history) -> tuple[str, str]:
    """(prompt, previous): the start of this user message and of the user message before it.
    The history is expected to end with the current message, so that one is dropped."""
    # Hermes puts its own notes into the history as user messages: the compaction summary and
    # display_kind rows. They are not what the user wrote.
    users = [
        m
        for m in history or []
        if isinstance(m, dict)
        and m.get("role") == "user"
        and isinstance(m.get("content"), str)
        and not m.get("display_kind")
        and not m["content"].startswith(_COMPACTION_PREFIX)
    ]
    if users:
        users.pop()
    previous = users[-1]["content"] if users else ""
    prompt = user_message if isinstance(user_message, str) else ""
    return _cut(prompt), _cut(previous)


def _classify(prompt: str, previous: str, settings: dict) -> str:
    """Ask System One which task `prompt` is. Returns a task, or raises when it cannot be used."""
    key = _api_key()
    if not key:
        raise _Unusable("no HOSTYOURAI_API_KEY set")
    state = {"prompt": prompt, **({"previous_user": previous} if previous else {})}
    body = {
        "model": settings["model"],
        "state": state,
        "questions": {"task": {"type": "choice", "instructions": _AUTO_QUESTION, "criteria": _AUTO_CRITERIA}},
    }
    req = urllib.request.Request(
        AUTO_BASE + "/v1/systemone",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with _OPENER.open(req, timeout=settings["timeout_ms"] / 1000) as resp:
        raw = resp.read(_AUTO_MAX_ANSWER + 1)
    if len(raw) > _AUTO_MAX_ANSWER:
        raise _ApiError("answer too large")
    answer = json.loads(raw)["answers"]["task"]
    choice, confidence = answer["choice"], answer["confidence"]
    if choice not in HYAI_TASKS or not _is_number(confidence):
        raise _ApiError("bad answer")
    if confidence < settings["threshold"]:
        raise _Unusable("low confidence")
    return choice


def _classify_in_time(prompt: str, previous: str, settings: dict) -> str:
    """_classify, but never later than the budget. The socket timeout does not cover DNS or a slow
    trickle, so the call runs in a thread that is given up on when the time is over."""
    result: dict = {}

    def work():
        try:
            result["task"] = _classify(prompt, previous, settings)
        except Exception as exc:
            result["error"] = exc

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(settings["timeout_ms"] / 1000)
    if worker.is_alive():
        raise TimeoutError
    if "error" in result:
        raise result["error"]
    return result["task"]


def _is_timeout(exc: Exception) -> bool:
    return isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError)


def _pause() -> None:
    global _backoff_until
    pause = _PAUSE_SECONDS * (1 + 0.25 * random.random())
    with _auto_lock:
        _backoff_until = max(_backoff_until, time.monotonic() + pause)


def _pick_task(user_message, history, settings: dict) -> tuple[str, bool]:
    """(task, fallback) for one user message; `chat` whenever auto cannot give a good answer."""
    prompt, previous = _texts(user_message, history)
    if not prompt or len(prompt) + len(previous) < settings["min_chars"]:
        return "chat", True
    with _auto_lock:
        paused = time.monotonic() < _backoff_until
    if paused:
        return "chat", True
    try:
        return _classify_in_time(prompt, previous, settings), False
    except _Unusable as exc:
        logger.debug("hyai: auto fell back to chat: %s", exc)
    except Exception as exc:  # log the type only: its text could hold the prompt or the key
        logger.debug("hyai: auto fell back to chat: %s", type(exc).__name__)
        if not _is_timeout(exc):  # a slow answer is not an error: the next message tries again
            _pause()
    return "chat", True


def _on_user_message(
    ctx,
    *,
    turn_id="",
    user_message=None,
    conversation_history=None,
    model="",
    platform="",
    parent_session_id="",
    **_ignored,
) -> None:
    """pre_llm_call: runs once per user message, before the first model call."""
    global _last_auto
    if _saved_state(ctx).get("task") != AUTO_TASK or not turn_id:
        return
    if model != "hyai/auto" or platform in ("cron", "subagent") or parent_session_id:
        return
    task, fallback = _pick_task(user_message, conversation_history, _auto_settings(ctx))
    with _auto_lock:
        _turns[turn_id] = task
        while len(_turns) > _MAX_TURNS:
            del _turns[next(iter(_turns))]
        _last_auto = (task, fallback)


def _on_llm_request(ctx, *, request=None, turn_id="", model="", **_ignored):
    """llm_request middleware: puts the task picked for this message into the request."""
    with _auto_lock:
        task = _turns.get(turn_id)
    if not task or model != "hyai/auto" or not isinstance(request, dict):
        return None
    request = dict(request)
    if _saved_state(ctx).get("transport") == "headers":
        request["extra_headers"] = {**(request.get("extra_headers") or {}), "X-HYAI-Task": task}
    else:
        extra = dict(request.get("extra_body") or {})
        extra["hyai"] = {**(extra.get("hyai") or {}), "task": task}
        request["extra_body"] = extra
    return {"request": request}


# --- provider profile ------------------------------------------------------

# Set by register(ctx) at plugin load. The provider-profile hooks below run on every real
# request (not via register(ctx)'s own call chain), so they reach the same plugin config
# through this module-level handle rather than importing/parsing config.yaml directly — now
# that the command and the provider are the same plugin, there is no cross-plugin coupling
# left to bridge.
_ctx: Any = None


def _read_routing_state() -> dict[str, Any]:
    """Read the sticky {task, policy, transport} dict the /hyai command persisted via ``ctx``.

    Fails open (empty dict) on ANY error — a provider-profile hook runs on every real
    request, so nothing here may ever raise into that hot path.
    """
    try:
        return _read_routing_state_unsafe()
    except Exception as exc:
        logger.warning(
            "hostyourai: routing-state read failed (%s: %s) — no routing hints applied.",
            type(exc).__name__,
            exc,
        )
        return {}


def _read_routing_state_unsafe() -> dict[str, Any]:
    """Unguarded body of :func:`_read_routing_state` — never call directly outside a test."""
    if _ctx is None:
        return {}
    state = dict(_saved_state(_ctx))
    # Defense-in-depth: re-validate against the enum even though the /hyai command already
    # only ever persists valid values — a hand-edited config.yaml, or header-injection via
    # control characters in task/policy, must never reach the wire unchecked.
    task, policy = state.get("task"), state.get("policy")
    if task == "auto":  # set by /hyai: the auto hook adds the real task per message itself
        state.pop("task")
    elif task is not None and task not in HYAI_TASKS:
        logger.warning("hostyourai: ignoring invalid persisted task %r (not in %s)", task, HYAI_TASKS)
        state.pop("task", None)
    if policy is not None and policy not in HYAI_POLICIES:
        logger.warning("hostyourai: ignoring invalid persisted policy %r (not in %s)", policy, HYAI_POLICIES)
        state.pop("policy", None)
    return state


class HostYourAiProfile(ProviderProfile):
    """HostYourAI — OpenAI-compatible router with task/policy hints on hyai/auto."""

    def build_extra_body(self, *, session_id: str | None = None, **context: Any) -> dict[str, Any]:
        if (context.get("model") or "") != HYAI_DEFAULT_MODEL:
            return {}
        state = _read_routing_state()
        if state.get("transport") == "headers":
            return {}  # headers path is emitted from build_api_kwargs_extras instead
        task, policy = state.get("task"), state.get("policy")
        if not task and not policy:
            return {}
        hyai: dict[str, str] = {}
        if task:
            hyai["task"] = task
        if policy:
            hyai["policy"] = policy
        return {"hyai": hyai}

    def build_api_kwargs_extras(
        self, *, reasoning_config: dict | None = None, **context: Any
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if (context.get("model") or "") != HYAI_DEFAULT_MODEL:
            return {}, {}
        state = _read_routing_state()
        if state.get("transport") != "headers":
            return {}, {}
        headers: dict[str, str] = {}
        if state.get("task"):
            headers["X-HYAI-Task"] = str(state["task"])
        if state.get("policy"):
            headers["X-HYAI-Policy"] = str(state["policy"])
        if not headers:
            return {}, {}
        return {}, {"extra_headers": headers}


hostyourai = HostYourAiProfile(
    name="hostyourai",
    aliases=("hyai",),
    display_name="HostYourAI",
    description="HostYourAI — self-hosted inference router (hyai/auto task/policy routing)",
    signup_url="https://hostyourai.com/app/docs",
    env_vars=("HOSTYOURAI_API_KEY",),
    base_url="https://hostyourai.com/api/v1",
    default_aux_model=HYAI_DEFAULT_MODEL,
    fallback_models=(HYAI_DEFAULT_MODEL,),
)

register_provider(hostyourai)


# --- /hyai command -----------------------------------------------------------


def register(ctx) -> None:
    global _ctx
    _ctx = ctx

    if hasattr(ctx, "register_skill") and ctx.get_config("hyai_decide_enabled", False):
        skills_dir = Path(__file__).parent / "skills"
        for child in sorted(skills_dir.iterdir()) if skills_dir.is_dir() else ():
            skill_md = child / "SKILL.md"
            if child.is_dir() and skill_md.exists():
                ctx.register_skill(child.name, skill_md)

    def _handle_hyai(raw_args: str):
        parts = (raw_args or "").split()
        first = parts[0].lower() if parts else ""
        # usage is about the account, not routing, so it has its own private-chat gate.
        if first == "usage":
            return _report_usage(ctx)
        if first == "help":
            return _USAGE
        state = dict(_saved_state(ctx))

        # /hyai no longer checks the chat's active model before reading or writing the
        # setting (it used to read it out of Hermes's private state.db). Nothing here is
        # gated on the model any more: the setting is always accepted, and takes effect
        # only when the chat is actually on hyai/auto — that check lives solely in the
        # provider profile above, at request time.
        if not parts or first == "status":
            status = _format_status(state)
            # No picker exists on text surfaces (Telegram), so bare /hyai also lists the choices.
            return status if parts else f"{status}\n\n{_USAGE}"

        if first in ("off", "clear"):
            # Keep any hand-set provider option (e.g. transport); only clear task/policy.
            ctx.set_config(CONFIG_KEY, {"transport": state["transport"]} if state.get("transport") else {})
            return "HostYourAI routing cleared — hyai/auto decides per request."

        if first == "task":
            if len(parts) < 2 or parts[1].lower() not in (*HYAI_TASKS, AUTO_TASK):
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

    if hasattr(ctx, "register_middleware"):  # older Hermes: /hyai still works, auto does not
        ctx.register_hook("pre_llm_call", lambda **kw: _on_user_message(ctx, **kw))
        ctx.register_middleware("llm_request", lambda **kw: _on_llm_request(ctx, **kw))
    ctx.register_command(
        "hyai",
        handler=_handle_hyai,
        description="HostYourAI: routing hints and balance",
        # must not start with "<": Hermes hides such commands from the Telegram menu
        args_hint="[status|task|policy|usage|off|help] [value]",
        argument_mode="options",
    )
