"""One test per rule in SPEC.md (rule R4 is test_R4_...). A meta-test fails if a rule has none."""

from __future__ import annotations

import os
import re
import threading
import time
import unittest
from pathlib import Path

import test_hyai_auto
from test_hyai_router import FakeCtx, _load_module
from test_hyai_usage import BALANCE, FAKE_KEY, UsageTestCase, ok

ROOT = Path(__file__).resolve().parent.parent
OTHER_MODEL = ("mistral", "mistral-small-latest")
HYAI_AUTO = ("hostyourai", "hyai/auto")


class SpecCoverage(unittest.TestCase):
    def test_every_rule_in_the_spec_has_a_test(self):
        rules = set(re.findall(r"^- \*\*([RUA]\d+)\*\*", (ROOT / "SPEC.md").read_text(), re.M))
        self.assertGreaterEqual(len(rules), 30)
        names = " ".join(dir(RoutingSpec) + dir(UsageSpec) + dir(test_hyai_auto.AutoSpec))
        self.assertEqual(sorted(r for r in rules if f"test_{r}_" not in names), [])


class RoutingSpec(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()
        self.model = HYAI_AUTO
        self.mod._active_model = lambda: self.model
        self.ctx = FakeCtx()
        self.mod.register(self.ctx)
        self.call = self.ctx.handler

    def saved(self):
        return self.ctx.get_config(self.mod.CONFIG_KEY, {})

    def test_R1_bare_command_shows_setting_and_choices_on_any_model(self):
        self.call("task code")
        for model in (HYAI_AUTO, OTHER_MODEL):
            self.model = model
            out = self.call("")
            self.assertIn("task=code", out)
            self.assertIn("policy:", out)  # the list of choices

    def test_R2_status_shows_the_setting_without_the_choices_on_any_model(self):
        self.call("task code")
        for model in (HYAI_AUTO, OTHER_MODEL):
            self.model = model
            out = self.call("status")
            self.assertIn("task=code", out)
            self.assertNotIn("shorthand", out)

    def test_R3_other_models_get_a_note_on_read_and_hyai_auto_does_not(self):
        for args in ("", "status"):
            self.model = HYAI_AUTO
            self.assertNotIn("only apply", self.call(args))
            self.model = OTHER_MODEL
            self.assertIn("only apply to hostyourai hyai/auto", self.call(args))

    def test_R4_single_value_is_saved_lowercased_and_keeps_the_other(self):
        self.call("task CODE")
        self.call("policy Cost")
        self.assertEqual(self.saved(), {"task": "code", "policy": "cost"})
        for args in ("task", "task banana", "policy", "policy cheap"):
            with self.subTest(args=args):
                self.assertIn("Usage", self.call(args))
                self.assertEqual(self.saved(), {"task": "code", "policy": "cost"})

    def test_R5_shorthand_takes_task_and_policy_in_any_order_and_no_aliases(self):
        self.call("cost code")
        self.assertEqual(self.saved(), {"task": "code", "policy": "cost"})
        self.call("Summarise SPEED")
        self.assertEqual(self.saved(), {"task": "summarise", "policy": "speed"})
        for args in (
            "code banana",
            "code legal",
            "cost quality",
            "code code",
            "cost cost",
            "cheap",
            "fast code",
        ):
            with self.subTest(args=args):
                self.assertIn("Usage", self.call(args))
                self.assertEqual(self.saved(), {"task": "summarise", "policy": "speed"})

    def test_R6_off_and_clear_remove_task_and_policy_but_keep_transport(self):
        for word in ("off", "clear"):
            self.ctx._store[self.mod.CONFIG_KEY] = {"task": "code", "policy": "cost", "transport": "headers"}
            self.call(word)
            self.assertEqual(self.saved(), {"transport": "headers"})

    def test_R7_changes_are_refused_off_hyai_auto_and_when_the_model_is_unknown(self):
        for model in (OTHER_MODEL, ("", ""), ("hostyourai", "hyai/fast"), ("other", "hyai/auto")):
            self.model = model
            for args in ("task code", "policy cost", "code cost", "off", "clear"):
                with self.subTest(model=model, args=args):
                    self.assertIn("only apply", self.call(args))
                    self.assertEqual(self.saved(), {})

    def test_R8_unknown_subcommand_shows_usage_and_changes_nothing(self):
        self.call("task code")
        for args in ("banana", "TASK", "usage2", "💥", "x" * 5000):
            with self.subTest(args=args[:20]):
                self.assertIn("Usage", self.call(args))
                self.assertEqual(self.saved(), {"task": "code"})

    def test_R9_help_shows_usage_on_any_model(self):
        for model in (HYAI_AUTO, OTHER_MODEL, ("", "")):
            self.model = model
            self.assertEqual(self.call("help"), self.mod._USAGE)

    def test_R10_no_reply_mentions_transport(self):
        self.ctx._store[self.mod.CONFIG_KEY] = {"transport": "headers"}
        for model in (HYAI_AUTO, OTHER_MODEL):
            self.model = model
            for args in ("", "status", "help", "off", "task code", "cost", "banana", "transport headers"):
                self.assertNotIn("transport", self.call(args).lower())

    def test_R11_saved_setting_is_sent_only_to_hyai_auto(self):
        from test_hostyourai_provider import _entry
        from test_hostyourai_provider import _load_module as load_provider

        profile = load_provider(lambda: _entry({"task": "code", "policy": "cost"})).hostyourai
        self.assertEqual(
            profile.build_extra_body(model="hyai/auto"), {"hyai": {"task": "code", "policy": "cost"}}
        )
        for model in ("mistral-small-latest", "hyai/auto-2", "hyai/fast", "HYAI/AUTO", " hyai/auto", ""):
            with self.subTest(model=model):
                self.assertEqual(profile.build_extra_body(model=model), {})
                self.assertEqual(profile.build_api_kwargs_extras(model=model), ({}, {}))


class UsageSpec(UsageTestCase):
    def test_U1_works_on_any_model_and_never_changes_the_setting(self):
        self.ctx._store[self.mod.CONFIG_KEY] = {"task": "code"}
        for model in (HYAI_AUTO, OTHER_MODEL, ("", "")):
            self.mod._active_model = lambda model=model: model
            self.mod._cache = None
            self.assertIn("Balance:", self.call("usage"))
        self.assertEqual(self.ctx.get_config(self.mod.CONFIG_KEY, {}), {"task": "code"})

    def test_U2_shows_balance_spend_top_up_and_local_time_in_euro(self):
        out = self.call("usage")
        self.assertIn("Balance: €12.34", out)
        self.assertIn("Spent this month: €5.50", out)
        self.assertIn("Auto top-up: off", out)
        self.assertRegex(out, r"As of: \w{3} \d{2}, \d{2}:\d{2}$")
        self.response = ok({**BALANCE, "balance": 1234.5, "auto_topup": True})
        self.mod._cache = None
        out = self.call("usage")
        self.assertIn("€1,234.50", out)
        self.assertIn("Auto top-up: on", out)

    def test_U3_refused_when_not_provably_private_and_no_request_is_made(self):
        # The detailed rules are tabled in PrivateChatRules (test_hyai_usage).
        self.mod._private_chat = lambda: False
        self.assertIn("private chat", self.call("usage"))
        self.assertEqual(self.requests, 0)

    def test_U4_one_get_to_balance_with_bearer_key_no_redirects_capped_body_timeout(self):
        # The wire details are tested against a local server in HttpLayerTests (test_hyai_usage).
        self.assertEqual(self.mod._HTTP_TIMEOUT, 5.0)
        self.call("usage")
        self.assertEqual(self.requests, 1)

    def test_U5_cached_for_a_minute_per_key_failures_not_cached_one_request_at_a_time(self):
        self.call("usage")
        self.call("usage")
        self.assertEqual(self.requests, 1)
        os.environ["HOSTYOURAI_API_KEY"] = FAKE_KEY + "-other"
        self.call("usage")
        self.assertEqual(self.requests, 2)

        # Simultaneous cold calls make one request, and everyone gets the answer.
        self.mod._cache = None
        self.requests = 0
        fast_get = self.mod._http_get

        def slow_get(path):
            time.sleep(0.2)
            return fast_get(path)

        self.mod._http_get = slow_get
        replies = []
        threads = [threading.Thread(target=lambda: replies.append(self.call("usage"))) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(self.requests, 1)
        self.assertEqual(len(replies), 4)
        self.assertTrue(all("Balance: €12.34" in reply for reply in replies))

    def test_U6_low_balance_line_uses_the_threshold_and_bad_settings_mean_default(self):
        self.response = ok({"balance": 4.0, "spend_month": 1})
        for setting, low in ((None, True), (0, False), (3, False), (10, True), ("abc", True), (-1, True)):
            with self.subTest(setting=setting):
                self.ctx.set_config("usage_warn_eur", setting)
                self.mod._cache = None
                self.assertEqual("Low balance" in self.call("usage"), low)
        self.ctx.set_config("usage_warn_eur", 5)
        self.response = ok({"balance": 5.0, "spend_month": 1})
        self.mod._cache = None
        self.assertNotIn("Low balance", self.call("usage"))  # equal to the threshold is not low
        self.response = ok({"balance": 0, "spend_month": 1, "unlimited": True})
        self.mod._cache = None
        self.assertNotIn("Low balance", self.call("usage"))

    def test_U7_bad_amounts_show_zero_and_bad_time_is_left_out(self):
        self.response = ok({"balance": "abc", "spend_month": -3, "as_of": "yesterday"})
        out = self.call("usage")
        self.assertIn("Balance: €0.00", out)
        self.assertIn("Spent this month: €0.00", out)
        self.assertNotIn("As of", out)

    def test_U8_every_failure_is_one_line_with_no_key_url_or_exception_text(self):
        cases = [
            ((401, "", ""), "key rejected"),
            ((403, "", ""), "personal key"),
            ((429, "9", ""), "retry in 9s"),
            ((500, "", ""), "HTTP 500"),
            (ok("<html>"), "unexpected response"),
            (self.mod._ApiError("HostYourAI unreachable"), "unreachable"),
            (self.mod._ApiError("no HOSTYOURAI_API_KEY set"), "no HOSTYOURAI_API_KEY"),
        ]
        for response, expected in cases:
            with self.subTest(expected=expected):
                self.mod._cache = None
                self.response = response
                out = self.call("usage")
                self.assertTrue(out.startswith("HostYourAI balance unavailable:"), out)
                self.assertIn(expected, out)
                self.assertNotIn("\n", out)
                for leak in (FAKE_KEY, "https://", "Traceback"):
                    self.assertNotIn(leak, out)

    def test_U9_malformed_replies_never_raise(self):
        for body in (
            "[" * 100_000,
            "1" * 5000,
            '{"balance": 1' + "0" * 400 + "}",
            "\x00",
            "{}",
            '{"balance": {}}',
        ):
            with self.subTest(body=body[:15]):
                self.mod._cache = None
                self.response = ok(body)
                self.assertIsInstance(self.call("usage"), str)


if __name__ == "__main__":
    unittest.main()
