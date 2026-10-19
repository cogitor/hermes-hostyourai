# /hyai behaviour spec

What `/hyai` must do. Every rule has an ID; `tests/test_spec_coverage.py` fails if a rule ID is
not mentioned in a test. Name the ID in the test's docstring or comment.

"On hyai/auto" means the chat's selected model is `hyai/auto` on the `hostyourai` provider
(or a provider that was not recorded). Anything else, or a model that cannot be determined,
is "not on hyai/auto".

## Routing setting

- **R1** `/hyai` shows the saved task and policy, then the list of choices. Works on any model.
- **R2** `/hyai status` shows the saved task and policy only. Works on any model.
- **R3** When not on hyai/auto, R1 and R2 add one line saying hints only apply to hyai/auto.
- **R4** `/hyai task <value>` and `/hyai policy <value>` save one value, lowercased, and keep
  the other. Values come from the fixed lists; anything else saves nothing and shows usage.
- **R5** Shorthand `/hyai <word> [<word>]`: task and policy words in any order. At most one of
  each. Words are the exact task and policy values (no aliases). Any
  unknown word, or two of the same kind, saves nothing and shows usage.
- **R6** `/hyai off` and `/hyai clear` remove task and policy. A hand-set `transport` stays.
- **R7** R4, R5 and R6 only work on hyai/auto. Elsewhere they change nothing and say why.
- **R8** An unknown subcommand shows usage (on hyai/auto) and never changes the setting.
- **R9** `/hyai help` shows usage. Works on any model.
- **R10** The word "transport" never appears in any reply.
- **R11** The saved setting is sent only to hyai/auto, by the provider profile (strict match).
- **R12** A local surface (CLI, TUI, desktop, or the Hermes dashboard) with no recorded `/model`
  override reads its effective model from the config default, so it stays determinable instead
  of reading as "unknown".

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

- **A1** `/hyai task auto` saves `auto` as the task. Only on hyai/auto (as R7). It is off until the
  user sets it. `/hyai off` clears it, and so does any other task value.
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
