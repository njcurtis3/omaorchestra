"""Running an agent through an API provider instead of its subscription.

Claude Code reads its endpoint and credential from the environment
(documented at code.claude.com/docs/en/llm-gateway-connect):
ANTHROPIC_BASE_URL is the endpoint (Claude Code appends /v1/messages),
ANTHROPIC_API_KEY is sent as `x-api-key`, ANTHROPIC_AUTH_TOKEN as
`Authorization: Bearer`. A credential variable replaces the claude.ai login
for that session, so the subscription's limits do not apply and the
provider bills per token.

Anthropic does not support routing Claude Code to non-Claude models through
any gateway, so only Claude models are allowed here.
"""

from . import keys, providers


class RoutingError(Exception):
    pass


# Provider kinds Claude Code can be routed through, and what each needs.
CLAUDE_CODE_KINDS = ("anthropic", "openrouter")
ANTHROPIC_DEFAULT_URL = "https://api.anthropic.com"


def routable(provider):
    return provider["kind"] in CLAUDE_CODE_KINDS


def is_claude_model(provider, model):
    """Whether `model` names a Claude model on this provider (aliases too)."""
    if not model:
        return True  # the agent's default, which is a Claude model
    if provider["kind"] == "openrouter":
        return model.startswith("anthropic/claude")
    return model.startswith("claude-") or model in ("fable", "opus", "sonnet", "haiku")


def claude_code_env(provider, model=None, lookup=None):
    """Environment variables that send Claude Code through `provider`."""
    lookup = lookup or keys.lookup  # looked up now, so it can be replaced
    if not routable(provider):
        raise RoutingError(f"Claude Code cannot run through {provider['label']} "
                           f"(it can use: {', '.join(providers.KINDS[k]['label'] for k in CLAUDE_CODE_KINDS)})")
    if not is_claude_model(provider, model):
        raise RoutingError(f"{model} is not a Claude model; Anthropic does not support routing Claude Code "
                           "to other models")
    key = lookup(provider["id"])
    if not key:
        raise RoutingError(f"no API key stored for {provider['id']} (`omaorchestra provider key {provider['id']}`)")
    if provider["kind"] == "anthropic":
        env = {"ANTHROPIC_API_KEY": key}
        if provider["base_url"] != ANTHROPIC_DEFAULT_URL:
            env["ANTHROPIC_BASE_URL"] = provider["base_url"]
        return env
    # OpenRouter's Anthropic-compatible endpoint, as OpenRouter documents it:
    # the key as a bearer token, and ANTHROPIC_API_KEY explicitly empty so
    # Claude Code does not fall back to Anthropic.
    base = provider["base_url"]
    base = base[: -len("/v1")] if base.endswith("/v1") else base
    return {"ANTHROPIC_BASE_URL": base, "ANTHROPIC_AUTH_TOKEN": key, "ANTHROPIC_API_KEY": ""}
