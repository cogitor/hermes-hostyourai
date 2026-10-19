"""End-to-end user-journey tests for the hyai-router + hostyourai pair.

Unlike test_hyai_router.py (handler-level unit tests) and
test_hostyourai_provider.py (provider-hook unit tests), these exercise a
realistic sequence of ``/hyai`` calls through a SHARED config store and then
assert what the provider profile would actually put on the wire — i.e. the
same round trip a real user triggers: type a slash command, then send a
message to hyai/auto.

Both plugin modules are loaded against one `SharedConfigStore` so a `/hyai`
call and a subsequent `build_extra_body`/`build_api_kwargs_extras` call see
the same persisted state, the way they do in a real Hermes process (both
read/write ``plugins.entries.hyai-router.settings.hyai_routing``).
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

_ROUTER_PATH = Path(__file__).resolve().parent.parent / "hyai-router" / "__init__.py"
_PROVIDER_PATH = Path(__file__).resolve().parent.parent / "model-providers" / "hostyourai" / "__init__.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _install_fake_hermes_modules(get_routing_state):
    """Same fakes as test_hostyourai_provider.py, but reads live from ``get_routing_state``
    (a zero-arg callable) on every call — so a journey test's /hyai calls are
    immediately visible to the provider profile, like a real shared config.yaml."""

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

    hermes_cli_pkg = types.ModuleType("hermes_cli")
    sys.modules["hermes_cli"] = hermes_cli_pkg

    plugins_state_mod = types.ModuleType("hermes_cli.plugins_state")

    def _plugin_settings_entry(_config, _plugin_id):
        state = get_routing_state()
        return {"settings": {"hyai_routing": state}} if state is not None else {}

    plugins_state_mod._plugin_settings_entry = _plugin_settings_entry
    sys.modules["hermes_cli.plugins_state"] = plugins_state_mod

    config_mod = types.ModuleType("hermes_cli.config")
    config_mod.load_config_readonly = lambda: {}
    sys.modules["hermes_cli.config"] = config_mod


def _load_provider(get_routing_state):
    _install_fake_hermes_modules(get_routing_state)
    mod_name = "hostyourai_journey_under_test"
    sys.modules.pop(mod_name, None)
    spec = importlib.util.spec_from_file_location(mod_name, _PROVIDER_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SharedConfigStore:
    """Stands in for config.yaml's plugin-settings namespace, shared across both plugins."""

    def __init__(self):
        self._routing: dict = {}

    # --- hyai-router's ctx surface -----------------------------------------
    def get_config(self, key, default=None):
        assert key == "hyai_routing"
        return dict(self._routing) if self._routing else default

    def set_config(self, key, value):
        assert key == "hyai_routing"
        self._routing = dict(value) if value else {}

    def register_command(self, name, handler, description, args_hint="", argument_mode=None):
        self.handler = handler


class JourneyTestCase(unittest.TestCase):
    """Each test drives /hyai like a user would, then checks the wire request."""

    def setUp(self):
        self.router_mod = _load(_ROUTER_PATH, "hyai_router_journey")
        self.router_mod._active_model = lambda: ("hostyourai", "hyai/auto")
        self.store = SharedConfigStore()
        self.router_mod.register(self.store)
        self.hyai = self.store.handler  # the /hyai handler, call like self.hyai("task code")

        # Load the provider profile wired to read live from the SAME store the
        # /hyai handler writes to — the real cross-plugin contract (shared
        # config.yaml key), not an in-process object reference.
        self.provider_mod = _load_provider(lambda: self.store.get_config("hyai_routing", None))
        self.profile = self.provider_mod.hostyourai

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
        state = self.store.get_config("hyai_routing", {})
        state["transport"] = "headers"
        self.store.set_config("hyai_routing", state)

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
        state = self.store.get_config("hyai_routing", {})
        state["transport"] = "headers"
        self.store.set_config("hyai_routing", state)

        self.hyai("off")
        self.assertEqual(self.store.get_config("hyai_routing", {}), {"transport": "headers"})
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
