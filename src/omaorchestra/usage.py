"""Subscription rate limits, from the usage records Omarchy keeps.

Omarchy's agents bar widget runs `omarchy-agent-usage-update`, which writes
one JSON record per agent to ~/.local/state/omarchy/agents/usage/<agent>.json.
Each has `limits`: [{"label", "percent" (0 to 1), "resetsAt" (ISO time)}].
The queue reads them to hold off starting tasks when a limit is nearly used.
"""

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

# A record this old tells us nothing about now: ignore it rather than block.
STALE_AFTER = 60 * 60
# Ask Omarchy to refresh a record older than this when it matters.
REFRESH_AFTER = 15 * 60


def usage_dir():
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(base) / "omarchy" / "agents" / "usage"


def _time(iso):
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def record(agent):
    try:
        data = json.loads((usage_dir() / f"{agent}.json").read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def age(rec, now=None):
    """Seconds since the record was written, or None if unknown."""
    now = now or datetime.now(timezone.utc)
    updated = _time(rec.get("updatedAt")) if rec else None
    return (now - updated).total_seconds() if updated else None


def blocking(agent, threshold, now=None, rec=None):
    """The limit that should hold new tasks for `agent`, or None.

    `threshold` is a fraction (0.9 = 90%); 0 or less turns this off. A limit
    whose reset time has passed no longer counts, and a missing or stale
    record never blocks.
    """
    if threshold <= 0:
        return None
    now = now or datetime.now(timezone.utc)
    rec = rec if rec is not None else record(agent)
    if not rec or not rec.get("ready", True):
        return None
    record_age = age(rec, now)
    if record_age is None or record_age > STALE_AFTER:
        return None
    worst = None
    for limit in rec.get("limits") or []:
        try:
            percent = float(limit.get("percent"))
        except (TypeError, ValueError):
            continue
        resets = _time(limit.get("resetsAt")) if limit.get("resetsAt") else None
        if resets and resets <= now:
            continue
        if percent >= threshold and (worst is None or percent > worst["percent"]):
            worst = {"agent": agent, "name": rec.get("name") or agent, "label": limit.get("label") or "limit",
                     "percent": percent, "resetsAt": limit.get("resetsAt")}
    return worst


def describe(block):
    """ "Claude Code's Session (5-hour) limit is at 92%, resets 19:39" """
    text = f"{block['name']}'s {block['label']} limit is at {round(block['percent'] * 100)}%"
    resets = _time(block.get("resetsAt")) if block.get("resetsAt") else None
    if resets:
        text += ", resets " + resets.astimezone().strftime("%H:%M")
    return text


def refresh_in_background(agent):
    """Ask Omarchy to refresh one agent's limits, without waiting."""
    updater = shutil.which("omarchy-agent-usage-update") or "/usr/share/omarchy/bin/omarchy-agent-usage-update"
    if not os.path.exists(updater):
        return False
    try:
        subprocess.Popen([updater, "--limits-only", agent], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return False
    return True
