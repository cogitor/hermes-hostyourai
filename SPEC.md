# /hyai behaviour spec

What `/hyai` must do. Every rule has an ID; `tests/test_spec_coverage.py` fails if a rule ID is
not mentioned in a test. Name the ID in the test's docstring or comment.

`/hyai` never reads the chat's currently selected model; every subcommand always applies.
Whether the saved setting actually does anything is decided solely by the `hostyourai`
provider profile at request time (see R11): the setting is silently inert on any model other
than hyai/auto.

## Routing setting

- **R1** `/hyai` shows the saved task and policy, then the list of choices. Works on any model.
- **R2** `/hyai status` shows the saved task and policy only. Works on any model.
- **R4** `/hyai task <value>` and `/hyai policy <value>` save one value, lowercased, and keep
  the other. Values come from the fixed lists; anything else saves nothing and shows usage.
- **R5** Shorthand `/hyai <word> [<word>]`: task and policy words in any order. At most one of
  each. Words are the exact task and policy values (no aliases). Any
  unknown word, or two of the same kind, saves nothing and shows usage.
- **R6** `/hyai off` and `/hyai clear` remove task and policy. A hand-set `transport` stays.
- **R8** An unknown subcommand shows usage and never changes the setting.
- **R9** `/hyai help` shows usage. Works on any model.
- **R10** The word "transport" never appears in any reply.
- **R11** The saved setting is sent only to hyai/auto, by the provider profile (strict match).
  This is the only gate on hyai/auto in the whole plugin; `/hyai` itself has none.

## Usage (`/hyai usage`)

- **U1** Works on any model. Never changes the saved setting.
- **U2** Shows balance, spend this month, auto top-up on/off, and "as of" in local time
  (`Jan 01, 13:00`). Amounts look like `€12.34`.
- **U3** Only in a direct message, the local CLI, or the Hermes dashboard. A group, a channel,
  an unknown messaging chat type, or a lost messaging session context is refused, and no network
  request is made.
- **U4** One `GET /balance` per call, with the key as a Bearer header, to the HostYourAI API
  address only. Redirects are never followed. The body is capped at 2 MB. Timeout 5 s.
- **U5** The answer is remembered for 60 s, per API key. Failures are not remembered.
  Simultaneous calls make one request.
- **U6** A balance below `usage_warn_eur` (default 5) adds a low-balance line. 0 turns it off.
  A setting that is not a non-negative number means the default. Unlimited accounts never warn.
- **U7** A missing, negative, infinite or non-numeric amount is shown as €0.00.
  A missing or unparseable "as of" is left out.
- **U8** Every failure is one plain line starting "HostYourAI balance unavailable:". It never
  contains the key, a URL or an exception text. Cases: no key, key rejected (401), team key
  (403), rate limited (429, with the wait if given), other HTTP status, unreachable, and a
  reply that is not a JSON object.
- **U9** The command never raises: a malformed reply cannot make the text fall through to
  the LLM.

## Auto task

`/hyai task auto` asks a small HostYourAI System One model to pick the task for each user
message, instead of one task for every chat.

How it hooks in (Hermes 0.21.7 and later, no core change): a `pre_llm_call` hook runs once per
user message (its `user_message` is the original text) and does the classifying. An
`llm_request` middleware adds the result to each model call of that message, in
`extra_body`/`extra_headers` only. A failure in either leaves the request unchanged.

- **A1** `/hyai task auto` saves `auto` as the task like any other task value (R4). It is off
  until the user sets it. `/hyai off` clears it, and so does any other task value.
- **A2** The hint is added only when the saved task is `auto` and the model is hyai/auto. The
  policy is sent as usual.
- **A3** The classifier runs once per user message, in `pre_llm_call`. The result is kept under the
  turn id (not the session id, which Hermes can change when it compresses) and applied to every
  model call of that turn. Only the latest few turns are kept.
- **A4** It sends two texts: the first 200 characters of the user's message and the first 200
  characters of the user message before it (if any). Never assistant text, tool output or
  files. Prompts are not written to logs.
- **A5** The request goes to the HostYourAI System One address with the same key as the router,
  never anywhere else. Redirects are not followed. The answer body is capped.
- **A6** The question is a Choice over the eight task values. The task is used only if the chosen
  value is valid and its confidence is at least `hyai_auto.threshold` (default 0.8).
- **A7** Anything else means task `chat`: any failure (see A13), a missing key, a confidence below
  the threshold. No retry. On a Hermes without middleware support `/hyai` still works and auto is
  not registered.
- **A8** The time budget is `hyai_auto.timeout_ms` (default 1000) for the whole call, including steps the socket timeout does not cover (name lookup, a slow trickle).
- **A9** The model is `hyai_auto.model` (default `hyai/decider-2b`). Defaults are used when a setting
  is missing or not valid.
- **A10** `/hyai` and `/hyai status` show `task=auto`, and the last result when there is one
  (the task picked and whether it was a fallback). Never any prompt text.
- **A11** These turns are never sent and get no auto hint at all (the saved setting applies as
  before): any model other than hyai/auto, a subagent turn, a cron turn, and a pinned task
  (a task other than `auto`).
- **A12** Too little text is not sent either and gets `chat`: when the current and the previous
  message, trimmed and cut as in A4, add up to fewer than `hyai_auto.min_chars` (default 20)
  characters. This includes an empty or whitespace-only message and a non-text message. Only text
  the user wrote counts as the previous message: not Hermes' own notes in the history (the
  compaction summary, `display_kind` rows) and not messages made of parts such as images.
- **A13** After an error of the service (an HTTP error including 429 and 503, a refused or
  failed connection, an oversized or unreadable answer, a choice or confidence that is not valid)
  auto sends nothing for 60 seconds, plus a random 0 to 25 percent so several processes do not
  retry together, and gives `chat`. A timeout does not start the pause: that message gets `chat`
  and the next one tries again. Low confidence, a too-short message and a missing key do not
  start the pause either.

## `hyai-decide` skill and `decision_client.ask()`

A bundled, generic skill wrapping HostYourAI's System One typed-decision API
(`decision_client.py`) for arbitrary classify/score/route judgments from any agent task —
distinct from, and independent of, the auto-task feature above. Auto-task has its own
hardcoded Choice question over the eight task values, wired through its own `pre_llm_call`
hook/middleware code path, not through this skill or `decision_client.ask()`'s public API.

- **D1** The skill is off by default. It is registered with `ctx.register_skill(...)`
  (so it becomes loadable/discoverable at all via `skill_view`) only when
  `plugins.entries.hostyourai.settings.hyai_decide_enabled` is `true`. Default `false`.
  Enabling or disabling takes effect the next time the plugin loads (a new session), not
  mid-session — `register()` runs once per load, like every other plugin registration.
- **D2** Disabling the skill specifically (`hyai_decide_enabled: false`) is independent of
  disabling the whole plugin (`hermes plugins disable hostyourai`); either is sufficient to
  stop the skill from loading, but only the whole-plugin toggle also stops the provider
  profile and `/hyai` command. Only the first half (the flag lives in this plugin's own
  config namespace, not any Hermes-core enabled/disabled state) is verified by this repo's
  test suite — whether a whole-plugin disable also stops the provider profile and `/hyai`
  command is a host-level behavior (Hermes core decides whether to call `register()` at
  all) that can't be exercised from inside this plugin's own tests.
- **D3** `decision_client.ask()`'s `model` parameter defaults to `None`, which resolves at
  call time (not import time) to `HOSTYOURAI_DECISION_MODEL` from the environment if set and
  non-empty, else to the `DEFAULT_MODEL` constant (`hyai/decider-2b`). An explicit `model=`
  argument on a single call overrides both. This lets a deployment repoint every
  `hyai-decide` call at a newer/better System One model without a code change — the
  decision-model landscape is expected to move fast.
- **D4** The skill's own frontmatter/body carries a structural "STOP" gate as the *first*
  thing after the intro, not a soft exclusion clause buried later: do not use this skill if
  the content involves health/medical, legal, financial, government-ID details, or a
  person's name plus contact info, regardless of how the request is phrased (the gate
  explicitly rebuts rationalizations like "it's just routing"/"it's quick"/"just an
  excerpt"). Rationale: negative exclusions expressed only as descriptive prose are an
  unreliable primitive for weak/cheap routing models, which match surface trigger keywords
  far more reliably than negation logic (see the `obra/superpowers-skills`
  `using-superpowers` meta-skill's equivalent structural-gate pattern, and Anthropic's
  Agent Skills authoring guidance, which has no "never use when X" frontmatter primitive at
  all — only positive what+when framing). A soft/prose-only version of this rule was tried
  first and measurably failed to be honored before being replaced with the structural gate.
- **D5** Behavioral/live quality of the skill's trigger wording is not covered by unit tests
  (an LLM's routing decision isn't a unit-testable function) — it's measured by
  `scripts/run_skill_eval.py` against `hostyourai/skills/hyai-decide/evals/{prompts,rubric}.yaml`,
  run by hand with a real model (not part of `scripts/check.sh` or CI). The harness's own
  pass/fail signal for a case is the model reply's first line, which every eval prompt must
  force into the shape `USE: <skill-name>` or `USE: none` — a looser "does the skill's name
  appear anywhere in the reply" substring check was tried first and produced false triggers
  whenever a model explained *why it would not* use the skill (the name still appears in
  that explanation). Trigger/privacy/correctness categories are weighted 40/35/25 toward the
  overall score; pass threshold is 70/100. A cheap/weak routing model (tested: `hyai/auto`
  under `hyai_routing.policy: cost`) can score far below a stronger one on the same wording
  — this is a model-capability ceiling the skill's own wording cannot fully compensate for;
  `hyai_routing.policy: balanced` or stronger is the expected baseline for evaluating this
  skill's wording changes, not `cost`.
- **D6** Code — not the decision model — owns thresholds, side effects, state changes and
  audit logging; the model only ever returns an opinion (a probability/choice/score), never
  a side effect by itself. This mirrors the "Jev judges, code decides" pattern from
  comparable decision-offload designs reviewed as prior art for this skill.
