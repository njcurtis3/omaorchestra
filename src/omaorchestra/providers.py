"""Model providers: the APIs agents (and the model catalog) can use.

Each provider has a kind, which fixes how it is spoken to, and a base URL.
The list lives in ~/.config/omaorchestra/providers.json (no secrets); keys
live in the system keyring (see keys.py). omaorchestra itself never sends
prompts to a provider: it lists models, checks keys, and reads credit and
usage figures. Plain HTTP from the standard library, no SDKs.
"""

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

from . import keys

TIMEOUT = 15

# kind -> defaults. `key`: whether the provider needs an API key.
KINDS = {
    "anthropic": {"label": "Anthropic API", "base_url": "https://api.anthropic.com", "key": True},
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "key": True},
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "key": True},
    "ollama": {"label": "Ollama (local)", "base_url": "http://localhost:11434", "key": False},
}


class ProviderError(Exception):
    pass


def path():
    if os.environ.get("OMAORCHESTRA_PROVIDERS"):
        return Path(os.environ["OMAORCHESTRA_PROVIDERS"])
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "omaorchestra" / "providers.json"


def load():
    try:
        data = json.loads(path().read_text())
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:
        raise ProviderError(f"{path()} is not valid JSON ({e})") from e
    items = data.get("providers", []) if isinstance(data, dict) else []
    return [p for p in items if isinstance(p, dict) and p.get("id") and p.get("kind") in KINDS]


def _save(items):
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps({"providers": items}, indent=2) + "\n")
    os.replace(tmp, target)


def get(provider_id):
    for p in load():
        if p["id"] == provider_id:
            return p
    raise ProviderError(f"no provider {provider_id} (see `omaorchestra provider list`)")


def add(kind, provider_id=None, base_url=None, label=None):
    if kind not in KINDS:
        raise ProviderError(f"unknown kind {kind} (known: {', '.join(KINDS)})")
    provider_id = provider_id or kind
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,39}", provider_id):
        raise ProviderError("a provider id is lowercase letters, digits, - and _")
    items = load()
    if any(p["id"] == provider_id for p in items):
        raise ProviderError(f"there is already a provider {provider_id}")
    url = (base_url or KINDS[kind]["base_url"]).rstrip("/")
    if not re.match(r"https?://", url):
        raise ProviderError("the base URL must start with http:// or https://")
    item = {"id": provider_id, "kind": kind, "base_url": url, "label": label or KINDS[kind]["label"]}
    _save(items + [item])
    return item


def remove(provider_id):
    items = load()
    if not any(p["id"] == provider_id for p in items):
        raise ProviderError(f"no provider {provider_id}")
    _save([p for p in items if p["id"] != provider_id])
    keys.clear(provider_id)


def needs_key(provider):
    return KINDS[provider["kind"]]["key"]


# ---------------------------------------------------------------- HTTP

def _headers(provider, key):
    if provider["kind"] == "anthropic":
        return {"x-api-key": key, "anthropic-version": "2023-06-01"} if key else {"anthropic-version": "2023-06-01"}
    return {"Authorization": f"Bearer {key}"} if key else {}


def get_json(provider, route, key=None, lookup=None):
    """GET <base_url><route> as JSON, with the provider's key when it has one."""
    lookup = lookup or keys.lookup
    if key is None and needs_key(provider):
        key = lookup(provider["id"])
    url = provider["base_url"] + route
    request = urllib.request.Request(url, headers={**_headers(provider, key), "Accept": "application/json",
                                                   "User-Agent": "omaorchestra"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ProviderError("the API key was rejected" if key else "this provider needs an API key") from None
        raise ProviderError(f"{url} answered {e.code} {e.reason}") from None
    except urllib.error.URLError as e:
        raise ProviderError(f"cannot reach {provider['base_url']} ({e.reason})") from None
    except (TimeoutError, OSError) as e:
        raise ProviderError(f"cannot reach {provider['base_url']} ({e})") from None
    except ValueError as e:
        raise ProviderError(f"{url} did not return JSON") from e


def balance(provider, lookup=None):
    """What the provider reports about spend on this key, or None when it
    reports nothing (only OpenRouter does, in credits, via GET /key)."""
    if provider["kind"] != "openrouter":
        return None
    data = get_json(provider, "/key", lookup=lookup).get("data") or {}
    return {k: data.get(k) for k in ("usage", "usage_daily", "usage_weekly", "usage_monthly", "limit",
                                     "limit_remaining") if k in data}


def test(provider, lookup=None):
    """Check a provider end to end: reachable, key accepted. Returns a
    one-line result; raises ProviderError on failure."""
    lookup = lookup or keys.lookup
    if needs_key(provider) and not lookup(provider["id"]):
        raise ProviderError(f"no API key stored for {provider['id']} (`omaorchestra provider key {provider['id']}`)")
    from . import catalog
    models = catalog.fetch(provider, lookup=lookup)  # OpenRouter's also checks its key
    return f"{provider['label']}: connected, {len(models)} model(s)"
