# hermes-hostyourai

![HostYourAI for Hermes Agent — EU-hosted inference](docs/banner.jpg)

[![CI](https://github.com/cogitor/hermes-hostyourai/actions/workflows/ci.yml/badge.svg)](https://github.com/cogitor/hermes-hostyourai/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/version-v0.4.0-blue)](https://github.com/cogitor/hermes-hostyourai/tags)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Use [HostYourAI](https://hostyourai.com) from [Hermes Agent](https://github.com/NousResearch/hermes-agent),
and tell its automatic model picker what kind of work you are doing.

## Why HostYourAI?

- 🇪🇺 **EU-only hosting.** Switch on "EU only" for your key in HostYourAI and
  every request stays on sovereign, open-weight models. Read more:
  [EU Inference Router](https://hostyourai.com/en/service/eu-inference-router).
- **Automatic model choice.** The `hyai/auto` model picks a suitable warm
  model for each request, and you can steer it by task and policy (below).
  Read more: [HostYourAI docs](https://hostyourai.com/app/docs).
- **Bring your own model.** Run any open model from the catalog, or host your
  own from Hugging Face. Read more:
  [Dedicated GPU instances](https://hostyourai.com/en/service/dedicated-gpu-instances).

## What you get

One plugin, `hostyourai`:

- **HostYourAI as a provider.** It shows up in Hermes's model picker, so you
  don't need the generic custom-endpoint setup. Default model: `hyai/auto`.
- **A `/hyai` command** to give `hyai/auto` a hint about the work, for example
  "this is code, optimise for speed".
- **A bundled `hyai-decide` skill** for calling HostYourAI's typed decision API
  (classify/score/route judgments) directly from your own agent tasks, not just
  through this plugin's `/hyai` command.

## Using the `hyai-decide` skill

Any agent task running under this plugin can call HostYourAI's typed decision
API directly, without going through `/hyai` or a chat model at all — useful
for classify/score/route judgments inside your own scripts or other skills.

```python
from hostyourai.decision_client import DecisionClient

client = DecisionClient()  # reads HOSTYOURAI_API_KEY from the environment
result = client.ask(
    "classify",
    prompt="Is this support ticket urgent?",
    choices=["urgent", "normal", "low"],
)
print(result.choice, client.confidence_of(result))
```

Needs `HOSTYOURAI_API_KEY` in the environment (same key as the plugin). See
[`hostyourai/skills/hyai-decide/SKILL.md`](hostyourai/skills/hyai-decide/SKILL.md)
for the full contract (ask/confidence_of, retries, timeouts) and when an
agent should reach for it.

The skill ships its own eval set
(`hostyourai/skills/hyai-decide/evals/{prompts,rubric}.yaml`). Run it against
a live model with:

```bash
python3 scripts/run_skill_eval.py \
  --skill-path hostyourai/skills/hyai-decide \
  --model hyai/auto --provider hostyourai
```

## Requirements

- Hermes Agent 0.21.7 or newer
- A HostYourAI account and API key

## Install

```bash
hermes plugins install cogitor/hermes-hostyourai/hostyourai --enable
```

Then add your key (`hermes config set HOSTYOURAI_API_KEY`) and pick the
provider with `hermes model`.

## Use it

Select HostYourAI and the model `hyai/auto`, then:

```
/hyai                  show the current setting
/hyai task code        chat, code, summarise, classify, extract, rag, legal or reason
/hyai policy speed     balanced, speed, cost or quality
/hyai off              clear the hint and let hyai/auto decide alone
```

Shorthand works too: `/hyai code cost` sets both. Use the exact task and policy
words, in any order.

> **Experimental:** `/hyai task auto` lets a small HostYourAI model pick the task
> for you. It is off until you enable it. See [Experimental](#experimental).

`/hyai usage` shows your HostYourAI balance, this month's spend and whether
auto top-up is on, straight from your account. It answers only in a private
chat, the local CLI, or the Hermes dashboard; never in a group. It works on
any model.

The setting sticks until you change it, and it is shared: it applies to every
chat on the same Hermes profile, and anyone who can use `/hyai` there can
change it. `/hyai` itself never checks which model the chat is on — it always
accepts the setting. The setting only has an effect when a chat is actually on
`hyai/auto`: that check lives in the `hostyourai` provider profile at request
time, not in `/hyai` itself. Any other model or provider never receives it.
Switch models with `/model` and the hint stops; switch back and it resumes.

`/hyai`, `/hyai status` and `/hyai usage` all work the same on every model.

### Experimental

> ⚠️ **This is an experiment, treat it as such.** It uses a small
> decision model (`hyai/decider-2b` by default) to guess what you are doing,
> and that guess is not always right. Treat it as a taste of what automatic
> model selection with decision models could be — not as something to rely on.
> It may change or disappear in a later version.

`/hyai task auto` lets the small model pick the task for each message instead of
you. It is off until you turn it on, and any other `/hyai task` value turns it
off again. `/hyai` shows what the last message got.

- If it is not sure, takes longer than 1 second or is unavailable, the message
  gets `chat`, so a failure never blocks a reply.
- Messages shorter than about 20 characters in total are not checked.
- After the service returns an error, auto waits about a minute before asking
  again.
- Settings are in `config.yaml` under `hyai_auto`: `model`, `threshold`,
  `timeout_ms` (default 1000) and `min_chars`.

## What it sends and changes

- Requests to HostYourAI go to `https://hostyourai.com/api/v1` with your API
  key. With `hyai/auto` they also carry your task and policy. Nothing else is
  sent anywhere. No telemetry.
- With `/hyai task auto`, each message makes one more request to
  `https://hostyourai.com/api/decision` with your key. It sends the first 200
  characters of your message and of your message before it, nothing else: no
  replies, tool output or files. Those texts are never written to logs, and the
  request never follows a redirect.
- The `hyai-decide` skill, when an agent task calls it, sends one request per
  call to `https://hostyourai.com/api/decision/v1/systemone` with your key,
  your prompt and your choices — only what the calling task explicitly passes
  in. Nothing is sent unless a task calls it.
- `/hyai usage` makes one request that only reads your balance to the same address
  with your key, only when you run it, never in a group chat, and it never
  follows a redirect. The result is kept in memory for a minute, not saved.
- `/hyai` saves your choice in your Hermes `config.yaml`, under this plugin's own
  settings. The command reads no other Hermes internal state — it no longer
  checks which model the chat is on before accepting a change.
- It never touches your system prompt, so prompt caching keeps working.

## Known limits

- A one-off `/model --once` choice is invisible to the plugin.
- Auto needs a warm model. After an idle period HostYourAI can take a few minutes
  to start the small model again; until then messages simply get `chat`.
- Auto can add up to 1 second before the first model call of a message.
- On Hermes older than 0.21.7, `/hyai` still works but auto does nothing.
- `/hyai` with no arguments shows its options as text. Telegram buttons would
  need support in Hermes itself.

## Troubleshooting

**`/hyai` is missing from Telegram's command menu.** Telegram limits how many
commands the menu shows. Reserve a slot, restart, then reopen the chat:

```bash
hermes config set platforms.telegram.extra.command_menu.priority '["hyai"]'
hermes gateway restart
```

Typing `/hyai` by hand works either way.

## Development

- [AGENTS.md](AGENTS.md): how the repo is built, tested and released.
- [CHANGELOG.md](CHANGELOG.md): what changed in each version.
- [SECURITY.md](SECURITY.md): how to report a vulnerability.
- Found a bug? [Open an issue](https://github.com/cogitor/hermes-hostyourai/issues/new/choose) and fill in the template.

## License and affiliation

License: [MIT](LICENSE). Not affiliated with or endorsed by Nous Research or HostYourAI.
