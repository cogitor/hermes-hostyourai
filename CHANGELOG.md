# Changelog

## 0.3.1
- Fix: `/hyai usage` now works in the local CLI and Hermes dashboard while
  retaining its private-chat protection for messaging platforms.
- Fix: routing hints (`/hyai task`/`policy`) no longer show as inapplicable for a
  local/dashboard session with no saved `/model` override — the config default is
  used instead.

## 0.3.0
- `/hyai task auto`: a small HostYourAI model picks the task for each message from the
  start of it and of the message before it. Opt-in; any other task turns it off.
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
