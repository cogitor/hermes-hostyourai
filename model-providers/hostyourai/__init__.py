"""HostYourAI provider profile.

Registers HostYourAI (https://hostyourai.com) as a named Hermes provider so it
shows up in the setup/model picker instead of requiring the generic
custom-endpoint flow. Default model is ``hyai/auto`` — HostYourAI's own router
that picks an underlying model per request.

Task/policy routing hints are set via the companion ``hyai-router`` plugin's
``/hyai`` slash command, which persists them to
``plugins.entries.hyai-router.settings.hyai_routing`` in config.yaml (plugin
config is the only cross-process-safe channel a general plugin has — there is
no public ``agent.request_overrides`` handle from a slash-command handler).
This profile reads that same config key directly (no cross-plugin coupling
beyond the key name) and applies it on every real request via
``build_extra_body``/``build_api_kwargs_extras``, which the transport
(``agent/transports/chat_completions.py::_build_kwargs_from_profile``) always
calls for the active provider.

Wire shape (mirrors the HostYourAI n8n community node,
``HostYourAiChatModel/model.ts::buildRequest``, so a Hermes session and an n8n
workflow route identically):
  - transport "body" (default): ``extra_body["hyai"] = {"task": ..., "policy": ...}``
  - transport "headers": ``X-HYAI-Task`` / ``X-HYAI-Policy`` request headers

Only applied when the active model is exactly ``hyai/auto`` — a pinned
concrete model has no router to hint.
"""

from __future__ import annotations

import logging
from typing import Any

from providers import register_provider
from providers.base import ProviderProfile

logger = logging.getLogger(__name__)

# Source of truth: HostYourAiChatModel.node.ts (n8n community node). Keep these
# two lists in sync manually if HostYourAI adds/renames task or policy values.
HYAI_TASKS = ("chat", "code", "summarise", "classify", "extract", "rag", "legal", "reason")
HYAI_POLICIES = ("balanced", "speed", "cost", "quality")
HYAI_DEFAULT_MODEL = "hyai/auto"

# Must match hyai-router/__init__.py's CONFIG_KEY and plugin id (directory name).
_ROUTER_PLUGIN_ID = "hyai-router"
_CONFIG_KEY = "hyai_routing"


def _read_routing_state() -> dict[str, Any]:
    """Read the sticky {task, policy, transport} dict the /hyai command persisted.

    Reads config directly rather than importing the sibling plugin — the two
    plugins are coupled only by this key name, not by Python imports, so either
    can be installed/updated independently. Fails open (empty dict) on ANY
    error in this function — a provider-profile hook runs on every real
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
    from hermes_cli.config import load_config_readonly
    from hermes_cli.plugins_state import _plugin_settings_entry

    entry = _plugin_settings_entry(load_config_readonly() or {}, _ROUTER_PLUGIN_ID)
    if not isinstance(entry, dict):
        return {}
    settings = entry.get("settings")
    if not isinstance(settings, dict):
        return {}
    value = settings.get(_CONFIG_KEY)
    state = dict(value) if isinstance(value, dict) else {}
    # Defense-in-depth: re-validate against the enum even though the /hyai command already
    # only ever persists valid values — a hand-edited config.yaml, or header-injection via
    # control characters in task/policy, must never reach the wire unchecked.
    task, policy = state.get("task"), state.get("policy")
    if task is not None and task not in HYAI_TASKS:
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
