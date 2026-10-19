"""Unit tests for hyai-router/__init__.py's /hyai command handler.

Run with: python -m pytest tests/ -v   (or plain unittest discovery)
No dependency on a real Hermes install — ``ctx`` is a minimal in-memory fake
mirroring only the two methods the plugin actually calls:
``get_config(key, default)`` / ``set_config(key, value)`` / ``register_command(...)``.
"""

from __future__ import annotations

import importlib.util
import sys
import types
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
        self.model = ("hostyourai", "hyai/auto")
        self.mod._active_model = lambda: self.model
        self.ctx = FakeCtx()
        self.mod.register(self.ctx)
        self.call = self.ctx.handler

    # --- only applies to hostyourai hyai/auto -------------------------------
    def test_refuses_on_other_models_and_changes_nothing(self):
        for model in (
            ("mistral", "mistral-small-latest"),
            ("hostyourai", "hyai/fast"),
            ("hostyourai", "hyai/auto-2"),
            ("other", "hyai/auto"),
            ("", ""),
        ):
            self.model = model
            for args in ("", "status", "task code", "policy cost", "off"):
                out = self.call(args)
                self.assertIn("only applies to hostyourai hyai/auto", out)
            self.assertEqual(self.ctx.get_config(self.mod.CONFIG_KEY, {}), {})

    def test_session_selection_wins_over_config_default(self):
        mod = _load_module()
        mod._session_route = lambda: ("mistral", "mistral-small-latest")
        mod._config_route = lambda: ("hostyourai", "hyai/auto")
        self.assertEqual(mod._active_model(), ("mistral", "mistral-small-latest"))
        self.assertIn("only applies", mod._not_applicable())

    def test_session_model_without_provider_does_not_borrow_config_provider(self):
        mod = _load_module()
        mod._session_route = lambda: ("", "claude-sonnet-5")
        mod._config_route = lambda: ("hostyourai", "hyai/auto")
        self.assertEqual(mod._active_model(), ("", "claude-sonnet-5"))
        self.assertIn("Current model: claude-sonnet-5.", mod._not_applicable())
        mod._session_route = lambda: ("", "hyai/auto")
        self.assertIsNone(mod._not_applicable())

    def test_config_is_fallback_only_when_session_has_no_model(self):
        mod = _load_module()
        mod._session_route = lambda: ("", "")
        mod._config_route = lambda: ("hostyourai", "hyai/auto")
        self.assertEqual(mod._active_model(), ("hostyourai", "hyai/auto"))
        self.assertIsNone(mod._not_applicable())

    def test_unreadable_state_fails_closed(self):
        mod = _load_module()

        def boom():
            raise RuntimeError("x")

        mod._session_route = boom
        mod._config_route = boom
        self.assertEqual(mod._active_model(), ("", ""))
        self.assertIn("unknown", mod._not_applicable())

    # --- registration shape ------------------------------------------------
    def test_registers_as_options_mode(self):
        self.assertEqual(self.ctx.registered["argument_mode"], "options")

    def test_args_hint_not_required_style_so_telegram_menu_keeps_it(self):
        # Hermes hides plugin commands whose args_hint starts with "<" from the Telegram menu.
        self.assertFalse(self.ctx.registered["args_hint"].strip().startswith("<"))

    def test_bare_command_lists_choices(self):
        out = self.call("")
        self.assertIn("task", out)
        self.assertIn("policy", out)

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

    def test_set_reply_says_the_setting_is_profile_wide(self):
        self.assertIn("every chat", self.call("task code"))

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

    def test_command_output_never_mentions_transport(self):
        self.ctx.set_config(self.mod.CONFIG_KEY, {"task": "code", "transport": "headers"})
        for args in ("", "status", "task", "policy", "bogus", "off"):
            self.assertNotIn("transport", self.call(args).lower())
        self.assertNotIn("transport", self.mod._USAGE.lower())

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


class RealRoutingReadTests(unittest.TestCase):
    """Exercise the real SQL/JSON path against a temp state.db with the real gateway_routing schema."""

    SCOPE = "/h/sessions"

    def setUp(self):
        import json
        import sqlite3
        import tempfile

        self.mod = _load_module()
        self.json = json
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = f"{self.tmp.name}/state.db"
        conn = sqlite3.connect(self.db)
        conn.execute(
            "CREATE TABLE gateway_routing (scope TEXT NOT NULL DEFAULT '', session_key TEXT NOT NULL, "
            "entry_json TEXT NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY (scope, session_key))"
        )
        conn.commit()
        conn.close()

    def _put(self, scope, key, entry, ts=1.0):
        import sqlite3

        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO gateway_routing VALUES (?,?,?,?)", (scope, key, self.json.dumps(entry), ts))
        conn.commit()
        conn.close()

    def test_reads_override_for_exact_scope_and_key(self):
        self._put(self.SCOPE, "k1", {"model_override": {"provider": "mistral", "model": "m-small"}})
        self._put(self.SCOPE, "k2", {"model_override": {"provider": "hostyourai", "model": "hyai/auto"}})
        self.assertEqual(self.mod._routing_override(self.db, self.SCOPE, "k1"), ("mistral", "m-small"))
        self.assertEqual(self.mod._routing_override(self.db, self.SCOPE, "k2"), ("hostyourai", "hyai/auto"))

    def test_other_scope_never_leaks_even_if_newer(self):
        self._put(self.SCOPE, "k", {"model_override": {"provider": "mistral", "model": "m"}}, ts=1.0)
        self._put(
            "/other/sessions",
            "k",
            {"model_override": {"provider": "hostyourai", "model": "hyai/auto"}},
            ts=9.0,
        )
        self.assertEqual(self.mod._routing_override(self.db, self.SCOPE, "k"), ("mistral", "m"))

    def test_no_row_or_no_override_is_empty(self):
        self._put(self.SCOPE, "k", {"session_id": "x"})
        self.assertEqual(self.mod._routing_override(self.db, self.SCOPE, "k"), ("", ""))
        with self.assertRaises(LookupError):  # unknown row -> gate fails closed, no config fallback
            self.mod._routing_override(self.db, self.SCOPE, "missing")

    def test_malformed_json_and_missing_table_raise_so_gate_fails_closed(self):
        import sqlite3

        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO gateway_routing VALUES (?,?,?,?)", (self.SCOPE, "bad", "{not json", 1.0))
        conn.commit()
        conn.close()
        with self.assertRaises(ValueError):
            self.mod._routing_override(self.db, self.SCOPE, "bad")
        with self.assertRaises(sqlite3.Error):
            self.mod._routing_override(f"{self.tmp.name}/nope.db", self.SCOPE, "k")

    def test_path_with_uri_special_chars_is_readable(self):
        import os
        import shutil

        d = os.path.join(self.tmp.name, "a b?c#d%e")
        os.makedirs(d)
        dst = os.path.join(d, "state.db")
        shutil.copy(self.db, dst)
        self._put(self.SCOPE, "k", {"model_override": {"provider": "p", "model": "m"}})
        shutil.copy(self.db, dst)
        self.assertEqual(self.mod._routing_override(dst, self.SCOPE, "k"), ("p", "m"))

    def test_connection_is_read_only(self):
        import sqlite3

        self._put(self.SCOPE, "k", {})
        conn = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        with self.assertRaises(sqlite3.OperationalError):
            conn.execute("DELETE FROM gateway_routing")
        conn.close()

    def test_session_key_selects_row_and_unset_key_falls_back(self):
        import os
        from unittest import mock

        self._put(self.SCOPE, "k1", {"model_override": {"provider": "mistral", "model": "m"}})
        fake_gsc = types.ModuleType("gateway.session_context")
        fake_gsc.get_session_env = lambda name, default="": os.environ.get(name, default)
        fake_hc = types.ModuleType("hermes_constants")
        home = Path(self.tmp.name)
        (home / "sessions").mkdir()
        fake_hc.get_hermes_home = lambda: home
        scope = str((home / "sessions").resolve())
        self._put(scope, "k1", {"model_override": {"provider": "hostyourai", "model": "hyai/auto"}})
        mods = {
            "gateway": types.ModuleType("gateway"),
            "gateway.session_context": fake_gsc,
            "hermes_constants": fake_hc,
        }
        with mock.patch.dict(sys.modules, mods), mock.patch.dict(os.environ, {"HERMES_SESSION_KEY": "k1"}):
            self.assertEqual(self.mod._session_route(), ("hostyourai", "hyai/auto"))
        with mock.patch.dict(sys.modules, mods), mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HERMES_SESSION_KEY", None)
            self.assertEqual(self.mod._session_route(), ("", ""))

    def test_config_plain_string_model_is_accepted(self):
        import sys as _sys
        from unittest import mock

        fake = types.ModuleType("hermes_cli.config")
        fake.load_config_readonly = lambda: {"model": "hyai/auto"}
        with mock.patch.dict(
            _sys.modules, {"hermes_cli": types.ModuleType("hermes_cli"), "hermes_cli.config": fake}
        ):
            self.assertEqual(self.mod._config_route(), ("", "hyai/auto"))


if __name__ == "__main__":
    unittest.main()
