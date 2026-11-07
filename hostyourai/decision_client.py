"""Generic HostYourAI System One decision client.

A small, reusable wrapper around HostYourAI's typed-decision API (Choice / Score / Noul
questions against a System One model) — the same primitive that powers this plugin's
``/hyai task auto`` feature, generalized to arbitrary questions and state instead of one
hardcoded task-classification call.

Not coupled to Hermes internals: this module only needs ``HOSTYOURAI_API_KEY`` in the
environment and plain stdlib networking, so it can be called from the plugin, from a
skill's shell/Python snippet via ``terminal``/``execute_code``, or from an unrelated script.

    from hostyourai.decision_client import ask
    answers = ask(
        questions={"flag": {"type": "noul", "instructions": "Is this worth a human look?"}},
        state={"item": "..."},
    )
    answers["flag"]["noul"]  # probability 0-1, no separate confidence field for noul
    # Choice/Score answers additionally carry answers[id]["confidence"] (0-1).

Design notes (carried over from the ``task auto`` implementation and from empirical
findings in local use — see hyai-decide skill for the full write-up):
  - A thread-join timeout is used instead of relying solely on the socket timeout: a slow
    DNS lookup or a byte-at-a-time trickle response is not reliably covered by
    ``timeout=`` on urllib alone.
  - Redirects are never followed — a redirect could forward the bearer key to another host.
  - The answer body is capped in size before JSON-decoding it.
  - Error messages never include the request/response body (which could hold prompt text
    or, in a crafted error page, reflect the key) — only exception types are surfaced.
  - 429/503 (rate limit / cold model) are the only statuses worth a bounded retry with
    backoff; everything else should fail fast so a caller on a request-path budget doesn't
    stall silently.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_URL = "https://hostyourai.com/api/decision/v1/systemone"
DEFAULT_MODEL = "hyai/decider-2b"
MAX_ANSWER_BYTES = 65_536


def _default_model() -> str:
    """The model ``ask()`` uses when the caller doesn't pass one explicitly.

    Reads ``HOSTYOURAI_DECISION_MODEL`` from the environment at call time (not import
    time), falling back to ``DEFAULT_MODEL``. The decision-model landscape moves fast —
    this lets a deployment point at a newer/better System One model without a code change,
    the same way ``HOSTYOURAI_API_KEY`` is read from the environment rather than hardcoded.
    """
    return os.environ.get("HOSTYOURAI_DECISION_MODEL", "").strip() or DEFAULT_MODEL


class DecisionError(Exception):
    """The service failed, answered badly, or ran out of time.

    The message never contains the API key, request state, or raw response body —
    only a short, generic description (exception type, HTTP status, or a fixed string).
    """

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status  # HTTP status code, if this came from an HTTPError; else None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a redirect would forward the Authorization header to another host


_OPENER = urllib.request.build_opener(_NoRedirect)


def _api_key(env_var: str = "HOSTYOURAI_API_KEY") -> str:
    key = os.environ.get(env_var, "").strip()
    if not key:
        raise DecisionError(f"no {env_var} set")
    return key


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate_questions(questions: dict) -> None:
    if not isinstance(questions, dict) or not questions:
        raise ValueError("questions must be a non-empty dict of id -> question spec")
    for qid, spec in questions.items():
        if not isinstance(spec, dict) or spec.get("type") not in ("choice", "score", "noul"):
            raise ValueError(f"question {qid!r} must have type one of choice/score/noul")


def _call_once(
    questions: dict,
    state: dict,
    *,
    model: str,
    url: str,
    api_key: str,
    timeout_s: float,
) -> dict:
    body = json.dumps({"model": model, "state": state, "questions": questions}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with _OPENER.open(req, timeout=timeout_s) as resp:
            raw = resp.read(MAX_ANSWER_BYTES + 1)
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        raise DecisionError(f"HTTP {status}", status=status) from None
    except Exception as exc:  # network/timeout/TLS: text could leak the key, log type only
        raise DecisionError(type(exc).__name__) from None
    if len(raw) > MAX_ANSWER_BYTES:
        raise DecisionError("answer too large")
    try:
        payload = json.loads(raw)
    except ValueError:
        raise DecisionError("answer was not valid JSON") from None
    answers = payload.get("answers") if isinstance(payload, dict) else None
    if not isinstance(answers, dict):
        raise DecisionError("unexpected response shape")
    return answers


def _call_with_hard_timeout(
    questions: dict,
    state: dict,
    *,
    model: str,
    url: str,
    api_key: str,
    timeout_s: float,
) -> dict:
    """``_call_once``, but never later than ``timeout_s`` wall-clock, even if the socket
    timeout alone wouldn't cover it (slow DNS, a byte-at-a-time trickle). Runs the real
    call in a daemon thread and gives up on it — the thread may still finish in the
    background, but the caller is never blocked past the budget."""
    result: dict[str, Any] = {}

    def work():
        try:
            result["answers"] = _call_once(
                questions, state, model=model, url=url, api_key=api_key, timeout_s=timeout_s
            )
        except Exception as exc:
            result["error"] = exc

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        raise DecisionError("TimeoutError")
    if "error" in result:
        raise result["error"]
    return result["answers"]


def ask(
    questions: dict,
    state: dict,
    *,
    model: str | None = None,
    url: str = DEFAULT_URL,
    timeout_ms: int = 2000,
    retries: int = 0,
    retry_sleep_s: float = 2.0,
    env_var: str = "HOSTYOURAI_API_KEY",
) -> dict:
    """Ask HostYourAI's System One model one or more typed questions about ``state``.

    ``model``: defaults to ``HOSTYOURAI_DECISION_MODEL`` from the environment, or
    ``DEFAULT_MODEL`` if that isn't set — see ``_default_model()``. Pass an explicit
    model to override both for this one call.

    ``questions``: dict of question-id -> {"type": "choice"|"score"|"noul", "instructions": str,
      "criteria": ...}. Question ids are never sent to the model; put all meaning the model
      needs into ``instructions``/``criteria``. Keep ``state`` to named fields the specific
      judgment needs (see the hyai-decide skill's privacy-minimization rule) rather than one
      free-text blob with everything in it.

    ``retries``: for request-path use (a human is waiting), leave this at 0 and set a tight
    ``timeout_ms`` — a slow or cold model should fail open, not stall the caller. For batch/
    background use, pass ``retries`` > 0 to ride out transient 429 (rate limit) and 503
    (cold model) responses with a short sleep between attempts.

    Raises ``DecisionError`` on any failure (bad/missing key, network error, timeout,
    malformed response, or a non-retryable HTTP status). Raises ``ValueError`` for a caller
    bug (malformed ``questions``) rather than a service failure, so callers can tell the two
    apart.
    """
    _validate_questions(questions)
    api_key = _api_key(env_var)
    resolved_model = model if model is not None else _default_model()
    timeout_s = timeout_ms / 1000
    attempt = 0
    while True:
        try:
            return _call_with_hard_timeout(
                questions, state, model=resolved_model, url=url, api_key=api_key, timeout_s=timeout_s
            )
        except DecisionError as exc:
            retryable = exc.status in (429, 503)
            if not retryable or attempt >= retries:
                raise
            attempt += 1
            time.sleep(retry_sleep_s)


def confidence_of(answer: dict) -> float | None:
    """The 0-1 confidence of a Choice/Score answer, or None for Noul (which has none) or a
    malformed answer. Treat this as "how concentrated the probability mass is", not a
    correctness guarantee — validate any threshold against a labelled fixture set first."""
    value = answer.get("confidence") if isinstance(answer, dict) else None
    return value if _is_number(value) else None
