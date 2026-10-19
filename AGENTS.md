# AGENTS.md — hermes-hostyourai

Standalone, unofficial Hermes Agent plugin pack (NOT upstream): a `hostyourai` provider profile
(`model-providers/hostyourai`) and the `/hyai` command (`hyai-router`) that steers HostYourAI's
`hyai/auto` router with task/policy hints. Private repo `cogitor/hermes-hostyourai`.

## Commands
- `scripts/check.sh` — THE gate (ruff check, ruff format --check, secret scan, tests). Run before every commit; the pre-commit hook and CI run the same script. Install the hook: `scripts/install-git-hooks.sh`.
- Tests: `python3 -m unittest discover -s tests` (only supported runner; running one test file directly can fail on import setup).
- Release: `scripts/release.sh X.Y.Z` from a clean `main` with code already committed. It gates, pins `hermes-pack.yaml` to the code SHA, commits, tags, pushes and verifies the remote tag. Never hand-edit pins or tags.

## Invariants (do not break)
- **Cache-safe.** Only per-request `extra_body`/`extra_headers` vary. Never mutate the system prompt, toolsets or past context. No core changes; plugins never touch Hermes core files.
- **Request gate is strict equality** `model == "hyai/auto"` in the provider. That is the real safeguard; the `/hyai` command gate is UX only.
- **No speculative code** for Hermes APIs that have not shipped (e.g. a picker hook). Propose upstream instead.
- **Transport is never mentioned** in `/hyai` output, help or README. It is config-only; `/hyai off` must preserve a hand-set value.
- **Config default is not the active model.** The truth is the chat's `/model` choice. The command gate reads the persisted override from `state.db` table `gateway_routing` (`scope` + `session_key` from `HERMES_SESSION_KEY`, read-only `mode=ro` connection, URI-quoted path), and fails closed (missing row or error => refuse). `HERMES_SESSION_ID` is NOT set for plugin handlers. `/model --once` is memory-only and invisible; documented limit.
- **Telegram menu:** a plugin command's `args_hint` must not start with `<`, or Hermes hides it from the menu.
- Hermes reads state via its own paths: use `get_hermes_home()`, never hardcode `~/.hermes`.

## Privacy and safety
- The repo is pushed to GitHub. Never commit real chat/user IDs, phone numbers, emails (other than the author identity in git metadata), API keys, `/home/<name>` paths, config, databases, sessions or backups. Test fixtures must be invented values. `scripts/scan_secrets.py` enforces generic shapes; `.gitignore` blocks data files. Mark a deliberate fake with `# scan: allow`.
- Never copy real `state.db`/config into tests; build empty temp databases with the real schema.
- Git author for every commit and tag: `Emil Varga <emil.varga@gmail.com>` (GitHub `cogitor`). `release.sh` refuses any other identity. Never rewrite published history without being asked. <!-- scan: allow -->

## Testing rules
- Test behavior and relationships, not frozen values (no change-detector tests). Exercise the real SQL/JSON path against a temp db, not lambdas that replace it.
- Mock Hermes with `sys.modules` fakes (see `tests/test_hostyourai_provider.py`, `tests/test_hyai_user_journeys.py`); no real Hermes install is needed.
- Any change to the gate, provider hooks or release tooling needs an independent review pass before pushing, and a full gate run.

## Release/manifest contract
`hermes-pack.yaml` `version` == the git tag; both plugin pins are the same full 40-char code commit SHA. `tests/test_manifest_sync.py` checks the relationships.
