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

## Usage (`/hyai usage`)

- **U1** Works on any model. Never changes the saved setting.
- **U2** Shows balance, spend this month, auto top-up on/off, and "as of" in local time
  (`Jan 01, 13:00`). Amounts look like `€12.34`.
- **U3** Only in a direct message or the local CLI. A group, a channel, an unknown chat type,
  or a lost session context is refused, and no network request is made.
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
