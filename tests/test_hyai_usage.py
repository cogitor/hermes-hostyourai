"""Tests for `/hyai usage` and the shorthand arguments.

The network is replaced by a fake `_http_get`, except in HttpLayerTests, which talks to a
throwaway local server. All values are invented.
"""

from __future__ import annotations

import http.server
import json
import os
import sys
import threading
import types
import unittest

from test_hyai_router import FakeCtx, _load_module

FAKE_KEY = "not-a-real-credential-placeholder"
BALANCE = {"balance": 12.34, "spend_month": 5.5, "auto_topup": False, "as_of": "2026-01-01T00:00:00Z"}


def ok(payload) -> tuple[int, str, str]:
    return 200, "", payload if isinstance(payload, str) else json.dumps(payload)


class UsageTestCase(unittest.TestCase):
    """A loaded plugin in a private chat, with a scripted /balance response."""

    def setUp(self):
        self.mod = _load_module()
        self.mod._private_chat = lambda: True
        self.response = ok(BALANCE)
        self.requests = 0

        def fake_get(path):
            self.requests += 1
            assert path == "/balance"
            if isinstance(self.response, Exception):
                raise self.response
            return self.response

        self.mod._http_get = fake_get
        self.ctx = FakeCtx()
        self.mod.register(self.ctx)
        self.call = self.ctx.handler
        self.addCleanup(os.environ.pop, "HOSTYOURAI_API_KEY", None)
        os.environ["HOSTYOURAI_API_KEY"] = FAKE_KEY


class PrivateChatRules(unittest.TestCase):
    """Which sessions may see the balance. Anything not proven private is refused."""

    CASES = [
        # (bound session vars, gateway process running, allowed)
        ({"HERMES_SESSION_CHAT_TYPE": "dm"}, False, True),
        ({"HERMES_SESSION_CHAT_TYPE": "DM"}, False, True),
        ({"HERMES_SESSION_CHAT_TYPE": "group"}, False, False),
        ({"HERMES_SESSION_CHAT_TYPE": "channel"}, False, False),
        ({"HERMES_SESSION_CHAT_TYPE": "thread"}, False, False),
        ({"HERMES_SESSION_CHAT_TYPE": "something-new"}, False, False),
        ({"HERMES_SESSION_PLATFORM": "telegram"}, False, False),  # gateway chat, type unknown
        ({"HERMES_SESSION_KEY": "k"}, False, False),
        ({"HERMES_SESSION_SOURCE": "tui", "HERMES_SESSION_KEY": "k"}, False, False),  # cannot prove a DM
        ({"HERMES_SESSION_SOURCE": "cli"}, False, True),
        ({"HERMES_SESSION_SOURCE": "TUI"}, False, True),
        ({"HERMES_SESSION_SOURCE": "desktop"}, False, True),
        ({"HERMES_SESSION_SOURCE": "telegram"}, False, False),
        ({"HERMES_SESSION_SOURCE": "kanban"}, False, False),
        ({}, False, True),  # plain CLI process
        ({}, True, False),  # gateway process whose session context was lost
    ]

    def stub_hermes(self, get_session_env, gateway_running=False):
        context = types.ModuleType("gateway.session_context")
        context.get_session_env = get_session_env
        names = ("gateway", "gateway.session_context", "gateway.run")
        saved = {name: sys.modules.get(name) for name in names}
        for name, module in saved.items():
            self.addCleanup(sys.modules.pop, name, None) if module is None else self.addCleanup(
                sys.modules.__setitem__, name, module
            )
        sys.modules["gateway"], sys.modules["gateway.session_context"] = types.ModuleType("gateway"), context
        if gateway_running:
            sys.modules["gateway.run"] = types.ModuleType("gateway.run")
        else:
            sys.modules.pop("gateway.run", None)

    def test_rules(self):
        mod = _load_module()
        for env, gateway_running, allowed in self.CASES:
            with self.subTest(env=env, gateway_running=gateway_running):
                self.stub_hermes(lambda name, default="", env=env: env.get(name, default), gateway_running)
                self.assertIs(mod._private_chat(), allowed)

    def test_unreadable_session_context_is_refused(self):
        def broken(*_args, **_kwargs):
            raise RuntimeError("boom")

        self.stub_hermes(broken)
        self.assertFalse(_load_module()._private_chat())


class UsageReplyTests(UsageTestCase):
    def test_shows_balance_spend_and_top_up(self):
        reply = self.call("usage")
        self.assertIn("Balance: €12.34", reply)
        self.assertIn("Spent this month: €5.50", reply)
        self.assertIn("Auto top-up: off", reply)

    def test_refused_outside_a_private_chat_without_any_request(self):
        self.mod._private_chat = lambda: False
        self.assertIn("private chat", self.call("usage"))
        self.assertEqual(self.requests, 0)

    def test_works_when_the_chat_model_is_not_hyai_auto(self):
        self.mod._active_model = lambda: ("mistral", "mistral-small-latest")
        self.assertIn("Balance: €12.34", self.call("usage"))

    def test_unlimited_account_is_never_low(self):
        self.response = ok({"balance": 0, "spend_month": 1, "unlimited": True})
        reply = self.call("usage")
        self.assertIn("unlimited", reply)
        self.assertNotIn("Low balance", reply)

    def test_low_balance_note_follows_the_threshold_setting(self):
        self.response = ok({"balance": 4.0, "spend_month": 1})
        self.assertIn("Low balance (below €5.00)", self.call("usage"))  # default 5
        self.ctx.set_config("usage_warn_eur", 3)
        self.assertNotIn("Low balance", self.call("usage"))
        self.ctx.set_config("usage_warn_eur", 0)  # 0 turns the note off
        self.assertNotIn("Low balance", self.call("usage"))

    def test_missing_balance_is_shown_as_zero_and_counts_as_low(self):
        self.response = ok({"spend_month": 1})
        reply = self.call("usage")
        self.assertIn("Balance: €0.00", reply)
        self.assertIn("Low balance", reply)

    def test_bad_numbers_are_shown_as_zero(self):
        for bad in (float("nan"), float("inf"), -5, "abc", None, [], {}, 10**400):
            with self.subTest(bad=bad):
                self.assertEqual(self.mod._num(bad), 0.0)
        self.assertEqual(self.mod._num("2.5"), 2.5)
        self.response = ok('{"balance": 1' + "0" * 400 + ', "spend_month": 1}')  # overflows a float
        self.assertIn("Balance: €0.00", self.call("usage"))

    def test_as_of_is_a_short_local_time_and_omitted_if_not_a_timestamp(self):
        reply = self.call("usage")
        self.assertRegex(reply, r"As of: \w{3} \d{2}, \d{2}:\d{2}\b")
        self.assertNotIn("2026-01-01T", reply)
        for stamp in (
            "2026-01-01T13:00:00Z",
            "2026-01-01T13:00:00+00:00",
            "2026-01-01T13:00:00.123456Z",
            "2026-01-01T13:00:00",
        ):
            with self.subTest(stamp=stamp):
                self.mod._cache = None
                self.response = ok({**BALANCE, "as_of": stamp})
                self.assertIn("As of: Jan 01", self.call("usage"))  # reached on Python 3.10 too
        for bad in ({"x": 1}, "x" * 500, "", None, 12345):
            with self.subTest(bad=bad):
                self.mod._cache = None
                self.response = ok({**BALANCE, "as_of": bad})
                self.assertNotIn("As of", self.call("usage"))


class UsageErrorTests(UsageTestCase):
    """Every failure is one plain line."""

    def test_error_replies(self):
        cases = [
            ((401, "", ""), "key rejected"),
            ((403, "", ""), "personal key"),
            ((429, "7", ""), "retry in 7s"),
            ((429, "", ""), "rate limited"),
            ((500, "", ""), "HTTP 500"),
            ((302, "", ""), "HTTP 302"),
            (ok("<html>"), "unexpected response"),
            (ok("[]"), "unexpected response"),
            (ok("null"), "unexpected response"),
            (ok("[" * 100_000), "unexpected response"),  # nested deeper than the parser allows
            (self.mod._ApiError("HostYourAI unreachable"), "unreachable"),
            (self.mod._ApiError("no HOSTYOURAI_API_KEY set"), "no HOSTYOURAI_API_KEY"),
        ]
        for response, expected in cases:
            with self.subTest(expected=expected):
                self.mod._cache = None
                self.response = response
                reply = self.call("usage")
                self.assertIn(expected, reply)
                self.assertNotIn("\n", reply)

    def test_failed_lookups_are_not_remembered(self):
        self.response = (500, "", "")
        self.call("usage")
        self.response = ok({"balance": 9, "spend_month": 1})
        self.assertIn("€9.00", self.call("usage"))


class CacheTests(UsageTestCase):
    def test_second_call_within_a_minute_makes_no_request(self):
        self.call("usage")
        self.call("usage")
        self.assertEqual(self.requests, 1)

    def test_call_after_a_minute_asks_again(self):
        clock = [1000.0]
        self.mod.time = types.SimpleNamespace(monotonic=lambda: clock[0])
        self.call("usage")
        clock[0] += 61
        self.call("usage")
        self.assertEqual(self.requests, 2)

    def test_changing_the_api_key_does_not_serve_the_old_balance(self):
        self.call("usage")
        os.environ["HOSTYOURAI_API_KEY"] = FAKE_KEY + "-rotated"
        self.call("usage")
        self.assertEqual(self.requests, 2)


class HttpLayerTests(unittest.TestCase):
    """The real `_http_get` against a local server (plain HTTP, through API_BASE)."""

    def setUp(self):
        self.mod = _load_module()
        self.addCleanup(os.environ.pop, "HOSTYOURAI_API_KEY", None)
        os.environ["HOSTYOURAI_API_KEY"] = FAKE_KEY

    def serve(self, do_get):
        handler = type(
            "H", (http.server.BaseHTTPRequestHandler,), {"do_GET": do_get, "log_message": lambda *a: None}
        )
        server = http.server.HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.mod.API_BASE = f"http://127.0.0.1:{server.server_port}"
        return server

    def test_the_real_base_url_is_hostyourai(self):
        self.assertEqual(_load_module().API_BASE, "https://hostyourai.com/api/v1")

    def test_key_is_sent_only_as_a_bearer_header_on_the_requested_path(self):
        seen = {}

        def do_get(handler):
            seen["path"], seen["auth"] = handler.path, handler.headers.get("Authorization")
            handler.send_response(200)
            handler.end_headers()
            handler.wfile.write(b"{}")

        self.serve(do_get)
        self.assertEqual(self.mod._http_get("/balance")[0], 200)
        self.assertEqual(seen, {"path": "/balance", "auth": f"Bearer {FAKE_KEY}"})

    def test_a_redirect_is_not_followed_so_the_key_never_reaches_the_target(self):
        target_saw = []

        def target_get(handler):
            target_saw.append(handler.headers.get("Authorization"))
            handler.send_response(200)
            handler.end_headers()

        target = self.serve(target_get)

        def redirect_get(handler):
            handler.send_response(302)
            handler.send_header("Location", f"http://127.0.0.1:{target.server_port}/steal")
            handler.end_headers()

        self.serve(redirect_get)
        self.assertEqual(self.mod._http_get("/balance")[0], 302)
        self.assertEqual(target_saw, [])

    def test_retry_after_is_passed_on(self):
        def do_get(handler):
            handler.send_response(429)
            handler.send_header("Retry-After", "9")
            handler.end_headers()

        self.serve(do_get)
        self.assertEqual(self.mod._http_get("/balance"), (429, "9", ""))

    def test_oversized_body_is_cut_off(self):
        def do_get(handler):
            handler.send_response(200)
            handler.end_headers()
            handler.wfile.write(b"x" * 3_000_000)

        self.serve(do_get)
        self.assertLessEqual(len(self.mod._http_get("/balance")[2]), 2_000_000)

    def test_unreachable_host_gives_a_message_without_key_or_address(self):
        self.mod.API_BASE = "http://127.0.0.1:1"  # nothing listens here
        with self.assertRaises(self.mod._ApiError) as caught:
            self.mod._http_get("/balance")
        self.assertEqual(str(caught.exception), "HostYourAI unreachable")

    def test_no_key_means_no_request(self):
        os.environ.pop("HOSTYOURAI_API_KEY")
        self.mod._OPENER = types.SimpleNamespace(
            open=lambda *a, **k: self.fail("sent a request without a key")
        )
        with self.assertRaises(self.mod._ApiError):
            self.mod._http_get("/balance")


class ShorthandTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_module()
        self.model = ("hostyourai", "hyai/auto")
        self.mod._active_model = lambda: self.model
        self.ctx = FakeCtx()
        self.mod.register(self.ctx)

    def call(self, args):
        return self.ctx.handler(args)

    def saved(self):
        return self.ctx.get_config(self.mod.CONFIG_KEY, {})

    def test_task_and_policy_in_either_order(self):
        self.call("code cost")
        self.assertEqual(self.saved(), {"task": "code", "policy": "cost"})
        self.call("quality summarise")
        self.assertEqual(self.saved(), {"task": "summarise", "policy": "quality"})

    def test_one_word_changes_only_its_own_part(self):
        self.call("code cost")
        self.call("speed")
        self.assertEqual(self.saved(), {"task": "code", "policy": "speed"})

    def test_old_aliases_are_not_accepted(self):
        for word in ("cheap", "fast", "best", "summarize", "summary"):
            with self.subTest(word=word):
                self.assertIn("Usage", self.call(word))
                self.assertEqual(self.saved(), {})

    def test_unknown_or_conflicting_words_change_nothing(self):
        self.call("task code")
        for args in ("code banana", "code legal", "cost speed", "code code"):
            with self.subTest(args=args):
                self.assertIn("Usage", self.call(args))
                self.assertEqual(self.saved(), {"task": "code"})

    def test_a_hand_set_transport_is_kept(self):
        self.ctx._store[self.mod.CONFIG_KEY] = {"transport": "headers"}
        self.call("cost")
        self.assertEqual(self.saved(), {"transport": "headers", "policy": "cost"})

    def test_other_models_are_not_touched(self):
        self.model = ("mistral", "mistral-small-latest")
        for args in ("code cost", "task code", "policy cost", "off"):
            with self.subTest(args=args):
                self.assertIn("only apply", self.call(args))
        self.assertEqual(self.saved(), {})

    def test_help_prints_the_usage_text(self):
        self.assertEqual(self.call("help"), self.mod._USAGE)

    def test_telegram_menu_hint_does_not_start_with_an_angle_bracket(self):
        self.assertFalse(self.ctx.registered["args_hint"].startswith("<"))
        self.assertIn("usage", self.ctx.registered["args_hint"])


if __name__ == "__main__":
    unittest.main()
