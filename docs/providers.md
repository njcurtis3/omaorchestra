# Running agents through API providers

By default an agent runs on its own subscription (for Claude Code, your
claude.ai login). With `--provider <id>` (or **Runs on** in the app's New
task form) omaorchestra starts it against an API provider instead, using the
provider's key from the system keyring.

## What works

| Agent | Provider | How | Models |
|---|---|---|---|
| Claude Code | Anthropic API | `ANTHROPIC_API_KEY` (plus `ANTHROPIC_BASE_URL` for a non-default endpoint) | Claude models and aliases |
| Claude Code | OpenRouter | `ANTHROPIC_BASE_URL=https://openrouter.ai/api`, the key in `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY` set empty | Claude models only (`anthropic/claude-…`) |
| Claude Code | OpenAI, Ollama | not supported | — |

Other agents (Codex, opencode) come with their adapters.

## What it means

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

## Sources

- Claude Code, connecting to an LLM gateway:
  https://code.claude.com/docs/en/llm-gateway-connect (which variable sends
  which header; credentials take precedence over the claude.ai login)
- Claude Code, other LLM gateways: https://code.claude.com/docs/en/llm-gateway
  (subscriptions and gateways; non-Claude models are not supported)
- OpenRouter, Claude Code integration:
  https://openrouter.ai/docs/guides/guides/claude-code-integration
