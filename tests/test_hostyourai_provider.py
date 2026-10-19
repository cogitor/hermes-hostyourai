"""Unit tests for model-providers/hostyourai/__init__.py's provider-profile hooks.

Mocks ``hermes_cli.config`` (the module the
plugin under test imports lazily inside ``_read_routing_state_unsafe``) so this
runs with zero real Hermes install. Covers the full build_extra_body /
build_api_kwargs_extras matrix plus the fail-open contract around malformed
config shapes.
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

_MODULE_PATH = Path(__file__).resolve().parent.parent / "model-providers" / "hostyourai" / "__init__.py"


def _install_fake_hermes_modules(settings_entry):
    """Install minimal ``providers``/``providers.base``/``hermes_cli.*`` fakes into
    sys.modules so the plugin module can import without a real Hermes checkout."""

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

    config_mod = types.ModuleType("hermes_cli.config")
    config_mod.load_config_readonly = lambda: {"plugins": {"entries": {"hyai-router": settings_entry()}}}
    sys.modules["hermes_cli.config"] = config_mod


def _load_module(settings_entry):
    """Load the plugin fresh with the given fake settings entry
    (a zero-arg callable so each test can vary/raise)."""
    _install_fake_hermes_modules(settings_entry)
    mod_name = "hostyourai_under_test"
    sys.modules.pop(mod_name, None)
    spec = importlib.util.spec_from_file_location(mod_name, _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _entry(state: dict | None):
    return {"settings": {"hyai_routing": state}} if state is not None else {}


class BuildExtraBodyTestCase(unittest.TestCase):
    def test_wrong_model_returns_empty(self):
        mod = _load_module(lambda: _entry({"task": "code"}))
        profile = mod.HostYourAiProfile(name="x")
        self.assertEqual(profile.build_extra_body(model="some-other-model"), {})

    def test_no_state_returns_empty(self):
        mod = _load_module(lambda: _entry(None))
        profile = mod.HostYourAiProfile(name="x")
        self.assertEqual(profile.build_extra_body(model="hyai/auto"), {})

    def test_task_only(self):
        mod = _load_module(lambda: _entry({"task": "code"}))
        profile = mod.HostYourAiProfile(name="x")
        self.assertEqual(profile.build_extra_body(model="hyai/auto"), {"hyai": {"task": "code"}})

    def test_policy_only(self):
        mod = _load_module(lambda: _entry({"policy": "speed"}))
        profile = mod.HostYourAiProfile(name="x")
        self.assertEqual(profile.build_extra_body(model="hyai/auto"), {"hyai": {"policy": "speed"}})

    def test_task_and_policy(self):
        mod = _load_module(lambda: _entry({"task": "rag", "policy": "cost"}))
        profile = mod.HostYourAiProfile(name="x")
        self.assertEqual(
            profile.build_extra_body(model="hyai/auto"), {"hyai": {"task": "rag", "policy": "cost"}}
        )

    def test_transport_headers_is_noop_here(self):
        mod = _load_module(lambda: _entry({"task": "code", "transport": "headers"}))
        profile = mod.HostYourAiProfile(name="x")
        self.assertEqual(profile.build_extra_body(model="hyai/auto"), {})

    def test_model_missing_from_context_returns_empty(self):
        mod = _load_module(lambda: _entry({"task": "code"}))
        profile = mod.HostYourAiProfile(name="x")
        self.assertEqual(profile.build_extra_body(), {})


class BuildApiKwargsExtrasTestCase(unittest.TestCase):
    def test_wrong_model_returns_empty_tuple(self):
        mod = _load_module(lambda: _entry({"task": "code", "transport": "headers"}))
        profile = mod.HostYourAiProfile(name="x")
        self.assertEqual(profile.build_api_kwargs_extras(model="not-hyai"), ({}, {}))

    def test_transport_body_is_noop_here(self):
        mod = _load_module(lambda: _entry({"task": "code"}))  # default transport = body
        profile = mod.HostYourAiProfile(name="x")
        self.assertEqual(profile.build_api_kwargs_extras(model="hyai/auto"), ({}, {}))

    def test_headers_task_only(self):
        mod = _load_module(lambda: _entry({"task": "code", "transport": "headers"}))
        profile = mod.HostYourAiProfile(name="x")
        self.assertEqual(
            profile.build_api_kwargs_extras(model="hyai/auto"),
            ({}, {"extra_headers": {"X-HYAI-Task": "code"}}),
        )

    def test_headers_policy_only(self):
        mod = _load_module(lambda: _entry({"policy": "speed", "transport": "headers"}))
        profile = mod.HostYourAiProfile(name="x")
        self.assertEqual(
            profile.build_api_kwargs_extras(model="hyai/auto"),
            ({}, {"extra_headers": {"X-HYAI-Policy": "speed"}}),
        )

    def test_headers_task_and_policy(self):
        mod = _load_module(lambda: _entry({"task": "legal", "policy": "quality", "transport": "headers"}))
        profile = mod.HostYourAiProfile(name="x")
        self.assertEqual(
            profile.build_api_kwargs_extras(model="hyai/auto"),
            ({}, {"extra_headers": {"X-HYAI-Task": "legal", "X-HYAI-Policy": "quality"}}),
        )

    def test_headers_empty_state_returns_empty_tuple(self):
        mod = _load_module(lambda: _entry({"transport": "headers"}))
        profile = mod.HostYourAiProfile(name="x")
        self.assertEqual(profile.build_api_kwargs_extras(model="hyai/auto"), ({}, {}))


class ReadRoutingStateFailOpenTestCase(unittest.TestCase):
    """Exercises _read_routing_state directly for malformed-config / error paths —
    the exact hot-path gap (E2) the second code review flagged."""

    def test_settings_entry_not_a_dict_returns_empty(self):
        mod = _load_module(lambda: "not-a-dict")
        self.assertEqual(mod._read_routing_state(), {})

    def test_settings_value_not_a_dict_returns_empty(self):
        mod = _load_module(lambda: {"settings": "not-a-dict"})
        self.assertEqual(mod._read_routing_state(), {})

    def test_hyai_routing_value_not_a_dict_returns_empty(self):
        mod = _load_module(lambda: {"settings": {"hyai_routing": "not-a-dict"}})
        self.assertEqual(mod._read_routing_state(), {})

    def test_config_read_raises_returns_empty_and_does_not_propagate(self):
        def _raiser():
            raise RuntimeError("boom")

        mod = _load_module(_raiser)
        # Must not raise — this runs inside a request-path hook.
        self.assertEqual(mod._read_routing_state(), {})

    def test_import_error_on_hermes_cli_returns_empty(self):
        mod = _load_module(lambda: _entry({"task": "code"}))
        with mock.patch.dict(
            sys.modules,
            {"hermes_cli.config": None},
        ):
            self.assertEqual(mod._read_routing_state(), {})

    def test_invalid_persisted_task_is_stripped(self):
        mod = _load_module(lambda: _entry({"task": "not-a-real-task", "policy": "speed"}))
        state = mod._read_routing_state()
        self.assertNotIn("task", state)
        self.assertEqual(state.get("policy"), "speed")

    def test_invalid_persisted_policy_is_stripped(self):
        mod = _load_module(lambda: _entry({"task": "code", "policy": "not-a-real-policy"}))
        state = mod._read_routing_state()
        self.assertEqual(state.get("task"), "code")
        self.assertNotIn("policy", state)

    def test_entry_missing_returns_empty(self):
        mod = _load_module(lambda: {})
        self.assertEqual(mod._read_routing_state(), {})

    def test_model_name_exact_match_required_no_whitespace_normalization(self):
        mod = _load_module(lambda: _entry({"task": "code"}))
        profile = mod.HostYourAiProfile(name="x")
        self.assertEqual(profile.build_extra_body(model="hyai/auto "), {})
        self.assertEqual(profile.build_extra_body(model=" hyai/auto"), {})
        self.assertEqual(profile.build_extra_body(model="HYAI/AUTO"), {})


if __name__ == "__main__":
    unittest.main()
