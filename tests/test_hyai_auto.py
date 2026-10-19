"""Auto task mode, tests for the rules A1-A13 in SPEC.md, against a local stub of the System One API.

Everything is invented. The stub plays HostYourAI; no request leaves the machine.
"""

from __future__ import annotations

import copy
import http.server
import json
import socket
import threading
import time
import unittest
from unittest import mock

from test_hyai_router import FakeCtx, _load_module

FAKE_KEY = "not-a-real-credential-placeholder"
HYAI_AUTO = ("hostyourai", "hyai/auto")
LONG = "Please fix the failing unit test in my parser module."
PREV = "I am working on a small tokenizer in Python."


def answer(choice="code", confidence=0.95):
    return 200, {"answers": {"task": {"choice": choice, "confidence": confidence}}}


class Stub:
    """A System One server. `reply` is a function (request body) -> (status, body) and may sleep."""

    def __init__(self):
        self.requests: list[dict] = []
        self.reply = lambda body: answer()
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                body = json.loads(raw)
                outer.requests.append(
                    {"path": self.path, "auth": self.headers.get("Authorization"), "body": body}
                )
                status, payload, *extra = outer.reply(body)
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                for name, value in (extra[0] if extra else {}).items():
                    self.send_header(name, value)
                self.end_headers()
                try:
                    self.wfile.write(data)
                except OSError:
                    pass  # the client gave up (a timeout test)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class OldHermesCtx(FakeCtx):
    """A Hermes that has no middleware yet."""

    def __getattribute__(self, name):
        if name == "register_middleware":
            raise AttributeError(name)
        return super().__getattribute__(name)


class AutoTestCase(unittest.TestCase):
    """A loaded plugin on hyai/auto with task=auto, pointed at the stub."""

    def setUp(self):
        self.stub = Stub()
        self.addCleanup(self.stub.close)
        self.mod = _load_module()
        self.mod.AUTO_BASE = self.stub.base
        self.model = HYAI_AUTO
        self.mod._active_model = lambda: self.model
        self.ctx = FakeCtx()
        self.mod.register(self.ctx)
        self.ctx.set_config(self.mod.CONFIG_KEY, {"task": "auto"})
        env = mock.patch.dict("os.environ", {"HOSTYOURAI_API_KEY": FAKE_KEY})
        env.start()
        self.addCleanup(env.stop)
        self.turn = 0

    def hook(self, text, history, turn_id, **kw):
        """The pre_llm_call hook for one user message. `history` is what came before it."""
        messages = [{"role": "user", "content": h} for h in history] + [{"role": "user", "content": text}]
        args = {
            "session_id": "s1",
            "turn_id": turn_id,
            "user_message": text,
            "conversation_history": messages,
            "model": "hyai/auto",
            "platform": "telegram",
            "parent_session_id": "",
            **kw,
        }
        self.ctx.hooks["pre_llm_call"](**args)
        return args

    def send(self, text=LONG, history=(PREV,), **kw):
        """One user message: the pre_llm_call hook, then one model call. Returns the request."""
        self.turn += 1
        turn_id = kw.pop("turn_id", f"t{self.turn}")
        args = self.hook(text, history, turn_id, **kw)
        return self.model_call(turn_id, model=args["model"])

    def model_call(self, turn_id, model="hyai/auto", request=None):
        request = {"model": model, "messages": [], "extra_body": {}} if request is None else request
        result = self.ctx.middleware["llm_request"](
            request=request, turn_id=turn_id, session_id="s1", model=model, provider="hostyourai"
        )
        return (result or {}).get("request", request)

    def task_sent(self, request):
        return (request.get("extra_body") or {}).get("hyai", {}).get("task")


class AutoSpec(AutoTestCase):
    def test_A1_task_auto_is_saved_on_hyai_auto_only_and_any_other_value_replaces_it(self):
        self.ctx.set_config(self.mod.CONFIG_KEY, {})
        self.assertIn("task=auto", self.ctx.handler("task auto"))
        self.assertEqual(self.ctx.get_config(self.mod.CONFIG_KEY)["task"], "auto")
        self.ctx.handler("task code")
        self.assertEqual(self.ctx.get_config(self.mod.CONFIG_KEY)["task"], "code")
        self.ctx.handler("task auto")
        self.ctx.handler("off")
        self.assertNotIn("task", self.ctx.get_config(self.mod.CONFIG_KEY))
        self.model = ("mistral", "mistral-small-latest")
        self.assertIn("Nothing changed", self.ctx.handler("task auto"))
        self.assertNotIn("task", self.ctx.get_config(self.mod.CONFIG_KEY))
        self.assertIn("auto", self.ctx.handler("help"))

    def test_A2_the_hint_is_added_only_when_the_saved_task_is_auto_and_the_model_is_hyai_auto(self):
        self.assertEqual(self.task_sent(self.send()), "code")
        self.assertEqual(len(self.stub.requests), 1)
        for saved in ({}, {"task": "legal"}, {"policy": "cost"}):
            self.ctx.set_config(self.mod.CONFIG_KEY, saved)
            self.stub.requests.clear()
            request = self.send()
            self.assertEqual(self.stub.requests, [])
            self.assertNotIn("hyai", request["extra_body"])
        self.ctx.set_config(self.mod.CONFIG_KEY, {"task": "auto"})
        self.assertEqual(self.task_sent(self.send(model="hyai/fast")), None)

    def test_A3_the_classifier_runs_once_per_user_message_and_every_model_call_gets_the_result(self):
        first = self.send(turn_id="turn-a")
        again = self.model_call("turn-a")
        self.assertEqual((self.task_sent(first), self.task_sent(again)), ("code", "code"))
        self.assertEqual(len(self.stub.requests), 1)
        self.stub.reply = lambda body: answer("legal")
        self.assertEqual(self.task_sent(self.send(turn_id="turn-b")), "legal")
        self.assertEqual(len(self.stub.requests), 2)

    def test_A3_the_result_follows_the_turn_id_even_if_the_session_id_changes(self):
        # Hermes starts a new session id when it compresses the history in the middle of a turn
        self.hook(LONG, (PREV,), "turn-c")
        request = {"model": "hyai/auto", "extra_body": {}}
        result = self.ctx.middleware["llm_request"](
            request=request, turn_id="turn-c", session_id="s2", model="hyai/auto"
        )
        self.assertEqual(self.task_sent(result["request"]), "code")

    def test_A3_only_the_latest_turns_are_kept_so_memory_stays_bounded(self):
        for n in range(200):
            self.send(turn_id=f"many-{n}")
        self.assertEqual(self.task_sent(self.model_call("many-199")), "code")
        self.assertIsNone(self.task_sent(self.model_call("many-0")))

    def test_A3_the_hint_goes_into_extra_headers_when_that_transport_is_saved(self):
        self.ctx.set_config(self.mod.CONFIG_KEY, {"task": "auto", "transport": "headers"})
        request = self.send()
        self.assertEqual(request["extra_headers"]["X-HYAI-Task"], "code")
        self.assertNotIn("hyai", request["extra_body"])

    def test_A3_the_rest_of_the_request_is_left_as_it_was(self):
        original = {
            "model": "hyai/auto",
            "messages": [{"role": "user", "content": "x"}],
            "extra_body": {"hyai": {"policy": "cost"}, "other": 1},
            "extra_headers": {"A": "b"},
        }
        self.hook(LONG, (), "t1", platform="cli")
        sent = self.model_call("t1", request=copy.deepcopy(original))
        self.assertEqual(sent["extra_body"], {"hyai": {"policy": "cost", "task": "code"}, "other": 1})
        self.assertEqual(
            {k: v for k, v in sent.items() if k != "extra_body"},
            {k: v for k, v in original.items() if k != "extra_body"},
        )

    def test_A4_only_the_start_of_the_two_user_messages_is_sent(self):
        long_now, long_before = "n" * 500, "p" * 500
        self.hook(
            long_now,
            (long_before, "SECRET ASSISTANT TEXT"),
            "t9",
            conversation_history=[
                {"role": "user", "content": long_before},
                {"role": "assistant", "content": "SECRET ASSISTANT TEXT"},
                {"role": "tool", "content": "SECRET TOOL OUTPUT"},
                {"role": "user", "content": long_now},
            ],
        )
        body = self.stub.requests[0]["body"]
        self.assertEqual(body["state"], {"prompt": "n" * 200, "previous_user": "p" * 200})
        self.assertNotIn("SECRET", json.dumps(body))

    def test_A4_the_first_message_of_a_chat_has_no_previous_text(self):
        self.send(history=())
        self.assertEqual(self.stub.requests[0]["body"]["state"], {"prompt": LONG})

    def test_A4_prompt_text_is_not_logged(self):
        with self.assertNoLogs(level="DEBUG"):
            self.send()
        self.stub.reply = lambda body: (500, {})
        with self.assertLogs(self.mod.logger, level="DEBUG") as logs:
            self.send()
        self.assertNotIn(LONG, "\n".join(logs.output))
        self.assertNotIn(FAKE_KEY, "\n".join(logs.output))

    def test_A5_one_post_to_the_system_one_address_with_the_bearer_key(self):
        self.send()
        (seen,) = self.stub.requests
        self.assertEqual((seen["path"], seen["auth"]), ("/v1/systemone", f"Bearer {FAKE_KEY}"))
        self.assertEqual(_load_module().AUTO_BASE, "https://hostyourai.com/api/decision")

    def test_A5_a_redirect_is_not_followed_so_the_key_never_reaches_the_target(self):
        target = Stub()
        self.addCleanup(target.close)
        self.stub.reply = lambda body: (302, b"", {"Location": target.base + "/v1/systemone"})
        self.assertEqual(self.task_sent(self.send()), "chat")
        self.assertEqual(len(self.stub.requests), 1)
        self.assertEqual(target.requests, [])

    def test_A5_an_oversized_answer_is_refused(self):
        self.stub.reply = lambda body: (200, b"x" * 200_000)
        self.assertEqual(self.task_sent(self.send()), "chat")

    def test_A6_the_task_is_used_only_when_valid_and_at_or_above_the_threshold(self):
        for choice, confidence, expected in [
            ("legal", 0.8, "legal"),
            ("legal", 0.79, "chat"),
            ("reason", 1.0, "reason"),
            ("cheap", 0.99, "chat"),
            ("auto", 0.99, "chat"),
            ("", 0.99, "chat"),
        ]:
            self.stub.reply = lambda body, c=choice, f=confidence: answer(c, f)
            self.assertEqual(self.task_sent(self.send()), expected, (choice, confidence))

    def test_A6_the_question_is_a_choice_over_the_task_values(self):
        self.send()
        question = self.stub.requests[0]["body"]["questions"]["task"]
        self.assertEqual(question["type"], "choice")
        self.assertEqual(tuple(question["criteria"]), self.mod.HYAI_TASKS)

    def test_A7_any_failure_means_chat_and_is_not_retried(self):
        failures = [
            lambda body: (429, {}),
            lambda body: (503, {}),
            lambda body: (401, {}),
            lambda body: (200, b"not json"),
            lambda body: (200, {"answers": {}}),
            lambda body: (200, {"answers": {"task": "code"}}),
            lambda body: (200, {"answers": {"task": {"choice": "code", "confidence": "high"}}}),
            lambda body: (200, {"answers": {"task": {"choice": ["code"], "confidence": 0.99}}}),
            lambda body: (200, {"answers": {"task": {"choice": "code", "confidence": float("nan")}}}),
        ]
        for fail in failures:
            self.stub.requests.clear()
            self.mod._backoff_until = 0
            self.stub.reply = fail
            self.assertEqual(self.task_sent(self.send()), "chat")
            self.assertEqual(len(self.stub.requests), 1)

    def test_A7_a_missing_key_or_a_dead_server_means_chat(self):
        with mock.patch.dict("os.environ", clear=True):
            self.assertEqual(self.task_sent(self.send()), "chat")
        self.assertEqual(self.stub.requests, [])
        self.mod._backoff_until = 0
        self.mod.AUTO_BASE = "http://127.0.0.1:1"
        self.assertEqual(self.task_sent(self.send()), "chat")

    def test_A7_an_older_hermes_without_middleware_still_gets_the_hyai_command(self):
        ctx = OldHermesCtx()
        self.mod.register(ctx)
        self.assertIsNotNone(ctx.handler)
        self.assertEqual(ctx.hooks, {})

    def test_A7_a_hook_that_breaks_never_breaks_the_request(self):
        self.mod._classify = lambda *a, **k: 1 / 0
        self.assertEqual(self.task_sent(self.send()), "chat")
        self.assertIsNone(self.ctx.middleware["llm_request"](request={}, turn_id="none", model="hyai/auto"))

    def test_A8_a_slow_answer_stays_inside_the_time_budget(self):
        self.stub.reply = lambda body: (time.sleep(1.0), answer())[1]
        self.ctx.set_config("hyai_auto", {"timeout_ms": 100})
        start = time.perf_counter()
        self.assertEqual(self.task_sent(self.send()), "chat")
        self.assertLess(time.perf_counter() - start, 0.9)

    def test_A8_a_server_that_trickles_bytes_stays_inside_the_time_budget(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        self.addCleanup(listener.close)

        def trickle():
            conn, _ = listener.accept()
            conn.recv(65536)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n")
            for _ in range(20):  # one byte every 50 ms: no single read ever times out
                conn.sendall(b"x")
                time.sleep(0.05)
            conn.close()

        threading.Thread(target=trickle, daemon=True).start()
        self.mod.AUTO_BASE = f"http://127.0.0.1:{listener.getsockname()[1]}"
        self.ctx.set_config("hyai_auto", {"timeout_ms": 300})
        start = time.perf_counter()
        self.assertEqual(self.task_sent(self.send()), "chat")
        self.assertLess(time.perf_counter() - start, 0.8)

    def test_A8_a_step_the_socket_timeout_does_not_cover_stays_inside_the_budget(self):
        self.mod._classify = lambda *a: time.sleep(1.0)  # stands in for a stuck DNS lookup
        self.ctx.set_config("hyai_auto", {"timeout_ms": 100})
        start = time.perf_counter()
        self.assertEqual(self.task_sent(self.send()), "chat")
        self.assertLess(time.perf_counter() - start, 0.9)

    def test_A9_defaults_apply_when_a_setting_is_missing_or_not_valid(self):
        defaults = self.mod._auto_settings(self.ctx)
        for bad in (
            {"threshold": "x"},
            {"threshold": 2},
            {"threshold": -1},
            {"timeout_ms": 0},
            {"timeout_ms": 99999},
            {"timeout_ms": True},
            {"model": ""},
            {"model": 5},
            {"min_chars": -3},
            {"min_chars": True},
            "nonsense",
        ):
            self.ctx.set_config("hyai_auto", bad)
            self.assertEqual(self.mod._auto_settings(self.ctx), defaults, bad)

    def test_A9_the_configured_model_and_threshold_are_used(self):
        self.ctx.set_config("hyai_auto", {"model": "hyai/nimble-9b", "threshold": 0.95})
        self.stub.reply = lambda body: answer("code", 0.9)
        self.assertEqual(self.task_sent(self.send()), "chat")
        self.assertEqual(self.stub.requests[0]["body"]["model"], "hyai/nimble-9b")

    def test_A10_status_shows_auto_and_the_last_result_but_never_the_prompt(self):
        self.send()
        status = self.ctx.handler("status")
        self.assertIn("task=auto", status)
        self.assertIn("code", status)
        self.assertNotIn(LONG, status)
        self.assertNotIn(PREV, status)
        self.stub.reply = lambda body: answer("code", 0.1)
        self.send()
        self.assertIn("fallback", self.ctx.handler("status"))

    def test_A11_other_turns_send_nothing_and_get_no_hint(self):
        for label, kw in [
            ("subagent", {"platform": "subagent"}),
            ("child session", {"parent_session_id": "parent-1"}),
            ("cron", {"platform": "cron"}),
            ("other model", {"model": "hyai/fast"}),
        ]:
            request = self.send(**kw)
            self.assertEqual(self.stub.requests, [], label)
            self.assertNotIn("hyai", request.get("extra_body", {}), label)
        self.ctx.set_config(self.mod.CONFIG_KEY, {"task": "code"})
        self.send()
        self.assertEqual(self.stub.requests, [])

    def test_A12_too_little_text_gets_chat_without_a_request(self):
        for text, history in [
            ("ok", ()),
            ("", ()),
            ("   ", ("  ",)),
            ("ok", ("hi",)),
            ("yes do it", ("hello",)),
        ]:
            self.assertEqual(self.task_sent(self.send(text, history)), "chat", (text, history))
        self.assertEqual(self.stub.requests, [])

    def test_A12_a_short_reply_after_a_real_question_is_still_classified(self):
        self.send("ok do that", (PREV,))
        self.assertEqual(len(self.stub.requests), 1)

    def test_A12_a_message_that_is_not_text_gets_chat_without_a_request(self):
        self.hook([{"type": "image_url"}], (), "img", conversation_history=[])
        self.assertEqual(self.task_sent(self.model_call("img")), "chat")
        self.assertEqual(self.stub.requests, [])

    def test_A12_only_what_the_user_wrote_counts_as_the_previous_message(self):
        summary = "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted into this."
        history = [
            {"role": "user", "content": PREV},
            {"role": "assistant", "content": "SECRET ASSISTANT TEXT"},
            {"role": "user", "content": summary},
            {"role": "user", "content": "[background job finished]", "display_kind": "notice"},
            {"role": "user", "content": [{"type": "image_url"}]},
            {"role": "user", "content": LONG},
        ]
        self.hook(LONG, (), "t-prev", conversation_history=history)
        self.assertEqual(self.stub.requests[0]["body"]["state"], {"prompt": LONG, "previous_user": PREV})

    def test_A12_a_repeated_message_does_not_hide_the_one_before_it(self):
        self.hook("yes please do it", (PREV, "yes please do it"), "t-twice")
        self.assertEqual(
            self.stub.requests[0]["body"]["state"],
            {"prompt": "yes please do it", "previous_user": "yes please do it"},
        )

    def test_A12_the_current_message_is_not_its_own_previous_message(self):
        # the history can hold the text as the model saw it, not as the user typed it
        history = [{"role": "user", "content": PREV}, {"role": "user", "content": "[voice] " + LONG}]
        self.hook(LONG, (), "t-voice", conversation_history=history)
        self.assertEqual(self.stub.requests[0]["body"]["state"], {"prompt": LONG, "previous_user": PREV})

    def test_A12_the_minimum_is_a_setting(self):
        self.ctx.set_config("hyai_auto", {"min_chars": 5})
        self.send("ok do it", ())
        self.assertEqual(len(self.stub.requests), 1)

    def test_A13_after_a_failure_nothing_is_sent_for_a_while_and_then_it_tries_again(self):
        self.stub.reply = lambda body: (503, {})
        self.assertEqual(self.task_sent(self.send()), "chat")
        self.assertEqual(len(self.stub.requests), 1)
        self.stub.reply = lambda body: answer("legal")
        self.assertEqual(self.task_sent(self.send()), "chat")
        self.assertEqual(len(self.stub.requests), 1)  # still paused
        self.mod._backoff_until = time.monotonic() - 1  # the pause is over
        self.assertEqual(self.task_sent(self.send()), "legal")
        self.assertEqual(len(self.stub.requests), 2)

    def test_A13_the_pause_is_60_seconds_plus_up_to_a_quarter_at_random(self):
        self.stub.reply = lambda body: (503, {})
        pauses = set()
        for _ in range(30):
            self.mod._backoff_until = 0
            before = time.monotonic()
            self.send()
            pauses.add(self.mod._backoff_until - before)
        self.assertTrue(all(60 <= pause <= 75.5 for pause in pauses), pauses)
        self.assertGreater(len(pauses), 1)  # not the same every time

    def test_A13_an_earlier_pause_is_never_shortened(self):
        self.mod._backoff_until = time.monotonic() + 600
        kept = self.mod._backoff_until
        self.mod._pause()  # a second failure while a long pause is running
        self.assertEqual(self.mod._backoff_until, kept)

    def test_A13_a_timeout_does_not_pause(self):
        self.stub.reply = lambda body: (time.sleep(0.4), answer())[1]
        self.ctx.set_config("hyai_auto", {"timeout_ms": 100})
        self.assertEqual(self.task_sent(self.send()), "chat")
        self.assertEqual(self.mod._backoff_until, 0)
        self.stub.reply = lambda body: answer()
        self.assertEqual(self.task_sent(self.send()), "code")

    def test_A13_a_dead_server_pauses(self):
        self.mod.AUTO_BASE = "http://127.0.0.1:1"
        self.send()
        self.assertGreater(self.mod._backoff_until, time.monotonic())

    def test_A13_a_bad_answer_pauses_but_low_confidence_does_not(self):
        self.stub.reply = lambda body: (
            200,
            {"answers": {"task": {"choice": "nonsense", "confidence": 0.99}}},
        )
        self.send()
        self.assertGreater(self.mod._backoff_until, time.monotonic())
        self.mod._backoff_until = 0
        self.stub.reply = lambda body: answer("code", 0.1)
        self.send()
        self.assertEqual(self.mod._backoff_until, 0)

    def test_A13_a_working_service_is_never_paused(self):
        self.stub.reply = lambda body: answer("code", 0.1)  # low confidence is a normal answer
        self.send()
        self.send("Hi", ())  # too short is not a failure either
        self.stub.reply = lambda body: answer("legal")
        self.assertEqual(self.task_sent(self.send()), "legal")

    def test_A13_a_missing_key_does_not_start_a_pause(self):
        with mock.patch.dict("os.environ", clear=True):
            self.send()
        self.assertEqual(self.task_sent(self.send()), "code")

    def test_A13_a_skipped_message_shows_as_a_fallback_in_status(self):
        self.stub.reply = lambda body: (503, {})
        self.send()
        self.send()
        self.assertIn("fallback", self.ctx.handler("status"))


if __name__ == "__main__":
    unittest.main()
