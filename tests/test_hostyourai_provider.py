"""Unit tests for hostyourai/__init__.py's provider-profile hooks (merged plugin).

Loads the merged plugin module fresh per test with minimal ``providers``/``providers.base``
fakes installed (zero real Hermes install needed), then drives the module through its own
``register(ctx)`` with a FakeCtx so the provider's ``_read_routing_state`` reads the same
in-process ``ctx`` the ``/hyai`` command writes to — no cross-plugin config.yaml path to
mock any more. Covers the full build_extra_body / build_api_kwargs_extras matrix plus the
fail-open contract around malformed state.
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

    def __init__(self, routing_state: dict | None = None):
        self._store: dict = {}
        if routing_state is not None:
            self._store["hyai_routing"] = routing_state
        self.handler = None

    def register_hook(self, name, callback):
        pass

    def register_middleware(self, kind, callback):
        pass

    def register_command(self, name, handler, description, args_hint="", argument_mode=None):
        self.handler = handler

    def get_config(self, key, default=None):
        return self._store.get(key, default)

    def set_config(self, key, value):
        self._store[key] = value


def _load_module(routing_state=None):
    """Load the plugin fresh, registered against a FakeCtx carrying ``routing_state``."""
    _install_fake_hermes_modules()
    mod_name = "hostyourai_under_test"
    sys.modules.pop(mod_name, None)
    spec = importlib.util.spec_from_file_location(mod_name, _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.register(FakeCtx(routing_state))
    return mod


class BuildExtraBodyTestCase(unittest.TestCase):
    def test_wrong_model_returns_empty(self):
        mod = _load_module({"task": "code"})
        profile = mod.HostYourAiProfile(name="x")
        self.assertEqual(profile.build_extra_body(model="some-other-model"), {})

    def test_no_state_returns_empty(self):
        mod = _load_module(None)
        profile = mod.HostYourAiProfile(name="x")
        self.assertEqual(profile.build_extra_body(model="hyai/auto"), {})

    def test_task_only(self):
        mod = _load_module({"task": "code"})
        profile = mod.HostYourAiProfile(name="x")
        self.assertEqual(profile.build_extra_body(model="hyai/auto"), {"hyai": {"task": "code"}})

    def test_policy_only(self):
        mod = _load_module({"policy": "speed"})
        profile = mod.HostYourAiProfile(name="x")
        self.assertEqual(profile.build_extra_body(model="hyai/auto"), {"hyai": {"policy": "speed"}})

    def test_task_and_policy(self):
        mod = _load_module({"task": "rag", "policy": "cost"})
        profile = mod.HostYourAiProfile(name="x")
        self.assertEqual(
            profile.build_extra_body(model="hyai/auto"), {"hyai": {"task": "rag", "policy": "cost"}}
        )

    def test_transport_headers_is_noop_here(self):
        mod = _load_module({"task": "code", "transport": "headers"})
        profile = mod.HostYourAiProfile(name="x")
        self.assertEqual(profile.build_extra_body(model="hyai/auto"), {})

    def test_model_missing_from_context_returns_empty(self):
        mod = _load_module({"task": "code"})
        profile = mod.HostYourAiProfile(name="x")
        self.assertEqual(profile.build_extra_body(), {})


class BuildApiKwargsExtrasTestCase(unittest.TestCase):
    def test_wrong_model_returns_empty_tuple(self):
        mod = _load_module({"task": "code", "transport": "headers"})
        profile = mod.HostYourAiProfile(name="x")
        self.assertEqual(profile.build_api_kwargs_extras(model="not-hyai"), ({}, {}))

    def test_transport_body_is_noop_here(self):
        mod = _load_module({"task": "code"})  # default transport = body
        profile = mod.HostYourAiProfile(name="x")
        self.assertEqual(profile.build_api_kwargs_extras(model="hyai/auto"), ({}, {}))

    def test_headers_task_only(self):
        mod = _load_module({"task": "code", "transport": "headers"})
        profile = mod.HostYourAiProfile(name="x")
        self.assertEqual(
            profile.build_api_kwargs_extras(model="hyai/auto"),
            ({}, {"extra_headers": {"X-HYAI-Task": "code"}}),
        )

    def test_headers_policy_only(self):
        mod = _load_module({"policy": "speed", "transport": "headers"})
        profile = mod.HostYourAiProfile(name="x")
        self.assertEqual(
            profile.build_api_kwargs_extras(model="hyai/auto"),
            ({}, {"extra_headers": {"X-HYAI-Policy": "speed"}}),
        )

    def test_headers_task_and_policy(self):
        mod = _load_module({"task": "legal", "policy": "quality", "transport": "headers"})
        profile = mod.HostYourAiProfile(name="x")
        self.assertEqual(
            profile.build_api_kwargs_extras(model="hyai/auto"),
            ({}, {"extra_headers": {"X-HYAI-Task": "legal", "X-HYAI-Policy": "quality"}}),
        )

    def test_headers_empty_state_returns_empty_tuple(self):
        mod = _load_module({"transport": "headers"})
        profile = mod.HostYourAiProfile(name="x")
        self.assertEqual(profile.build_api_kwargs_extras(model="hyai/auto"), ({}, {}))


class ReadRoutingStateFailOpenTestCase(unittest.TestCase):
    """Exercises _read_routing_state directly for malformed-config / error paths."""

    def test_state_value_not_a_dict_is_coerced_to_empty(self):
        mod = _load_module(None)
        mod._ctx._store["hyai_routing"] = "not-a-dict"
        self.assertEqual(mod._read_routing_state(), {})

    def test_ctx_get_config_raises_returns_empty_and_does_not_propagate(self):
        mod = _load_module({"task": "code"})

        def _raiser(key, default=None):
            raise RuntimeError("boom")

        mod._ctx.get_config = _raiser
        # Must not raise — this runs inside a request-path hook.
        self.assertEqual(mod._read_routing_state(), {})

    def test_no_ctx_registered_yet_returns_empty(self):
        mod = _load_module({"task": "code"})
        mod._ctx = None
        self.assertEqual(mod._read_routing_state(), {})

    def test_invalid_persisted_task_is_stripped(self):
        mod = _load_module({"task": "not-a-real-task", "policy": "speed"})
        state = mod._read_routing_state()
        self.assertNotIn("task", state)
        self.assertEqual(state.get("policy"), "speed")

    def test_invalid_persisted_policy_is_stripped(self):
        mod = _load_module({"task": "code", "policy": "not-a-real-policy"})
        state = mod._read_routing_state()
        self.assertEqual(state.get("task"), "code")
        self.assertNotIn("policy", state)

    def test_entry_missing_returns_empty(self):
        mod = _load_module(None)
        self.assertEqual(mod._read_routing_state(), {})

    def test_model_name_exact_match_required_no_whitespace_normalization(self):
        mod = _load_module({"task": "code"})
        profile = mod.HostYourAiProfile(name="x")
        self.assertEqual(profile.build_extra_body(model="hyai/auto "), {})
        self.assertEqual(profile.build_extra_body(model=" hyai/auto"), {})
        self.assertEqual(profile.build_extra_body(model="HYAI/AUTO"), {})


if __name__ == "__main__":
    unittest.main()
