"""What agent sessions cost, and what has been spent today.

A session's cost is its transcript's token usage times the model's price per
million tokens. For a session run through an API provider that is real
spend; for a subscription session it is only what the same work would cost
on the API ("API-equivalent"), since subscriptions are not billed per token.

Prompt caching is priced as Anthropic prices it by default: writing to the
cache at 1.25x the input price (the 5-minute cache; the 1-hour cache costs
2x, so long-cache sessions are slightly under-counted), reading at 0.1x.
"""

import json
import os
import time

from . import catalog, paths

CACHE_WRITE = 1.25
CACHE_READ = 0.1

_usage_cache = {}  # transcript path -> ((size, mtime), usage)


def transcript_usage(path):
    """{model: {"input", "output", "cache_write", "cache_read"}} for a
    transcript. Each API message is counted once (Claude Code writes one
    entry per content block, repeating the message's usage)."""
    try:
        st = os.stat(path)
    except (OSError, TypeError):
        return {}
    stamp = (st.st_size, st.st_mtime)
    cached = _usage_cache.get(path)
    if cached and cached[0] == stamp:
        return cached[1]
    usage, seen = {}, set()
    with open(path, "rb") as f:
        for line in f:
            if b'"usage"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            message = entry.get("message") if isinstance(entry, dict) else None
            if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
                continue
            key = message.get("id") or entry.get("uuid")
            if key in seen:
                continue
            seen.add(key)
            model = message.get("model") or "unknown"
            if model == "<synthetic>":
                continue
            u = message["usage"]
            totals = usage.setdefault(model, {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0})
            totals["input"] += int(u.get("input_tokens") or 0)
            totals["output"] += int(u.get("output_tokens") or 0)
            totals["cache_write"] += int(u.get("cache_creation_input_tokens") or 0)
            totals["cache_read"] += int(u.get("cache_read_input_tokens") or 0)
    _usage_cache[path] = (stamp, usage)
    return usage


def price_for(model, provider_id=None):
    """(input, output) USD per million tokens, or (None, None)."""
    if provider_id:
        for m in catalog.cached().get(provider_id, {}).get("models", []):
            if m["id"] == model:
                return m["input_price"], m["output_price"]
    return catalog.anthropic_price(model)


def cost(usage, provider_id=None):
    """{"usd", "unpriced": [models], "tokens"} for a transcript's usage."""
    usd, unpriced, tokens = 0.0, [], 0
    for model, u in usage.items():
        tokens += sum(u.values())
        price_in, price_out = price_for(model, provider_id)
        if price_in is None or price_out is None:
            unpriced.append(model)
            continue
        usd += (u["input"] * price_in + u["output"] * price_out
                + u["cache_write"] * price_in * CACHE_WRITE + u["cache_read"] * price_in * CACHE_READ) / 1_000_000
    return {"usd": round(usd, 4), "unpriced": unpriced, "tokens": tokens}


def session_cost(session):
    """The session's cost, with "real" True when it is actual provider spend."""
    result = cost(transcript_usage(session.get("transcript_path")), session.get("provider"))
    result["real"] = bool(session.get("provider"))
    return result


# ---------------------------------------------------------------- ledger

def _ledger_path():
    return paths.state_dir() / "spend.json"


def today():
    return time.strftime("%Y-%m-%d")


def ledger():
    """{date: {provider id: usd}} of real (provider) spend."""
    try:
        data = json.loads(_ledger_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def record(provider_id, usd, day=None):
    if usd <= 0:
        return
    data = ledger()
    day = day or today()
    data.setdefault(day, {})
    data[day][provider_id] = round(data[day].get(provider_id, 0.0) + usd, 4)
    # Keep about three months.
    for old in sorted(data)[:-92]:
        del data[old]
    target = _ledger_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, target)


def spent_today():
    return round(sum(ledger().get(today(), {}).values()), 4)
