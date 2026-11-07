"""Tests for the generic decision client, against a local stub of the System One API.

Everything invented. The stub plays HostYourAI; no request leaves the machine.
"""

from __future__ import annotations

import http.server
import importlib.util
import json
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

_MODULE_PATH = Path(__file__).resolve().parent.parent / "hostyourai" / "decision_client.py"


def _load_module():
    mod_name = "decision_client_under_test"
    sys.modules.pop(mod_name, None)
    spec = importlib.util.spec_from_file_location(mod_name, _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dc = _load_module()

FAKE_KEY = "not-a-real-credential-placeholder"


class Stub:
    """A System One server. `reply` is a function (request body) -> (status, body) and may sleep."""

    def __init__(self):
        self.requests: list[dict] = []
        self.reply = lambda body: (200, {"answers": {}})
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
                    pass

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.server.server_port}/v1/systemone"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class DecisionClientTests(unittest.TestCase):
    def setUp(self):
        self.stub = Stub()
        self.addCleanup(self.stub.close)
        env = mock.patch.dict("os.environ", {"HOSTYOURAI_API_KEY": FAKE_KEY})
        env.start()
        self.addCleanup(env.stop)

    def test_rejects_malformed_questions_without_a_network_call(self):
        for bad in ({}, {"q": "not-a-dict"}, {"q": {"type": "unknown"}}, "nonsense"):
            with self.assertRaises(ValueError):
                dc.ask(bad, {}, url=self.stub.base)
        self.assertEqual(self.stub.requests, [])

    def test_default_model_is_decider_2b_without_env_override(self):
        dc.ask({"q": {"type": "noul", "instructions": "x"}}, {}, url=self.stub.base)
        self.assertEqual(self.stub.requests[-1]["body"]["model"], dc.DEFAULT_MODEL)

    def test_env_var_overrides_default_model(self):
        with mock.patch.dict("os.environ", {"HOSTYOURAI_DECISION_MODEL": "hyai/some-newer-model"}):
            dc.ask({"q": {"type": "noul", "instructions": "x"}}, {}, url=self.stub.base)
        self.assertEqual(self.stub.requests[-1]["body"]["model"], "hyai/some-newer-model")

    def test_explicit_model_overrides_env_var(self):
        with mock.patch.dict("os.environ", {"HOSTYOURAI_DECISION_MODEL": "hyai/some-newer-model"}):
            dc.ask(
                {"q": {"type": "noul", "instructions": "x"}},
                {},
                url=self.stub.base,
                model="hyai/explicit-choice",
            )
        self.assertEqual(self.stub.requests[-1]["body"]["model"], "hyai/explicit-choice")

    def test_missing_key_raises_without_a_network_call(self):
        with mock.patch.dict("os.environ", clear=True):
            with self.assertRaises(dc.DecisionError):
                dc.ask({"q": {"type": "noul", "instructions": "x"}}, {}, url=self.stub.base)
        self.assertEqual(self.stub.requests, [])

    def test_a_noul_round_trip(self):
        self.stub.reply = lambda body: (200, {"answers": {"flag": {"noul": 0.87}}})
        answers = dc.ask(
            {"flag": {"type": "noul", "instructions": "worth a look?"}},
            {"item": "widget"},
            url=self.stub.base,
        )
        self.assertEqual(answers["flag"]["noul"], 0.87)
        (seen,) = self.stub.requests
        self.assertEqual(seen["auth"], f"Bearer {FAKE_KEY}")
        self.assertEqual(seen["body"]["questions"]["flag"]["type"], "noul")
        self.assertEqual(seen["body"]["state"], {"item": "widget"})
        self.assertIsNone(dc.confidence_of(answers["flag"]))

    def test_a_choice_round_trip_and_confidence_helper(self):
        self.stub.reply = lambda body: (
            200,
            {"answers": {"c": {"choice": "a", "confidence": 0.95, "probabilities": {"a": 0.95, "b": 0.05}}}},
        )
        answers = dc.ask(
            {"c": {"type": "choice", "instructions": "pick", "criteria": {"a": "...", "b": "..."}}},
            {},
            url=self.stub.base,
        )
        self.assertEqual(answers["c"]["choice"], "a")
        self.assertEqual(dc.confidence_of(answers["c"]), 0.95)

    def test_multiple_questions_in_one_call(self):
        self.stub.reply = lambda body: (
            200,
            {"answers": {"q1": {"noul": 0.1}, "q2": {"noul": 0.9}}},
        )
        answers = dc.ask(
            {
                "q1": {"type": "noul", "instructions": "a?"},
                "q2": {"type": "noul", "instructions": "b?"},
            },
            {"x": 1},
            url=self.stub.base,
        )
        self.assertEqual((answers["q1"]["noul"], answers["q2"]["noul"]), (0.1, 0.9))
        self.assertEqual(len(self.stub.requests), 1)

    def test_a_redirect_is_not_followed(self):
        target = Stub()
        self.addCleanup(target.close)
        self.stub.reply = lambda body: (302, b"", {"Location": target.base})
        with self.assertRaises(dc.DecisionError):
            dc.ask({"q": {"type": "noul", "instructions": "x"}}, {}, url=self.stub.base)
        self.assertEqual(target.requests, [])

    def test_an_oversized_answer_is_refused(self):
        self.stub.reply = lambda body: (200, b"x" * (dc.MAX_ANSWER_BYTES + 1))
        with self.assertRaises(dc.DecisionError):
            dc.ask({"q": {"type": "noul", "instructions": "x"}}, {}, url=self.stub.base)

    def test_non_json_and_malformed_response_raise(self):
        for reply in (
            lambda body: (200, b"not json"),
            lambda body: (200, {"answers": "not-a-dict"}),
            lambda body: (200, {"no_answers_key": True}),
        ):
            self.stub.reply = reply
            with self.assertRaises(dc.DecisionError):
                dc.ask({"q": {"type": "noul", "instructions": "x"}}, {}, url=self.stub.base)

    def test_http_error_statuses_raise_without_leaking_the_body(self):
        self.stub.reply = lambda body: (401, {"error": "SECRET DETAIL"})
        try:
            dc.ask({"q": {"type": "noul", "instructions": "x"}}, {}, url=self.stub.base)
            self.fail("expected DecisionError")
        except dc.DecisionError as exc:
            self.assertEqual(str(exc), "HTTP 401")
            self.assertNotIn("SECRET", str(exc))

    def test_timeout_budget_is_enforced_even_when_the_socket_timeout_would_not_cover_it(self):
        self.stub.reply = lambda body: (time.sleep(1.0), (200, {"answers": {"q": {"noul": 0.5}}}))[1]
        start = time.perf_counter()
        with self.assertRaises(dc.DecisionError):
            dc.ask({"q": {"type": "noul", "instructions": "x"}}, {}, url=self.stub.base, timeout_ms=100)
        self.assertLess(time.perf_counter() - start, 0.9)

    def test_429_and_503_are_retried_up_to_the_limit_then_raise(self):
        self.stub.reply = lambda body: (503, {})
        start = time.perf_counter()
        with self.assertRaises(dc.DecisionError):
            dc.ask(
                {"q": {"type": "noul", "instructions": "x"}},
                {},
                url=self.stub.base,
                retries=2,
                retry_sleep_s=0.01,
            )
        self.assertEqual(len(self.stub.requests), 3)  # 1 + 2 retries
        self.assertLess(time.perf_counter() - start, 1.0)

    def test_a_retry_that_eventually_succeeds_returns_the_good_answer(self):
        calls = {"n": 0}

        def reply(body):
            calls["n"] += 1
            if calls["n"] < 2:
                return (503, {})
            return (200, {"answers": {"q": {"noul": 0.5}}})

        self.stub.reply = reply
        answers = dc.ask(
            {"q": {"type": "noul", "instructions": "x"}},
            {},
            url=self.stub.base,
            retries=2,
            retry_sleep_s=0.01,
        )
        self.assertEqual(answers["q"]["noul"], 0.5)

    def test_a_non_retryable_status_raises_immediately(self):
        self.stub.reply = lambda body: (400, {})
        with self.assertRaises(dc.DecisionError):
            dc.ask(
                {"q": {"type": "noul", "instructions": "x"}},
                {},
                url=self.stub.base,
                retries=5,
                retry_sleep_s=0.01,
            )
        self.assertEqual(len(self.stub.requests), 1)

    def test_a_score_round_trip(self):
        self.stub.reply = lambda body: (
            200,
            {"answers": {"s": {"score": "major", "confidence": 0.7, "legend": ["minor", "major"]}}},
        )
        answers = dc.ask(
            {"s": {"type": "score", "instructions": "how bad?", "criteria": ["minor", "major"]}},
            {},
            url=self.stub.base,
        )
        self.assertEqual(answers["s"]["score"], "major")
        self.assertEqual(dc.confidence_of(answers["s"]), 0.7)

    def test_confidence_of_edge_cases(self):
        self.assertIsNone(dc.confidence_of({}))
        self.assertIsNone(dc.confidence_of({"confidence": None}))
        self.assertIsNone(dc.confidence_of({"confidence": "0.9"}))  # string, not a number
        self.assertIsNone(dc.confidence_of({"confidence": True}))  # bool excluded explicitly
        self.assertIsNone(dc.confidence_of({"confidence": float("nan")}))
        self.assertIsNone(dc.confidence_of({"confidence": float("inf")}))
        self.assertIsNone(dc.confidence_of("not-a-dict"))
        self.assertEqual(dc.confidence_of({"confidence": 0}), 0)  # falsy-but-valid number

    def test_custom_env_var_name_is_honored(self):
        with mock.patch.dict("os.environ", {"OTHER_KEY": FAKE_KEY}, clear=True):
            self.stub.reply = lambda body: (200, {"answers": {"q": {"noul": 0.5}}})
            dc.ask(
                {"q": {"type": "noul", "instructions": "x"}},
                {},
                url=self.stub.base,
                env_var="OTHER_KEY",
            )
            (seen,) = self.stub.requests
            self.assertEqual(seen["auth"], f"Bearer {FAKE_KEY}")

    def test_whitespace_only_key_is_treated_as_missing(self):
        with mock.patch.dict("os.environ", {"HOSTYOURAI_API_KEY": "   "}, clear=True):
            with self.assertRaises(dc.DecisionError):
                dc.ask({"q": {"type": "noul", "instructions": "x"}}, {}, url=self.stub.base)
        self.assertEqual(self.stub.requests, [])

    def test_default_retries_is_zero_so_a_503_raises_on_first_attempt(self):
        self.stub.reply = lambda body: (503, {})
        with self.assertRaises(dc.DecisionError):
            dc.ask({"q": {"type": "noul", "instructions": "x"}}, {}, url=self.stub.base)
        self.assertEqual(len(self.stub.requests), 1)  # no retries by default

    def test_connection_refused_raises_decision_error_without_leaking_detail(self):
        # Nothing is listening on this port once the stub that briefly owned it is closed.
        throwaway = Stub()
        dead_url = throwaway.base
        throwaway.close()
        with self.assertRaises(dc.DecisionError):
            dc.ask({"q": {"type": "noul", "instructions": "x"}}, {}, url=dead_url, timeout_ms=500)


if __name__ == "__main__":
    unittest.main()
