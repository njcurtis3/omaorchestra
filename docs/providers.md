# Providers and models

## Quick start

OpenRouter, for Claude models billed per token instead of your subscription:

```bash
omaorchestra provider add openrouter
omaorchestra provider key openrouter      # paste the key when asked; it goes to the keyring
omaorchestra provider test openrouter     # checks the endpoint and the key
omaorchestra run "tidy the README" --in ~/code/app \
    --provider openrouter --model anthropic/claude-sonnet-5
```

A local Ollama needs no key; it adds its models to `omaorchestra models`:

```bash
omaorchestra provider add ollama          # http://localhost:11434 by default
omaorchestra models --provider ollama --refresh
```

Keys need a Secret Service keyring (GNOME Keyring, which Omarchy runs) and
`secret-tool` from `libsecret`. `omaorchestra spend` shows OpenRouter's
balance, and `omaorchestra provider remove <id>` removes a provider and its
key.

## Providers

Beyond your Claude subscription, omaorchestra knows about API providers:
Anthropic's API, OpenAI, OpenRouter and a local Ollama (any of them at a
different endpoint too, such as a proxy).

```bash
omaorchestra provider add openrouter
omaorchestra provider key openrouter    # asks for the key; it goes to the system keyring
omaorchestra provider test openrouter
omaorchestra models --refresh           # every provider's models, with context and prices
```

API keys are stored in the system keyring (Secret Service, via `secret-tool`)
and nowhere else: not in config files, logs, the queue, or this repository.
The provider list itself (ids, kinds, endpoints) is in
`~/.config/omaorchestra/providers.json`. omaorchestra never sends prompts to
a provider; it lists models, checks keys and reads usage, with plain HTTP.
Model lists are cached in the state directory; prices are per million
tokens, from the provider (OpenRouter) or Anthropic's published prices
(Anthropic's models API reports context sizes but not prices). The app's
**Providers** page does the same.

When a task names no model, it uses its folder's default (or that of a
folder above it), then `tasks.default_model`, then the agent's own:

```bash
omaorchestra models default opus --for ~/code/app   # this project
omaorchestra models default sonnet                  # everywhere else
omaorchestra models default                         # what applies here
```

The New task form lists Claude Code's aliases and the Claude models it
accepts, shows the folder's default, and can remember the chosen model for
the folder. Sessions show their model in the bar panel and the app.

Agents can also run through a provider instead of their subscription:
`omaorchestra run "..." --provider openrouter --model anthropic/claude-sonnet-5`
(or **Runs on** in the New task form). Claude Code can run through the
Anthropic API and OpenRouter, with Claude models only; billing then moves to
the provider. [Below](#running-agents-through-a-provider): what works, what it
means for billing and terms, and how the key is handled.

```bash
omaorchestra spend          # today's provider spend, subscription limits, what sessions cost
```

A session's cost is its transcript's token usage times the model's price.
For a session run through a provider that is real spend, kept in a daily
ledger; for a subscription session it is only the API-equivalent, since
subscriptions are not billed per token. With `tasks.daily_budget` set,
queued tasks that run through a provider wait once today's provider spend
reaches it (subscription tasks keep going, and subscription limits hold only
subscription tasks). Provider balances come from the provider where it
reports them (OpenRouter's key usage, in credits); Anthropic's own usage and
cost reports need an Admin API key and are not read. The app's **Usage**
page shows the same, and a session's details show its cost.

## Running agents through a provider

By default an agent runs on its own subscription (for Claude Code, your
claude.ai login). With `--provider <id>` (or **Runs on** in the app's New
task form) omaorchestra starts it against an API provider instead, using the
provider's key from the system keyring.

### What works

| Agent | Provider | How | Models |
|---|---|---|---|
| Claude Code | Anthropic API | `ANTHROPIC_API_KEY` (plus `ANTHROPIC_BASE_URL` for a non-default endpoint) | Claude models and aliases |
| Claude Code | OpenRouter | `ANTHROPIC_BASE_URL=https://openrouter.ai/api`, the key in `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY` set empty | Claude models only (`anthropic/claude-…`) |
| Claude Code | OpenAI, Ollama | not supported | — |

Codex and opencode have provider settings of their own; omaorchestra starts
them as they are configured and does not route them.

### What it means

- **Billing moves to the provider.** While a credential variable is set,
  Claude Code does not use your claude.ai login: the subscription's limits
  do not apply, and the provider bills per token. Unset, the login is used
  again. omaorchestra shows routed sessions as "via <provider>".
- **Claude models only.** Anthropic does not support routing Claude Code to
  non-Claude models through any gateway, so omaorchestra refuses them.
- **Gateways are third-party.** Anthropic does not endorse, maintain or
  audit third-party gateways such as OpenRouter; check the provider's own
  terms for how it may be used.
- **The key's path.** It is read from the keyring when the agent starts and
  passed only in that agent's environment (readable by your own user, like
  any process environment). It is never written to the queue, the session
  registry, logs or config files; a queued task records only the provider's
  id.

### Sources

- Claude Code, connecting to an LLM gateway:
  https://code.claude.com/docs/en/llm-gateway-connect (which variable sends
  which header; credentials take precedence over the claude.ai login)
- Claude Code, other LLM gateways: https://code.claude.com/docs/en/llm-gateway
  (subscriptions and gateways; non-Claude models are not supported)
- OpenRouter, Claude Code integration:
  https://openrouter.ai/docs/guides/guides/claude-code-integration
