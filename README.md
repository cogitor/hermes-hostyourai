# hermes-hostyourai

[![CI](https://github.com/cogitor/hermes-hostyourai/actions/workflows/ci.yml/badge.svg)](https://github.com/cogitor/hermes-hostyourai/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/version-v0.1.7-blue)](https://github.com/cogitor/hermes-hostyourai/tags)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Use [HostYourAI](https://hostyourai.com) from [Hermes Agent](https://github.com/NousResearch/hermes-agent),
and tell its automatic model picker what kind of work you are doing.

## Why HostYourAI?

- **EU-only hosting.** Switch on "EU only" for your key in HostYourAI and
  every request stays on sovereign, open-weight models. Read more:
  [EU Inference Router](https://hostyourai.com/en/service/eu-inference-router).
- **Automatic model choice.** The `hyai/auto` model picks a suitable warm
  model for each request, and you can steer it by task and policy (below).
  Read more: [HostYourAI docs](https://hostyourai.com/app/docs).
- **Bring your own model.** Run any open model from the catalog, or host your
  own from Hugging Face. Read more:
  [Dedicated GPU instances](https://hostyourai.com/en/service/dedicated-gpu-instances).

## What you get

- **HostYourAI as a provider.** It shows up in Hermes's model picker, so you
  don't need the generic custom-endpoint setup. Default model: `hyai/auto`.
- **A `/hyai` command** to give `hyai/auto` a hint about the work, for example
  "this is code, optimise for speed".

The two parts are independent. Install either one alone.

## Requirements

- Hermes Agent 0.21.7 or newer
- A HostYourAI account and API key

## Install

```bash
curl -O https://raw.githubusercontent.com/cogitor/hermes-hostyourai/main/hermes-pack.yaml
hermes plugins pack install ./hermes-pack.yaml
hermes plugins enable hyai-router
hermes plugins enable hostyourai-profile
```

Then add your key (`hermes config set HOSTYOURAI_API_KEY`) and pick the
provider with `hermes model`. To install just one part:

```bash
hermes plugins install cogitor/hermes-hostyourai/hyai-router
hermes plugins install cogitor/hermes-hostyourai/model-providers/hostyourai
```

## Use it

Select HostYourAI and the model `hyai/auto`, then:

```
/hyai                  show the current setting
/hyai task code        chat, code, summarise, classify, extract, rag, legal or reason
/hyai policy speed     balanced, speed, cost or quality
/hyai off              clear the hint and let hyai/auto decide alone
```

The setting sticks until you change it. It only applies to `hyai/auto`:
any other model or provider never receives it. Switch models with `/model`
and the hint stops; switch back and it resumes.

If `/hyai` replies "only applies to hostyourai hyai/auto", the chat is on a
different model; nothing was changed.

## What it sends and changes

- Requests to HostYourAI go to `https://hostyourai.com/api/v1` with your API
  key. With `hyai/auto` they also carry your task and policy. Nothing else is
  sent anywhere. No telemetry.
- `/hyai` saves your choice in your Hermes `config.yaml`.
- To check which model a chat is using, it reads Hermes's session database in
  read-only mode. If that fails, it does nothing rather than guess.
- It never touches your system prompt, so prompt caching keeps working.

## Known limits

- A one-off `/model --once` choice is invisible to the plugin.
- `/hyai` with no arguments shows its options as text. Telegram buttons would
  need support in Hermes itself.
- Checking the chat's model relies on a Hermes internal that may change. If it
  does, `/hyai` refuses safely instead of misbehaving.

## Troubleshooting

**`/hyai` is missing from Telegram's command menu.** Telegram limits how many
commands the menu shows. Reserve a slot, restart, then reopen the chat:

```bash
hermes config set platforms.telegram.extra.command_menu.priority '["hyai"]'
hermes gateway restart
```

Typing `/hyai` by hand works either way.

## Contributing

See [AGENTS.md](AGENTS.md) for how the repo is built, tested and released.

[MIT](LICENSE).
