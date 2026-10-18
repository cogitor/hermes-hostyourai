# hermes-hyai-plugin

Standalone [Hermes Agent](https://hermes-agent.nousresearch.com/docs) plugin pair for
[HostYourAI](https://hostyourai.com): a named provider profile for HostYourAI's
`hyai/auto` router, plus a `/hyai` slash command to set sticky task/policy routing
hints on it.

Not affiliated with or endorsed by Nous Research or HostYourAI; a personal,
unofficial integration.

## What's in here

- **`model-providers/hostyourai/`** — a `ProviderProfile` that registers HostYourAI
  as a first-class provider (shows up in Hermes's setup/model picker instead of
  requiring the generic custom-endpoint flow). Default model: `hyai/auto`.
- **`hyai-router/`** — the `/hyai` slash command. Sets a sticky `task`/`policy`/
  `transport` preference that the provider profile applies to every request sent
  to `hyai/auto`, mirroring the UX of Hermes's built-in `/fast`/`/reasoning`
  commands.

The two plugins are coupled only by a shared config key name (`hyai_routing`),
not by Python imports — either can be installed, updated, or removed independently.

## Usage

```
/hyai                      show current task/policy/transport
/hyai status               same as above
/hyai task <value>         chat|code|summarise|classify|extract|rag|legal|reason
/hyai policy <value>       balanced|speed|cost|quality
/hyai transport <mode>     body (default, extra_body.hyai) | headers (X-HYAI-Task/-Policy)
/hyai off | clear          remove the override — hyai/auto decides per request
```

Task/policy/transport enums mirror HostYourAI's own n8n community node
(`HostYourAiChatModel`), so a Hermes session and an n8n workflow route the same
way given the same hints.

## Install

```bash
cp -r hyai-router model-providers/hostyourai ~/.hermes/plugins/
hermes plugins enable hyai-router
hermes plugins enable hostyourai-profile
```

Then set `HOSTYOURAI_API_KEY` in your Hermes `.env` / provider setup as you
would for any other provider.

## Design notes

- **Fail-open.** The provider profile's config read never raises into a live
  request — any error (missing/renamed internal Hermes API, malformed
  hand-edited config, etc.) logs a warning and is treated as "no routing hint
  set", never a broken request.
- **Defense-in-depth enum validation.** Both the slash command and the
  provider-profile's config read independently re-validate `task`/`policy`
  against the same fixed allowlists, so a hand-edited `config.yaml` can never
  put an arbitrary string on the wire.
- **No system-prompt mutation, no cache-busting.** Routing hints go out only as
  per-request `extra_body`/`extra_headers`; nothing here touches the system
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
