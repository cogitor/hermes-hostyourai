"""End-to-end user-journey tests for the merged hostyourai plugin.

Unlike test_hyai_router.py (handler-level unit tests) and
test_hostyourai_provider.py (provider-hook unit tests), these exercise a
realistic sequence of ``/hyai`` calls through the plugin's own ``ctx``
and then assert what the provider profile would actually put on the
wire — i.e. the same round trip a real user triggers: type a slash
command, then send a message to hyai/auto. Both roles are the same
plugin and share the same ``ctx`` captured at ``register(ctx)`` time,
so no separate shared-store rig is needed any more.
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parent.parent / "hostyourai" / "__init__.py"


def _install_fake_hermes_modules():
    providers_pkg = types.ModuleType("providers")
    providers_pkg.register_provider = lambda profile: None
    sys.modules["providers"] = providers_pkg

    providers_base = types.ModuleType("providers.base")

    class _FakeProviderProfile:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    providers_base.ProviderProfile = _FakeProviderProfile
    sys.modules["providers.base"] = providers_base


class FakeCtx:
    """Minimal in-memory stand-in for the real plugin ``ctx``."""

    def __init__(self):
        self._store: dict = {}
        self.handler = None

    def get_config(self, key, default=None):
        return self._store.get(key, default)

    def set_config(self, key, value):
        self._store[key] = value

    def register_hook(self, name, callback):
        pass

    def register_middleware(self, kind, callback):
        pass

    def register_command(self, name, handler, description, args_hint="", argument_mode=None):
        self.handler = handler


def _load_module():
    _install_fake_hermes_modules()
    mod_name = "hostyourai_journey_under_test"
    sys.modules.pop(mod_name, None)
    spec = importlib.util.spec_from_file_location(mod_name, _MODULE_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class JourneyTestCase(unittest.TestCase):
    """Each test drives /hyai like a user would, then checks the wire request."""

    def setUp(self):
        self.mod = _load_module()
        self.ctx = FakeCtx()
        self.mod.register(self.ctx)
        self.hyai = self.ctx.handler  # the /hyai handler, call like self.hyai("task code")
        self.profile = self.mod.hostyourai

    def _extra_body(self, model="hyai/auto"):
        return self.profile.build_extra_body(model=model)

    def _api_extras(self, model="hyai/auto"):
        return self.profile.build_api_kwargs_extras(model=model)

    # --- journey 1: fresh user, never touched /hyai -------------------------
    def test_fresh_install_no_hints_on_wire(self):
        self.assertIn("not set", self.hyai(""))
        self.assertEqual(self._extra_body(), {})
        self.assertEqual(self._api_extras(), ({}, {}))

    # --- journey 2: set task, send message, change policy, send again ------
    def test_set_task_then_policy_both_land_on_wire_progressively(self):
        self.hyai("task code")
        self.assertEqual(self._extra_body(), {"hyai": {"task": "code"}})

        self.hyai("policy speed")
        self.assertEqual(self._extra_body(), {"hyai": {"task": "code", "policy": "speed"}})

    # --- journey 3: switch transport via config (not /hyai), hints move to headers --
    def test_config_only_transport_switch_moves_hints_to_headers(self):
        self.hyai("task rag")
        self.hyai("policy quality")
        self.assertEqual(self._extra_body(), {"hyai": {"task": "rag", "policy": "quality"}})
        self.assertEqual(self._api_extras(), ({}, {}))

        # Simulate the one-off config.yaml edit a user does directly (not via /hyai —
        # transport has no /hyai subcommand per the new UX).
        state = self.ctx.get_config(self.mod.CONFIG_KEY, {})
        state["transport"] = "headers"
        self.ctx.set_config(self.mod.CONFIG_KEY, state)

        self.assertEqual(self._extra_body(), {})  # body path goes quiet
        self.assertEqual(
            self._api_extras(),
            ({}, {"extra_headers": {"X-HYAI-Task": "rag", "X-HYAI-Policy": "quality"}}),
        )

    # --- journey 4: user runs /hyai off mid-session, next message is clean --
    def test_off_mid_session_next_request_has_no_hints(self):
        self.hyai("task code")
        self.hyai("policy speed")
        self.assertNotEqual(self._extra_body(), {})

        self.hyai("off")
        self.assertEqual(self._extra_body(), {})
        self.assertEqual(self._api_extras(), ({}, {}))
        self.assertIn("not set", self.hyai("status"))

    # --- journey 5: /hyai off preserves a config-only transport setting -----
    def test_off_does_not_clobber_config_only_transport(self):
        self.hyai("task code")
        state = self.ctx.get_config(self.mod.CONFIG_KEY, {})
        state["transport"] = "headers"
        self.ctx.set_config(self.mod.CONFIG_KEY, state)

        self.hyai("off")
        self.assertEqual(self.ctx.get_config(self.mod.CONFIG_KEY, {}), {"transport": "headers"})
        # And it stays inert until task/policy are set again.
        self.assertEqual(self._api_extras(), ({}, {}))

    # --- journey 6: user switches away from hyai/auto mid-session ----------
    def test_switching_model_away_from_hyai_auto_suppresses_hints_immediately(self):
        self.hyai("task code")
        self.hyai("policy speed")
        self.assertNotEqual(self._extra_body(model="hyai/auto"), {})

        # Same persisted state, different active model — must stay silent,
        # including a plausible but wrong near-miss name.
        for other in ("hyai/fast", "openai/gpt-4o", ""):
            self.assertEqual(self._extra_body(model=other), {})
            self.assertEqual(self._api_extras(model=other), ({}, {}))

        # Switching back to hyai/auto resumes hints without re-running /hyai.
        self.assertNotEqual(self._extra_body(model="hyai/auto"), {})

    # --- journey 7: typo recovery — invalid value doesn't corrupt existing state --
    def test_typo_in_followup_command_does_not_corrupt_existing_hint(self):
        self.hyai("task code")
        out = self.hyai("policy fastt")  # typo: not a real policy
        self.assertIn("Unknown policy", out)
        # Existing task hint must still be live on the wire.
        self.assertEqual(self._extra_body(), {"hyai": {"task": "code"}})

    # --- journey 8: clear is a full alias for off within the same session ---
    def test_clear_alias_journey(self):
        self.hyai("policy cost")
        self.hyai("clear")
        self.assertEqual(self._extra_body(), {})


if __name__ == "__main__":
    unittest.main()
