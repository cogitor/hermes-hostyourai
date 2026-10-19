# Changelog

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
