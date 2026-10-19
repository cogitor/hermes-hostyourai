# hermes-hostyourai

[![CI](https://github.com/cogitor/hermes-hostyourai/actions/workflows/ci.yml/badge.svg)](https://github.com/cogitor/hermes-hostyourai/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/version-v0.1.7-blue)](https://github.com/cogitor/hermes-hostyourai/tags)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A [HostYourAI](https://hostyourai.com) provider plugin for
[Hermes Agent](https://github.com/NousResearch/hermes-agent): registers
HostYourAI as a named model provider, and adds a `/hyai` slash command to
steer its `hyai/auto` router with task/policy hints.

This package adds two Hermes plugins:

- **`model-providers/hostyourai`** — a `ProviderProfile` so HostYourAI shows up
  in Hermes's setup/model picker instead of requiring the generic
  custom-endpoint flow. Default model: `hyai/auto`.
- **`hyai-router`** — the `/hyai` slash command, for setting sticky
  `task`/`policy` routing hints that get applied to every request
  sent to `hyai/auto` (mirrors the UX of Hermes's built-in `/fast`/`/reasoning`
  commands).

The two plugins are coupled only by a shared config key name, not by Python
imports — either can be installed, updated, or removed independently.

## Why HostYourAI?

### 🇪🇺 EU Sovereign hosting of openweight LLM models

- Enable "EU only" on a key to enforce sovereign routing.
- This is configured in HostYourAI's key-level setting, then used by this node.

Read more: [EU Inference Router](https://hostyourai.com/en/service/eu-inference-router)

### Automatic model choice with parameter-based optimization

- Use the `hyai/auto` model option to automatically select an available warm model.
- You can steer optimization with Task + Policy parameters.
- Parameter details are documented below in [Node parameters](./#node-parameters).

Read more: [HostYourAI docs (auto mode)](https://hostyourai.com/app/docs)

### Bring your own model

- You can run any open model from the model catalog, or host dedicated/own models from HuggingFace.

Read more: [Dedicated GPU instances (bring your own model)](https://hostyourai.com/en/service/dedicated-gpu-instances)

## Requirements

- Hermes Agent `>=0.21.7` (`requires_hermes` in both manifests; the `/hyai`
  command uses `argument_mode`, first shipped there).
- A HostYourAI account and API key (`HOSTYOURAI_API_KEY`).
- No Python dependencies.

## What leaves your machine, and what the plugin touches

- **Network:** requests to the `hostyourai` provider go to
  `https://hostyourai.com/api/v1`, with your API key and, when `hyai/auto` is
  the active model, per-request `task`/`policy` routing hints. Nothing is
  sent for any other provider or model. No telemetry, no other endpoints.
- **Credentials:** the key is read only through the provider profile's
  `HOSTYOURAI_API_KEY` env var; the plugin never reads, logs or stores it.
- **Files:** `/hyai` writes the chosen `task`/`policy` to this plugin's
  settings entry in `config.yaml`. It reads Hermes's `state.db`
  (`gateway_routing`) **read-only** to see which model a gateway session
  selected with `/model`, and fails closed (no hints) if that cannot be read.
- **Not done:** no system-prompt changes, no core patching, no self-updating,
  no background processes.

## Install

Hermes Agent plugin docs: [Plugins user guide](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins) ·
[Build a Hermes Plugin](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins) ·
[Plugin packs](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins#plugin-packs)

This repo holds **two plugins** (one `/hyai` slash command, one model-provider
profile) in separate subdirectories. The easiest way to get both is the
bundled plugin pack — one command, one consent screen, both pinned to the
same commit:

```bash
curl -O https://raw.githubusercontent.com/cogitor/hermes-hostyourai/main/hermes-pack.yaml
hermes plugins pack install ./hermes-pack.yaml
```

That installs both plugins (disabled by default) — then enable them:

```bash
hermes plugins enable hyai-router
hermes plugins enable hostyourai-profile
```

Or install each subdirectory individually if you only want one of the two
(the `owner/repo/path` form pulls just that folder):

```bash
hermes plugins install cogitor/hermes-hostyourai/hyai-router --no-enable
hermes plugins install cogitor/hermes-hostyourai/model-providers/hostyourai --no-enable
```

Either way, set `HOSTYOURAI_API_KEY` in your Hermes `.env` / provider setup
afterwards, as you would for any other provider (`hermes config set
HOSTYOURAI_API_KEY`, or via `hermes model` / `hermes setup`).

## How to use

Pick `HostYourAI` / model `hyai/auto` as your provider+model (`hermes model`),
then steer its router with `/hyai`:

```
/hyai                      show current task/policy
/hyai task <value>         chat | code | summarise | classify | extract | rag | legal | reason
/hyai policy <value>       balanced | speed | cost | quality
/hyai off                  clear the hint — hyai/auto decides per request alone
```

`/hyai` is a finite-choice (options-mode) command — typing `/hyai ` on a
surface with command autocomplete (CLI, TUI, Desktop) offers
`status`/`task`/`policy`/`off`, and once you pick `task`/`policy` it offers
the valid enum values, so you fill it in like a short dialog rather than
memorizing the list.

Example — tell the router you're doing a coding task, optimized for speed:

```
/hyai task code
/hyai policy speed
```

Every request to `hyai/auto` after that carries those hints as
`extra_body.hyai = {"task": "code", "policy": "speed"}` until you change them
or run `/hyai off`. Run `/hyai` with no arguments any time to see the current
setting. **This only ever applies to the `hyai/auto` model** — the provider
profile checks the active model with a strict equality match before adding
any hint; any other model (a different HostYourAI model, or any other
provider entirely) gets no `hyai` field added to its request at all.

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

## Troubleshooting

**`/hyai` doesn't appear in Telegram's `/` command menu.** Telegram caps the
visible bot menu at a small number of slots (Hermes defaults to 60; Telegram's
own hard limit is 100) and fills it by priority — on an account with many
commands (built-ins + other plugins + skills), `/hyai` can simply lose that
ranking fight. Two things to check, in order:

1. **Give `/hyai` a guaranteed slot** — add it to Hermes's Telegram menu
   priority list and restart the gateway:
   ```bash
   hermes config set platforms.telegram.extra.command_menu.priority '["hyai"]'
   hermes gateway restart
   ```
   (`hermes config set` is required — `config.yaml` itself is protected from
   direct file edits as a security measure.)
2. **Re-enter the chat.** Telegram clients only reload a bot's command list
   when you open/enter the chat — they do not live-refresh an already-open
   chat after `setMyCommands`. If step 1 doesn't seem to have worked, leave
   the Telegram chat (or switch away and back) rather than assuming the
   registration failed; `hermes logs` showing `set_my_commands OK` means the
   server side already succeeded and the gap is client-side caching.

`/hyai` itself still works by typing it manually at any time regardless of
whether it's in the menu — the menu is a discovery aid, not a requirement.

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

## Known limits

- `/model --once` is not visible to the plugin: it cannot read a per-turn
  override, so hints follow the session's selected model.
- Bare `/hyai` shows status and choices as text; Telegram buttons would need
  a core dialog API.
- Reading the session's selected model relies on `state.db`'s
  `gateway_routing` table, a private Hermes schema. A public API (a
  hook passing provider and model to command handlers) is the proper fix.

## Compatibility

Developed against Hermes Agent 2026.9.24 and declared `>=0.21.7`;
`model-providers/hostyourai/__init__.py` reads Hermes's internal
`hermes_cli.plugins_state._plugin_settings_entry` (an underscore-prefixed,
non-public helper) to resolve the persisted routing state. That's an explicit
tradeoff for v1 — there's currently no public equivalent in the plugin API —
and it fails open (silently no-ops with a logged warning) if that internal
shape ever changes in a future Hermes release, rather than crashing requests.

## License

[MIT](LICENSE).
