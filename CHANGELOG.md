# Changelog

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
