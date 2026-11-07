# Changelog

## Unreleased

## 0.4.1 — experimental hyai-decide skill
- Adds the `hyai-decide` skill: a reusable pattern for cheap, bounded typed
  classify/score/route judgments (choice/score/noul) against HostYourAI's System One
  decision API, via a new standalone `hostyourai/decision_client.py` module usable from
  `execute_code`/`terminal` independent of the `/hyai` command.
- **Off by default.** Enable with `hyai_decide_enabled: true` in the plugin's own config
  namespace; disabled plugins/skills never load it.
- Decision model is now configurable: `HOSTYOURAI_DECISION_MODEL` env var overrides the
  hardcoded `hyai/decider-2b` default; an explicit `model=` argument on a single call
  still wins over both.
- Structured `DecisionError.status` attribute (was a string-matched HTTP status) makes
  the 429/503 retry check robust to message-format changes.
- `SPEC.md` gains D1–D6 entries documenting these decisions, each enforced by a real
  behavioral test via the existing spec-coverage meta-test.
- Live eval (`hyai/auto`, HostYourAI): stable 93.3–100/100 across reruns (pass threshold
  70) after a 3-way parallel code review and bloat/accuracy audit — no change to the
  stable `hostyourai` provider profile or `/hyai task auto` path.

## 0.4.0 — BREAKING: merged into one plugin
- **Breaking:** the two separate plugins `hyai-router` and `hostyourai-profile` are merged
  into a single plugin named `hostyourai` (directory `hostyourai/`, one `plugin.yaml`). They
  were always tightly coupled — the `/hyai` command is meaningless without the `hostyourai`
  provider — and now ship, install and uninstall as one unit.
- **Upgrade:** uninstall both old plugins (`hermes plugins uninstall hyai-router` and
  `hermes plugins uninstall hostyourai-profile`), then install the new `hostyourai` plugin
  from the updated `hermes-pack.yaml`.
- This is a structural change only, not a behavior change for users: the provider profile,
  the `/hyai` command and all of its subcommands (`task`, `policy`, `off`, `clear`, `status`,
  `usage`, `help`), the auto task-pick middleware, and usage/balance reporting behave exactly
  as before.
- Internal-only simplification (invisible to users): the provider profile now reads the
  persisted routing state through the same `ctx.get_config(CONFIG_KEY, ...)` call the `/hyai`
  command uses to write it, instead of manually parsing the other (now nonexistent) plugin's
  `plugins.entries.hyai-router.settings.hyai_routing` config path. That cross-plugin config-key
  coupling only existed because the two roles used to be separate plugins.

## Fix (previously Unreleased, now part of 0.4.0)
- Fix: `/hyai` (the hyai/auto routing-hint feature — `/hyai task|policy|off|clear`) no longer
  reads Hermes's private `state.db`/`gateway_routing` table to check the chat's current model
  before accepting a change. The setting is now always accepted. This behavior change is
  scoped only to `/hyai`'s task/policy routing hint; it does not affect the `hostyourai`
  provider profile, `/hyai usage` reporting, or any other part of the plugin — the real
  and only gate on hyai/auto (the provider profile's strict `model == "hyai/auto"` check at
  request time) is unchanged. Users who are not on `hyai/auto` no longer see a "not
  applicable"/"only apply to hostyourai hyai/auto" warning when changing the setting; it is
  simply silently inert until they switch to `hyai/auto`.

## 0.3.2
- Docs: `/hyai task auto` is flagged **Experimental** in the README (own
  subsection with a gentle "treat it as an experiment" note) and in the
  0.3.0 changelog entry, so readers know it is a prototype shaped by a
  HostYourAI decision model, not something to rely on.

## 0.3.1
- Fix: `/hyai usage` now works in the local CLI and Hermes dashboard while
  retaining its private-chat protection for messaging platforms.
- Fix: routing hints (`/hyai task`/`policy`) no longer show as inapplicable for a
  local/dashboard session with no saved `/model` override — the config default is
  used instead.

## 0.3.0
- **Experimental** `/hyai task auto`: a small HostYourAI decision model
  (`hyai/decider-2b` by default) picks the task for each message from the
  start of it and of the message before it. Treat it as an experiment: the
  guess is sometimes wrong, and the behaviour may change or disappear.
  Opt-in; any other task turns it off.
- It never delays a reply by more than 1 s and falls back to `chat` on any problem:
  timeout, low confidence, error, no key. After an error it waits 60-75 s before
  asking again. It skips other models, subagents, cron and very short messages.
- Settings under `hyai_auto`: `model`, `threshold`, `timeout_ms`, `min_chars`.
- SPEC.md rules A1-A13, with a test for each.

## 0.2.1
- Fix: the "As of" line in `/hyai usage` was missing on Python 3.10 (timestamps ending in `Z`).

## 0.2.0
- `/hyai usage`: balance, this month's spend and auto top-up from your HostYourAI
  account, with a low-balance note below `usage_warn_eur` (default 5).
- Answers only in a private chat or the local CLI and makes no request
  otherwise. The key goes only to `hostyourai.com`; redirects are refused.
- Shorthand: `/hyai code cost` (task and policy in any order).
- `/hyai` and `/hyai status` now work on any model and show your saved setting;
  only changing it still needs `hyai/auto`.
- SPEC.md describes the exact behaviour, with a test for every rule.

## 0.1.10
- README: affiliation note restored, Development and License sections, install steps.
- Bug report issue template; private vulnerability reporting enabled.
- GitHub Releases are now published by the release script.

## 0.1.9
- SECURITY.md, CHANGELOG.md, CI job that validates both plugins against Hermes.
- Hook installer no longer overwrites an existing pre-commit hook.

## 0.1.8
- Provider no longer imports a private Hermes function; it reads its setting
  from the public config API.
- `/hyai` replies and the README say the setting is shared by every chat on the
  profile.
- Simpler README.
- Dev tooling: stricter secret scan, pinned CI actions, safer release script.

## 0.1.7
- Manifest v2 (`requires_hermes >=0.21.7`, license, tags, required env).
- README: requirements, what it sends, known limits.

## 0.1.6 and earlier
- Slash command `/hyai` (task, policy, status, off) for the `hyai/auto` model,
  and the HostYourAI provider profile.
