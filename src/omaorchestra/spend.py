"""A spending report: provider spend today, subscription limits, what each
session costs, and provider balances. Used by `omaorchestra spend` and the
app's Usage page."""

import time
from pathlib import Path

from . import client, config, costs, keys, providers, usage
from .transcript import model_name


def report(sessions=None, include_balances=True):
    settings = config.load_or_defaults()
    if sessions is None:
        try:
            sessions = client.request({"cmd": "list"})["sessions"]
        except client.DaemonUnavailable:
            sessions = []
    rows = []
    for s in sessions:
        c = costs.session_cost(s) if s.get("transcript_path") else {"usd": 0.0, "unpriced": [], "tokens": 0, "real": bool(s.get("provider"))}
        rows.append({"id": s["id"], "project": Path(s.get("cwd") or "").name or "(unknown)",
                     "model": model_name(s.get("model")), "provider": s.get("provider"),
                     "usd": c["usd"], "real": c["real"], "unpriced": c["unpriced"], "tokens": c["tokens"]})
    limits = []
    rec = usage.record("claude")
    if rec:
        for lim in rec.get("limits") or []:
            try:
                limits.append({"name": rec.get("name") or "claude", "label": lim.get("label"),
                               "percent": float(lim.get("percent")), "resetsAt": lim.get("resetsAt")})
            except (TypeError, ValueError):
                pass
    balances = []
    if include_balances:
        try:
            configured = providers.load()
        except providers.ProviderError:
            configured = []
        for p in configured:
            if p["kind"] != "openrouter":
                continue
            if not keys.lookup(p["id"]):
                balances.append({"provider": p["id"], "error": "no key stored"})
                continue
            try:
                balances.append({"provider": p["id"], **(providers.balance(p) or {})})
            except providers.ProviderError as e:
                balances.append({"provider": p["id"], "error": str(e)})
    day = costs.today()
    return {"day": day, "spent": costs.spent_today(), "budget": settings["tasks"]["daily_budget"],
            "byProvider": costs.ledger().get(day, {}), "limits": limits,
            "limitsAge": usage.age(rec) if rec else None, "sessions": rows, "balances": balances}


def reset_text(iso):
    t = usage._time(iso) if iso else None
    return t.astimezone().strftime("%a %H:%M") if t else ""


def format_report(r):
    lines = [f"Today ({r['day']})"]
    budget = f" of the ${r['budget']} daily budget" if r["budget"] else " (no daily budget)"
    lines.append(f"  provider spend: ${r['spent']:.2f}{budget}")
    for pid, usd in sorted(r["byProvider"].items()):
        lines.append(f"    {pid}: ${usd:.2f}")
    lines.append("Subscription (Omarchy's usage records)")
    if not r["limits"]:
        lines.append("  no usage record")
    for lim in r["limits"]:
        lines.append(f"  {lim['name']} {lim['label']}: {round(lim['percent'] * 100)}%"
                     + (f", resets {reset_text(lim['resetsAt'])}" if lim.get("resetsAt") else ""))
    if r["limitsAge"] is not None and r["limitsAge"] > usage.STALE_AFTER:
        lines.append(f"  (last updated {int(r['limitsAge'] // 60)} minutes ago)")
    lines.append("Sessions")
    if not r["sessions"]:
        lines.append("  none")
    for s in r["sessions"]:
        what = f"via {s['provider']}, spent" if s["real"] else "API-equivalent"
        note = f"  (no price for {', '.join(s['unpriced'])})" if s["unpriced"] else ""
        lines.append(f"  {s['id'][:8]}  {s['project']:<20} {s['model']:<10} ${s['usd']:.2f} {what}{note}")
    if r["balances"]:
        lines.append("Provider balances")
    for b in r["balances"]:
        if b.get("error"):
            lines.append(f"  {b['provider']}: {b['error']}")
            continue
        left = f", {b['limit_remaining']:.2f} of {b['limit']:.2f} left" if b.get("limit") else ""
        lines.append(f"  {b['provider']}: {b.get('usage_daily') or 0:.2f} credits used today, "
                     f"{b.get('usage') or 0:.2f} in all{left}")
    return "\n".join(lines)
