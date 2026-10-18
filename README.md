# hermes-hostyourai

A [HostYourAI](https://hostyourai.com) provider plugin for
[Hermes Agent](https://github.com/NousResearch/hermes-agent): registers
HostYourAI as a named model provider, and adds a `/hyai` slash command to
steer its `hyai/auto` router with task/policy hints.

Not affiliated with or endorsed by Nous Research or HostYourAI — a personal,
unofficial integration.

This package adds two Hermes plugins:

- **`model-providers/hostyourai`** — a `ProviderProfile` so HostYourAI shows up
  in Hermes's setup/model picker instead of requiring the generic
  custom-endpoint flow. Default model: `hyai/auto`.
- **`hyai-router`** — the `/hyai` slash command, for setting sticky
  `task`/`policy`/`transport` routing hints that get applied to every request
  sent to `hyai/auto` (mirrors the UX of Hermes's built-in `/fast`/`/reasoning`
  commands).

The two plugins are coupled only by a shared config key name, not by Python
imports — either can be installed, updated, or removed independently.

## Install

Hermes Agent plugin docs: [Plugins user guide](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins) ·
[Build a Hermes Plugin](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins)

```bash
hermes plugins install cogitor/hermes-hostyourai --no-enable
hermes plugins enable hyai-router
hermes plugins enable hostyourai-profile
```

Then set `HOSTYOURAI_API_KEY` in your Hermes `.env` / provider setup, as you
would for any other provider (`hermes config set HOSTYOURAI_API_KEY`, or via
`hermes model` / `hermes setup`).

## How to use

Pick `HostYourAI` / model `hyai/auto` as your provider+model (`hermes model`),
then steer its router with `/hyai`:

```
/hyai                      show current task/policy/transport
/hyai task <value>         chat | code | summarise | classify | extract | rag | legal | reason
/hyai policy <value>       balanced | speed | cost | quality
/hyai transport <mode>     body (default) | headers
/hyai off                  clear the hint — hyai/auto decides per request alone
```

Example — tell the router you're doing a coding task, optimized for speed:

```
/hyai task code
/hyai policy speed
```

Every request to `hyai/auto` after that carries those hints (as
`extra_body.hyai = {"task": "code", "policy": "speed"}`, or as
`X-HYAI-Task`/`X-HYAI-Policy` headers if `transport headers` is set) until you
change them or run `/hyai off`. Run `/hyai` with no arguments any time to see
the current setting.

Task/policy enums mirror HostYourAI's own n8n community node
(`HostYourAiChatModel`), so a Hermes session and an n8n workflow route the
same way given the same hints.

## Design notes

- **Fail-open.** The provider profile's config read never raises into a live
  request — any error (missing/renamed internal Hermes API, malformed
  hand-edited config, etc.) logs a warning and is treated as "no routing hint
  set," never a broken request.
- **Defense-in-depth enum validation.** Both the slash command and the
  provider profile's config read independently re-validate `task`/`policy`
  against the same fixed allowlists, so a hand-edited `config.yaml` can never
  put an arbitrary string on the wire.
- **No system-prompt mutation, no cache-busting.** Routing hints go out only
  as per-request `extra_body`/`extra_headers`; nothing here touches the system
  prompt, so prompt caching is unaffected.
- **Future auto-classify mode (not built).** HostYourAI has floated a
  Jev-like mode that would pick `task` per turn from the prompt instead of a
  fixed sticky value. If that ships: add `task auto` as a third state, call
  the classifier from `build_extra_body`, default to `chat` on any classifier
  failure/timeout, and keep it strictly opt-in (it adds per-turn latency/cost).
  Documented here only — no speculative code for an unshipped API.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

40 unit tests, no real Hermes install required (the provider-profile test
module installs minimal in-memory fakes for the two internal Hermes modules
it reads config through).

## Development

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs `ruff check`,
`ruff format --check`, and the test suite on every push/PR to `main`.

To get the same lint/format check locally before each commit:

```bash
./scripts/install-git-hooks.sh   # one-time, installs .git/hooks/pre-commit
```

The hook runs `ruff check .` and `ruff format --check .` on commit; it
installs `ruff` for you on first run if it's not already present. Fix with
`ruff check --fix .` / `ruff format .` as needed.

## Compatibility

Developed and tested against a specific Hermes Agent checkout as of 2026-10;
`model-providers/hostyourai/__init__.py` reads Hermes's internal
`hermes_cli.plugins_state._plugin_settings_entry` (an underscore-prefixed,
non-public helper) to resolve the persisted routing state. That's an explicit
tradeoff for v1 — there's currently no public equivalent in the plugin API —
and it fails open (silently no-ops with a logged warning) if that internal
shape ever changes in a future Hermes release, rather than crashing requests.

## License

MIT — see [LICENSE](LICENSE).
