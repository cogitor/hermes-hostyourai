---
name: hyai-decide
description: "Use when a task needs a cheap, bounded classify/score/yes-no judgment — tagging items, rating severity/priority, routing/gating, or batch-labeling many things fast — instead of a full reasoning turn. Triggers on requests like 'tag/categorize these', 'cheapest way to classify/sort a batch', 'is this urgent/severe', 'should this go to review'."
version: 0.1.0
platforms: [linux]
---

# HYAI Decide: typed decisions via HostYourAI

A decision model answers one narrow, pre-defined question about some text — "which
category", "how severe", "yes or no" — and returns a typed answer plus a confidence
score, in a few hundred milliseconds, for a fraction of the cost of a full reasoning
call. It is not a chat model: it cannot discuss, explain its reasoning in prose, or
handle a question whose criteria you can't write down in advance. Use it to offload
the bounded, repeatable sliver of a pipeline — not the judgment-call part.

## When to use this skill

- **Classify**: assign one label out of a fixed, named set (e.g. task type, topic,
  priority tier). Includes bulk/batch tagging — "tag these 50 tickets", "sort this
  batch of postings into categories", "cheapest way to label a pile of X".
- **Score**: rate something on an ordered scale (e.g. severity 0-3, relevance low/
  medium/high, "how urgent/critical is this").
- **Route / gate (yes-no)**: a single binary question that decides which branch a
  pipeline takes next (e.g. "does this need a human review?", "should this escalate?") —
  called `noul` in the API.
- **Verify**: check one well-defined property of a candidate answer before trusting it
  (e.g. "does this summary contradict the source text?").

If a request mentions tagging, categorizing, scoring, rating, or routing many items
cheaply/quickly/in bulk — even without the words "classify" or "decision model" — that's
this skill, not a from-scratch reasoning pass over each item.

Each needs criteria you can write down before seeing the specific input.

## When NOT to use this skill

- **Open-ended reasoning, synthesis, or novel trade-offs.** If you'd need to explain
  *why* in prose, or weigh several considerations against each other, that's a full
  reasoning-model turn, not a typed decision.
- **The deciding fact isn't in the text you can send.** A decision model grades only
  what you hand it. If the real answer lives behind a login wall, a JS-rendered page you
  only have static HTML for, or logic you haven't captured in the input, the model will
  guess near-uniformly and report low confidence while looking calm about it. Fix the
  input pipeline before blaming the primitive.
- **Data too sensitive to leave the local agent.** See the privacy section below.
- **One-off, never-repeated question.** The setup cost (writing criteria, testing a
  threshold) only pays off for a judgment you'll run more than a few times.

## Privacy and vendor disclosure — read before sending anything

This skill calls **HostYourAI**'s System One decision API
(`https://hostyourai.com/api/decision/v1/systemone`), a distinct vendor from
TypeSafe/Jev or any other decision-model provider you may also have configured — do not
assume the same privacy posture applies to both, and if more than one vendor is
available in the same session, be explicit about which one a given call used.

HostYourAI's stated terms for this endpoint: zero data retention by default for
request/response content; only technical usage metadata (model, token counts, latency,
IP) is kept, for a limited retention window. Inference runs on EU-based infrastructure;
some underlying capacity may come from third-party GPU marketplaces unless a stricter
"sovereignty" mode is requested and available on your account.

Regardless of vendor trust level, apply this **minimization rule** to every call:
- Send only the named fields the specific judgment needs — never one large blob with
  everything in it "just in case".
- Strip or redact direct identifiers (names, emails, addresses) from `state` unless the
  judgment specifically needs them and your own data-handling policy allows it.
- Never put secrets, API keys, or credentials in `state`.
- If the judgment inherently requires content too sensitive to send externally at all
  (per your own data-handling policy), do the judgment with the local/primary agent
  model instead of this tool — don't force a private judgment through an external API
  just to save tokens. This is the one case "When NOT to use this skill" refers to.

## The three question types

| Type   | Use for                        | `criteria`                          | Answer fields |
|--------|---------------------------------|--------------------------------------|---------------|
| choice | pick one label from a fixed set | dict: `{label: "one-sentence description"}` | `choice`, `confidence` (0-1), `probabilities` |
| score  | rate on an ordered scale         | ordered list, low → high: `["minor", "moderate", "severe"]` | `score`, `confidence` (0-1), `legend`, `probabilities` |
| noul   | yes/no, binary gate              | none — put the whole question in `instructions` | `noul` (probability 0-1) — **no `confidence` field**; gate on the probability itself |

Question IDs (the keys of the `questions` dict) are never sent to the model — all
meaning the model needs must be in `instructions`/`criteria`, not implied by the ID.
Probe with one minimal real call before trusting any shape above — vendor APIs drift.

## How to call it

This plugin ships a small stdlib-only Python module,
`hostyourai/decision_client.py`, usable directly via `execute_code`/`terminal`. Set
`HOSTYOURAI_API_KEY` in the environment first — `ask()` raises `DecisionError`
immediately if it's missing, without making a network call.

```python
import os
import sys

# Default Hermes plugin install path (adjust if yours differs):
sys.path.insert(0, os.path.expanduser("~/.hermes/plugins/hostyourai"))
from hostyourai.decision_client import ask, confidence_of

answers = ask(
    questions={
        "flag": {
            "type": "noul",
            "instructions": "Does this job posting mention a visa/relocation requirement?",
        }
    },
    state={"posting_snippet": "..."},  # named fields only — see minimization rule above
    model="hyai/decider-2b",  # a small, cheap decision model; see note below
    timeout_ms=1500,  # default is 2000; tightened here for a live request path
)
p = answers["flag"]["noul"]  # 0-1 probability, gate directly on this
```

For a Choice or Score question, read `answers[id]["choice"]`/`["score"]` and
`confidence_of(answers[id])` for the 0-1 confidence.

For batch/background use (a cron job, not a live request a human is waiting on), pass
`retries=2` or so with a short `retry_sleep_s` — see Error handling below for when to
retry vs. fail open.

`model` defaults to `hyai/decider-2b`, a small classifier model (verify against
`GET /v1/models`, not wrapped by this client, for what's currently warm before
committing to one in a request path). Larger models on the same endpoint may do
better on harder judgments at higher cost/latency.

## Interpreting confidence

Confidence (Choice/Score) is "how concentrated the probability mass is on the top
answer" — not a correctness guarantee. A recommended banding pattern:

- **High confidence (e.g. ≥ 0.8-0.9, tune per use case)** → act on the answer directly.
- **Mid confidence** → use the answer but flag it for review, or route to a larger/
  slower model for a second opinion ("verify-and-escalate").
- **Low confidence** → do not act automatically; this often means the deciding signal
  wasn't present in `state` (see "When NOT to use this skill" above), not that the task
  is inherently hard.

Noul has no confidence field; treat the probability itself the same way (e.g. ≥0.85 →
act, 0.5-0.85 → review, below → treat as "no").

**Always validate a new threshold against a small labelled fixture set** before
trusting it in production — see the Validation footnote below.

## Error handling

- Code — not the model — owns thresholds, side effects, state changes, and audit
  logging. The decision model only ever returns an opinion; never let its output alone
  perform an irreversible action.
- Decide per use case whether to fail open (treat an error as "proceed without the
  hint", e.g. a cheap task-routing nicety) or fail closed (treat an error as "escalate
  to a human / use the safe default", e.g. a moderation gate). `ask()` raises
  `DecisionError` on any service failure (bad key, timeout, bad response, non-retryable
  HTTP status) and `ValueError` for a malformed `questions` dict (a caller bug, not a
  service failure) — catch them separately.
- A cold model can 503 for several minutes; a live request-path caller should not wait
  for it — set a tight `timeout_ms`, catch `DecisionError`, and fall back. A batch/cron
  caller can afford `retries` with a sleep.

## Batching rules

- Multiple **questions about the same state** in one call is fine and often cheaper
  than separate calls — the model answers all of them against the same `state` object.
- Multiple **items** batched into one call is a known pitfall: putting several
  candidates into one shared array field (e.g. `state["candidates"] = [...]`) causes the
  model to cross-reference them, contaminating each individual judgment (observed in
  informal testing: identical pairwise-similarity judgments scoring very differently in
  isolation vs. in a shared-array batch of many). If you need to judge N separate items, give each its own
  **named** field (`item0`, `item1`, ...) and scope each question's `instructions` to
  "consider only `itemN`" — this keeps the one-call cost win without the cross-
  contamination bug. Keep such batches to a modest size (tens, not hundreds) — see
  Validation footnote below for recalibrating after any change.

## Worked examples

**1. Routing (Choice)** — pick a task category for a user message. (This pattern is
what this plugin's own `/hyai task auto` command uses internally.)
```python
ask(
    questions={
        "task": {
            "type": "choice",
            "instructions": "Which kind of task is the user asking for?",
            "criteria": {"code": "writing or reviewing code", "chat": "casual conversation"},
        }
    },
    state={"prompt": "fix the bug in my parser"},
)
```

**2. Severity triage (Score)**:
```python
ask(
    questions={
        "severity": {
            "type": "score",
            "instructions": "How severe is the issue described in `report`?",
            "criteria": ["cosmetic", "minor", "major", "critical"],
        }
    },
    state={"report": "login page returns a 500 for all users"},
)
```

**3. Review gate (Noul)**:
```python
ask(
    questions={
        "needs_review": {
            "type": "noul",
            "instructions": "Does `comment` contain a complaint that needs a human reply?",
        }
    },
    state={"comment": "..."},
)
```

## Validation footnote

Before trusting any new judgment's threshold in production: build a small labelled
fixture script (10-50 invented or real examples with known right answers), run it
against the live API, and record accuracy per class/band plus the confidence
distribution. Re-run this whenever you change the model, the wording of `instructions`/
`criteria`, or the batch size — small wording changes can shift calibration.
