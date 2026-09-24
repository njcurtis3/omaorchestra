"""The models each provider offers, fetched over its API and cached.

Every model is normalised to {"id", "name", "provider", "context",
"max_output", "input_price", "output_price"} with prices in US dollars per
million tokens (None when the provider does not say).
"""

import json
import os
import time

from . import keys, paths, providers

# Anthropic's models endpoint reports context and output limits but not
# prices. First-party list prices, USD per million tokens (input, output),
# as published by Anthropic (checked 2026-06-24). Update when they change.
ANTHROPIC_PRICES = {
    "claude-fable-5-1": (10.0, 50.0), "claude-fable-5": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0), "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0), "claude-opus-4-7": (5.0, 25.0), "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0), "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

# Model aliases Claude Code accepts (`claude --model <alias>`); it maps them
# to the current model of each family on its own subscription or key.
CLAUDE_CODE_ALIASES = [
    {"id": "fable", "name": "Fable (latest)"},
    {"id": "opus", "name": "Opus (latest)"},
    {"id": "sonnet", "name": "Sonnet (latest)"},
    {"id": "haiku", "name": "Haiku (latest)"},
]


def anthropic_price(model_id):
    """(input, output) USD per MTok for an Anthropic model id (dated
    snapshots included), or (None, None)."""
    for known, price in sorted(ANTHROPIC_PRICES.items(), key=lambda kv: -len(kv[0])):
        if model_id == known or model_id.startswith(known + "-"):
            return price
    return (None, None)


def _model(provider, model_id, name=None, context=None, max_output=None, price=(None, None)):
    return {"id": model_id, "name": name or model_id, "provider": provider["id"], "context": context,
            "max_output": max_output, "input_price": price[0], "output_price": price[1]}


def _per_million(value):
    try:
        return round(float(value) * 1_000_000, 6)
    except (TypeError, ValueError):
        return None


def fetch(provider, lookup=None):
    """The provider's models, straight from its API (raises ProviderError)."""
    lookup = lookup or keys.lookup
    kind = provider["kind"]
    models = []
    if kind == "anthropic":
        after = None
        while True:
            page = providers.get_json(provider, "/v1/models?limit=1000" + (f"&after_id={after}" if after else ""),
                                      lookup=lookup)
            for m in page.get("data", []):
                models.append(_model(provider, m["id"], m.get("display_name"), m.get("max_input_tokens"),
                                     m.get("max_tokens"), anthropic_price(m["id"])))
            if not page.get("has_more") or not page.get("last_id"):
                break
            after = page["last_id"]
    elif kind == "openrouter":
        if lookup(provider["id"]):
            providers.get_json(provider, "/key", lookup=lookup)  # the model list is public: check the key itself
        for m in providers.get_json(provider, "/models", lookup=lookup).get("data", []):
            pricing = m.get("pricing") or {}
            top = m.get("top_provider") or {}
            models.append(_model(provider, m["id"], m.get("name"), m.get("context_length"),
                                 top.get("max_completion_tokens"),
                                 (_per_million(pricing.get("prompt")), _per_million(pricing.get("completion")))))
    elif kind == "openai":
        for m in providers.get_json(provider, "/models", lookup=lookup).get("data", []):
            models.append(_model(provider, m["id"]))
    elif kind == "ollama":
        for m in providers.get_json(provider, "/api/tags", lookup=lookup).get("models", []):
            models.append(_model(provider, m.get("name") or m.get("model"), price=(0.0, 0.0)))
    return sorted(models, key=lambda m: m["id"])


# ---------------------------------------------------------------- cache

def _cache_path():
    return paths.state_dir() / "catalog.json"


def cached():
    """{provider id: {"fetched", "models", "error"}} from the last refresh."""
    try:
        data = json.loads(_cache_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def refresh(provider_ids=None, lookup=None):
    """Fetch every provider (or the given ones) and cache the result; a
    provider that fails keeps its last models and records the error."""
    cache = cached()
    for provider in providers.load():
        if provider_ids and provider["id"] not in provider_ids:
            continue
        entry = cache.get(provider["id"], {"models": []})
        try:
            entry = {"fetched": time.time(), "models": fetch(provider, lookup=lookup), "error": None}
        except providers.ProviderError as e:
            entry = {**entry, "fetched": time.time(), "error": str(e)}
        cache[provider["id"]] = entry
    known = {p["id"] for p in providers.load()}
    cache = {k: v for k, v in cache.items() if k in known}
    target = _cache_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache))
    os.replace(tmp, target)
    return cache


def all_models():
    """Claude Code's aliases, then every cached provider model."""
    models = [{**a, "provider": "claude-code", "context": None, "max_output": None,
               "input_price": None, "output_price": None} for a in CLAUDE_CODE_ALIASES]
    for entry in cached().values():
        models += entry.get("models", [])
    return models
