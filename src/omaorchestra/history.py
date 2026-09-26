"""Session history: one record per ended session, in history.jsonl.

When a session ends or is removed, the daemon appends a record of what it
was and how it went: its folder and task, agent, model and provider, when it
ran and how long it spent working, waiting for you and idle, what it cost,
the commits it made, how often it waited for you, why it ended, and the
outcome. The transcript stays where the agent keeps it; the record points to
it and never copies it.

[history] keep_days prunes old records (0 keeps them all), and
[history] titles = false keeps task text out of the records altogether.
"""

import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from . import paths

VERSION = 1
TASK_LIMIT = 2000
MAX_DIFF_BYTES = 200 * 1024
GIT_TIMEOUT = 10

# Why a session ended (daemon reasons) -> outcome, by its last status.
OUTCOMES = ("finished", "stopped", "crashed", "never-started", "dismissed")


class HistoryError(Exception):
    pass


def path():
    return paths.state_dir() / "history.jsonl"


def git(cwd, *args):
    """git's output, or None when it fails (not a repository, no git...)."""
    try:
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                                timeout=GIT_TIMEOUT, errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def head(cwd):
    """The commit a folder is at (recorded when a session starts)."""
    return git(cwd, "rev-parse", "HEAD") if cwd and Path(cwd).is_dir() else None


# ---------------------------------------------------------------- building a record

def outcome(session, reason):
    """How the session ended: finished, stopped, crashed, never-started, or
    dismissed (removed from the list while still busy)."""
    last = session.get("status")
    if reason == "did-not-start" or (session.get("launching") and "pid" not in session):
        return "never-started"
    if session.get("stopping"):
        return "stopped"  # stopped through omaorchestra
    if reason == "session-end":
        return "finished" if last == "idle" else "stopped"  # the user quit it mid-work
    if reason == "dismissed":
        return "finished" if last == "idle" else "dismissed"
    # The process went away: after its work (a closed terminal), or during it.
    return "finished" if last == "idle" else "crashed"


def time_per_status(session, ended):
    """Seconds spent in each status, from the session's status history."""
    history = session.get("history") or []
    if not history:
        history = [{"status": session.get("status"), "at": session.get("status_since") or session.get("started")}]
    seconds = {}
    for i, entry in enumerate(history):
        until = history[i + 1]["at"] if i + 1 < len(history) else ended
        if entry.get("status") and entry.get("at") is not None and until is not None:
            seconds[entry["status"]] = round(seconds.get(entry["status"], 0) + max(0, until - entry["at"]), 1)
    return seconds


def commits_made(session, git_fn=git):
    """{"from", "to", "commits"}: the commits between the session's start and
    its end in its folder, or None when there is no way to tell."""
    start, cwd = session.get("git_start"), session.get("cwd")
    if not start or not cwd or not Path(cwd).is_dir():
        return None
    end = git_fn(cwd, "rev-parse", "HEAD")
    if not end:
        return None
    count = git_fn(cwd, "rev-list", "--count", f"{start}..{end}")
    return {"from": start, "to": end, "commits": int(count) if count and count.isdigit() else 0}


def build(session, reason, ended=None, cost_fn=None, git_fn=git, titles=True):
    """The history record for a session that just ended."""
    ended = time.time() if ended is None else ended
    cost = None
    if cost_fn and session.get("transcript_path"):
        try:
            found = cost_fn(session)
            cost = {"usd": round(found["usd"], 4), "real": bool(found.get("real"))}
        except (OSError, KeyError, TypeError, ValueError):
            cost = None
    cwd = session.get("cwd") or ""
    record = {
        "v": VERSION, "id": session["id"], "agent": session.get("agent") or "claude",
        "project": os.path.basename(cwd.rstrip("/")) or cwd, "cwd": cwd,
        "model": session.get("model"), "provider": session.get("provider"),
        "started": session.get("started"), "ended": ended,
        "seconds": time_per_status(session, ended), "cost": cost,
        "worktree": session.get("worktree"), "branch": session.get("branch"),
        "git": commits_made(session, git_fn),
        "waits": sum(1 for h in session.get("history") or [] if h.get("status") == "needs-input"),
        "reason": reason, "outcome": outcome(session, reason),
        "transcript": session.get("transcript_path"),
    }
    for key in ("resumed_from", "chain", "step", "review"):
        if session.get(key):
            record[key] = session[key]
    if titles:
        record["title"] = session.get("title")
        task = session.get("task")
        record["task"] = task[:TASK_LIMIT] if isinstance(task, str) else None
    return record


def append(record, target=None):
    target = Path(target or path())
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a") as f:
        f.write(json.dumps(record) + "\n")


# ---------------------------------------------------------------- reading

def load(target=None):
    """Every record, in the order they ended (oldest first); lines that do
    not parse are skipped."""
    try:
        lines = Path(target or path()).read_text().splitlines()
    except OSError:
        return []
    records = []
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("id"):
            records.append(record)
    return sorted(records, key=lambda r: r.get("ended") or 0)


def model_label(model):
    """"claude-opus-5-5" -> "Opus 5.5"; other agents' models as they are."""
    from .transcript import model_name
    return model_name(model) if model and model.startswith("claude") else (model or "")


def since_seconds(text, now=None):
    """"7d", "12h", "2w", "30m" or a date (2026-09-01) -> a timestamp."""
    now = time.time() if now is None else now
    match = re.fullmatch(r"\s*(\d+)\s*([mhdw])\s*", text or "")
    if match:
        return now - int(match.group(1)) * {"m": 60, "h": 3600, "d": 86400, "w": 604800}[match.group(2)]
    try:
        return datetime.fromisoformat(text.strip()).timestamp()
    except (ValueError, AttributeError):
        raise HistoryError(f"cannot read {text!r} as a time: use 7d, 12h, 2w or a date like 2026-09-01") from None


def haystack(record):
    return " ".join(str(record.get(k) or "") for k in
                    ("id", "project", "cwd", "title", "task", "model", "provider", "branch", "agent", "outcome"))


def matches(record, project=None, agent=None, since=None, search=None, outcome=None):
    if project and project.lower() not in (record.get("project") or "").lower() \
            and project not in (record.get("cwd") or ""):
        return False
    if agent and record.get("agent") != agent:
        return False
    if since is not None and (record.get("ended") or 0) < since:
        return False
    if outcome and record.get("outcome") != outcome:
        return False
    if search and search.strip().lower() not in haystack(record).lower():
        return False
    return True


def find(key, records=None):
    """The latest record whose id starts with `key`."""
    records = load() if records is None else records
    found = [r for r in records if r["id"].startswith((key or "").strip())] if key else []
    if not found:
        raise HistoryError(f"no session {key} in the history")
    ids = {r["id"] for r in found}
    if len(ids) > 1:
        raise HistoryError(f"{key} matches {len(ids)} sessions; give more of the id")
    return found[-1]


def worked(record):
    s = record.get("seconds") or {}
    return s.get("working", 0)


def length(record):
    return max(0, (record.get("ended") or 0) - (record.get("started") or record.get("ended") or 0))


def workdir(record):
    """Where the session ran, if that folder is still there."""
    cwd = record.get("cwd")
    return cwd if cwd and Path(cwd).is_dir() else None


def changes(record, max_bytes=MAX_DIFF_BYTES):
    """What the session committed, in the shape of changes.uncommitted()."""
    result = {"repo": True, "stat": "", "diff": "", "untracked": [], "truncated": False, "error": "",
              "commits": []}
    span, cwd = record.get("git"), workdir(record)
    if not span:
        result["repo"] = bool(cwd and git(cwd, "rev-parse", "--is-inside-work-tree") == "true")
        if result["repo"]:
            result["error"] = "not recorded: the session started before history kept its commits"
        return result
    if not cwd:
        result["error"] = "its folder is gone (a removed worktree?)"
        return result
    rng = f"{span['from']}..{span['to']}"
    log = git(cwd, "log", "--format=%h %s", rng)
    if log is None:
        result["error"] = "those commits are no longer in this repository"
        return result
    result["commits"] = log.split("\n") if log else []
    result["stat"] = git(cwd, "diff", "--stat", span["from"], span["to"]) or ""
    diff = git(cwd, "diff", span["from"], span["to"]) or ""
    if len(diff.encode()) > max_bytes:
        diff = diff.encode()[:max_bytes].decode(errors="ignore")
        result["truncated"] = True
    result["diff"] = diff
    return result


# ---------------------------------------------------------------- retention

def prune(keep_days, now=None, target=None):
    """Drop records that ended more than `keep_days` ago (0 keeps all).
    Returns how many were dropped."""
    if not keep_days:
        return 0
    target = Path(target or path())
    records = load(target)
    cutoff = (time.time() if now is None else now) - keep_days * 86400
    kept = [r for r in records if (r.get("ended") or 0) >= cutoff]
    if len(kept) == len(records):
        return 0
    tmp = target.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in kept))
    os.replace(tmp, target)
    return len(records) - len(kept)


def forget_titles(target=None):
    """Remove task text from every record (after titles is turned off)."""
    target = Path(target or path())
    records = load(target)
    if not any(r.get("title") or r.get("task") for r in records):
        return 0
    for r in records:
        r.pop("title", None)
        r.pop("task", None)
    tmp = target.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in records))
    os.replace(tmp, target)
    return len(records)


# ---------------------------------------------------------------- stats

def stats(records, answered=()):
    """Time and cost per project, agent and model, and waits: how often
    sessions waited for you and how long you took (`answered` is
    permissions.record(), requests with "waited" seconds)."""
    def group(key):
        rows = {}
        for r in records:
            name = r.get(key) or "(unknown)"
            row = rows.setdefault(name, {"name": name, "sessions": 0, "working": 0.0, "length": 0.0,
                                         "usd": 0.0, "estimated": False})
            row["sessions"] += 1
            row["working"] += worked(r)
            row["length"] += length(r)
            if r.get("cost"):
                row["usd"] += r["cost"].get("usd") or 0
                row["estimated"] = row["estimated"] or not r["cost"].get("real")
        return sorted(rows.values(), key=lambda row: (-row["working"], row["name"]))

    waited = sorted(e["waited"] for e in answered if e.get("waited") is not None)
    median = waited[len(waited) // 2] if waited else None
    return {
        "sessions": len(records),
        "outcomes": {o: sum(1 for r in records if r.get("outcome") == o) for o in OUTCOMES},
        "by_project": group("project"), "by_agent": group("agent"), "by_model": group("model"),
        "waits": {"total": sum(r.get("waits") or 0 for r in records),
                  "per_session": round(sum(r.get("waits") or 0 for r in records) / len(records), 1) if records else 0,
                  "answered": len(waited), "median_seconds": median,
                  "longest_seconds": waited[-1] if waited else None},
    }
