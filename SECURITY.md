# Security

## Reporting a problem

Please report security issues privately through GitHub's
["Report a vulnerability"](https://github.com/cogitor/hermes-hostyourai/security/advisories/new)
form on this repository, not in a public issue. Include the plugin version and
Hermes version.

## What this plugin does with your data

- It never stores or logs your API key. Hermes sends the key on model requests.
  `/hyai usage` reads it from the environment to call HostYourAI directly; it goes only in the Authorization header to `hostyourai.com`,
  redirects are refused, and it is never put in a reply or log line.
- `/hyai` stores two short choices (task, policy) in your Hermes config under
  `plugins.entries.hyai-router.settings`. They are shared by every chat on the
  same Hermes profile.
- On requests to `hyai/auto` only, it adds those two choices to the request.
  Nothing else is added, and no conversation content is read or sent by the plugin.
- Its only network call is `GET /balance` on `https://hostyourai.com/api/v1`,
  made when you run `/hyai usage`. It refuses to run in group chats. No other
  host is contacted.
- It reads Hermes's `state.db` read-only (to find the chat's model).

See "What it sends" and "Known limits" in the README for details.
