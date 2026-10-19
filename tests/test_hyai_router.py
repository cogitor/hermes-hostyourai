"""Unit tests for hyai-router/__init__.py's /hyai command handler.

Run with: python -m pytest tests/ -v   (or plain unittest discovery)
No dependency on a real Hermes install — ``ctx`` is a minimal in-memory fake
mirroring only the two methods the plugin actually calls:
``get_config(key, default)`` / ``set_config(key, value)`` / ``register_command(...)``.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parent.parent / "hyai-router" / "__init__.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("hyai_router_under_test", _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeCtx:
    """Minimal in-memory stand-in for the real plugin ``ctx``."""

    def __init__(self):
        self._store: dict = {}
        self.handler = None
        self.registered = None

    def get_config(self, key, default=None):
        return self._store.get(key, default)

    def set_config(self, key, value):
        self._store[key] = value

    def register_command(self, name, handler, description, args_hint="", argument_mode=None):
        assert name == "hyai"
        self.handler = handler
        self.registered = {"args_hint": args_hint, "argument_mode": argument_mode}


class HyaiCommandTestCase(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()
        self.ctx = FakeCtx()
        self.mod.register(self.ctx)
        self.call = self.ctx.handler

    # --- registration shape ------------------------------------------------
    def test_registers_as_options_mode(self):
        self.assertEqual(self.ctx.registered["argument_mode"], "options")

    # --- status / no-args -------------------------------------------------
    def test_status_no_args_when_unset(self):
        self.assertIn("not set", self.call(""))

    def test_status_explicit_when_unset(self):
        self.assertIn("not set", self.call("status"))

    def test_status_reflects_set_values(self):
        self.call("task code")
        self.call("policy speed")
        out = self.call("status")
        self.assertIn("task=code", out)
        self.assertIn("policy=speed", out)

    # --- task ---------------------------------------------------------
    def test_task_valid_persists_and_is_lowercased(self):
        self.call("task CODE")
        self.assertEqual(self.ctx.get_config(self.mod.CONFIG_KEY, {}).get("task"), "code")

    def test_task_invalid_shows_usage_and_does_not_persist(self):
        out = self.call("task bogus")
        self.assertIn("Unknown task", out)
        self.assertNotIn("task", self.ctx.get_config(self.mod.CONFIG_KEY, {}))

    def test_task_missing_value_shows_usage(self):
        out = self.call("task")
        self.assertIn("Unknown task", out)

    def test_task_set_preserves_existing_policy(self):
        self.call("policy cost")
        self.call("task rag")
        state = self.ctx.get_config(self.mod.CONFIG_KEY, {})
        self.assertEqual(state, {"policy": "cost", "task": "rag"})

    # --- policy ---------------------------------------------------------
    def test_policy_valid_persists(self):
        self.call("policy quality")
        self.assertEqual(self.ctx.get_config(self.mod.CONFIG_KEY, {}).get("policy"), "quality")

    def test_policy_invalid_shows_usage(self):
        self.assertIn("Unknown policy", self.call("policy nonsense"))

    def test_policy_missing_value_shows_usage(self):
        self.assertIn("Unknown policy", self.call("policy"))

    # --- transport is config-only, not an /hyai subcommand -----------------
    def test_transport_is_not_a_subcommand(self):
        out = self.call("transport headers")
        self.assertIn("Usage: /hyai", out)
        self.assertNotIn("transport", self.ctx.get_config(self.mod.CONFIG_KEY, {}))

    def test_usage_mentions_config_only_transport(self):
        self.assertIn("config-only", self.mod._USAGE)
        self.assertIn("hermes config set", self.mod._USAGE)

    # --- off / clear ---------------------------------------------------------
    def test_off_clears_task_and_policy(self):
        self.call("task code")
        self.call("off")
        state = self.ctx.get_config(self.mod.CONFIG_KEY, {})
        self.assertNotIn("task", state)
        self.assertNotIn("policy", state)

    def test_off_preserves_config_only_transport(self):
        # Transport is set directly via config (simulated here), never via /hyai.
        self.ctx.set_config(self.mod.CONFIG_KEY, {"task": "code", "transport": "headers"})
        self.call("off")
        self.assertEqual(self.ctx.get_config(self.mod.CONFIG_KEY, {}), {"transport": "headers"})

    def test_clear_is_alias_for_off(self):
        self.call("policy speed")
        self.call("clear")
        self.assertEqual(self.ctx.get_config(self.mod.CONFIG_KEY, {}), {})

    # --- unknown / malformed input ---------------------------------------------------------
    def test_unknown_subcommand_shows_usage(self):
        self.assertIn("Usage: /hyai", self.call("frobnicate"))

    def test_unicode_and_long_input_falls_through_to_usage_safely(self):
        out = self.call("task " + "🦄" * 500)
        self.assertIn("Unknown task", out)

    # --- enum parity across the two plugin files (regression guard for the
    # manually-duplicated enum lists called out in both files' comments) -----
    def test_task_and_policy_enums_match_provider_profile(self):
        provider_path = _MODULE_PATH.parent.parent / "model-providers" / "hostyourai" / "__init__.py"
        spec = importlib.util.spec_from_file_location("hostyourai_under_test_for_parity", provider_path)
        provider_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(provider_mod)
        self.assertEqual(self.mod.HYAI_TASKS, provider_mod.HYAI_TASKS)
        self.assertEqual(self.mod.HYAI_POLICIES, provider_mod.HYAI_POLICIES)

    # --- the companion provider profile must only ever hint hyai/auto -------
    def test_provider_profile_gates_strictly_on_hyai_auto(self):
        provider_path = _MODULE_PATH.parent.parent / "model-providers" / "hostyourai" / "__init__.py"
        spec = importlib.util.spec_from_file_location("hostyourai_under_test_for_gate", provider_path)
        provider_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(provider_mod)
        profile = provider_mod.hostyourai
        provider_mod._read_routing_state = lambda: {"task": "code", "policy": "speed"}
        for other_model in ("hyai/fast", "hyai/auto-2", "hyai", "", "openai/gpt-4o", None):
            self.assertEqual(profile.build_extra_body(model=other_model), {})
            self.assertEqual(profile.build_api_kwargs_extras(model=other_model), ({}, {}))
        self.assertEqual(
            profile.build_extra_body(model=provider_mod.HYAI_DEFAULT_MODEL),
            {"hyai": {"task": "code", "policy": "speed"}},
        )


if __name__ == "__main__":
    unittest.main()
