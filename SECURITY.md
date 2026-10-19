# Security

## Reporting a problem

Please report security issues privately through GitHub's
["Report a vulnerability"](https://github.com/cogitor/hermes-hostyourai/security/advisories/new)
form on this repository, not in a public issue. Include the plugin version and
Hermes version.

## What this plugin does with your data

- It never reads, stores or logs your API key. The key stays in Hermes'
  own config and is sent by Hermes, not by this plugin.
- `/hyai` stores two short choices (task, policy) in your Hermes config under
  `plugins.entries.hyai-router.settings`. They are shared by every chat on the
  same Hermes profile.
- On requests to `hyai/auto` only, it adds those two choices to the request.
  Nothing else is added, and no conversation content is read or sent by the plugin.
- It does not make network calls of its own.

See "What it sends" and "Known limits" in the README for details.
