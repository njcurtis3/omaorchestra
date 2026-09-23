"""How sessions are ordered and labelled in the app. No Qt here, so it is testable anywhere."""

import os
import time
from datetime import datetime

from ..transcript import model_name

# Waiting first (it needs you), then working, then idle.
STATUS_ORDER = {"needs-input": 0, "working": 1, "idle": 2}
STATUS_LABEL = {"needs-input": "Waiting", "working": "Working", "idle": "Idle"}


def project(cwd):
    path = str(cwd or "").rstrip("/")
    if not cwd:
        return "(unknown)"
    return os.path.basename(path) or "/"


def row(session):
    """A session plus the derived fields the dashboard shows."""
    return {
        **session,
        "project": project(session.get("cwd")),
        "statusLabel": STATUS_LABEL.get(session.get("status"), str(session.get("status") or "")),
        "modelName": model_name(session.get("model")),
        "since": session.get("status_since") or session.get("updated") or session.get("started") or 0,
    }


def ordered(sessions):
    """Status groups in priority order; within a group, the most recent to
    enter that status first. Ordered by status_since, not updated, so rows do
    not jump about while an agent works (every tool call bumps `updated`)."""
    return sorted(
        (row(s) for s in sessions),
        key=lambda r: (STATUS_ORDER.get(r.get("status"), 3), -r["since"]),
    )


def matches(r, status_filter, text):
    if status_filter and r.get("status") != status_filter:
        return False
    needle = (text or "").strip().lower()
    if not needle:
        return True
    haystack = " ".join(str(r.get(k) or "") for k in ("project", "title", "cwd", "branch", "modelName", "agent", "id"))
    return needle in haystack.lower()


def timeline(session):
    """Status history, oldest first, with how long each status lasted
    (`until` is None for the current one)."""
    history = session.get("history") or []
    if not history and session.get("status"):
        history = [{"status": session["status"], "at": session.get("status_since") or session.get("started") or 0}]
    items = []
    for i, h in enumerate(history):
        until = history[i + 1]["at"] if i + 1 < len(history) else None
        items.append({"status": h["status"], "label": STATUS_LABEL.get(h["status"], h["status"]),
                      "at": h["at"], "until": until})
    return items


def clock(timestamp):
    """Local time of day for an epoch timestamp."""
    return time.strftime("%H:%M:%S", time.localtime(timestamp)) if timestamp else ""


def iso_clock(iso):
    """Local time of day for a transcript timestamp such as 2026-09-23T21:17:03.511Z."""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%H:%M:%S")
    except (ValueError, AttributeError):
        return ""


def duration(seconds):
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h {s % 3600 // 60}m"
    return f"{s // 86400}d {s % 86400 // 3600}h"
