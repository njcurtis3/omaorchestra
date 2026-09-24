import json
import os
import time
from pathlib import Path

STATUSES = ("idle", "working", "needs-input")


class Registry:
    """Known agent sessions, keyed by session id and persisted as JSON."""

    def __init__(self, path: Path):
        self.path = path
        self.sessions = {}
        if path.exists():
            try:
                self.sessions = json.loads(path.read_text())
            except (OSError, ValueError):
                self.sessions = {}

    # Optional facts a session may carry; None leaves the stored value alone.
    EXTRA = ("transcript_path", "model", "branch", "title", "task", "launching", "worktree", "provider")
    # A session registered by `omaorchestra run` that no agent has claimed
    # (reported a process for) by then is dropped: the launch failed.
    LAUNCH_TIMEOUT = 60
    HISTORY_LIMIT = 100

    def update(self, session_id, agent, status, cwd=None, message=None, pid=None, pid_start=None, **extra):
        if status not in STATUSES:
            raise ValueError(f"unknown status: {status}")
        now = time.time()
        session = self.sessions.setdefault(session_id, {"id": session_id, "agent": agent, "started": now})
        if session.get("status") != status:
            session["status_since"] = now
            history = session.setdefault("history", [])
            history.append({"status": status, "at": now})
            del history[: -self.HISTORY_LIMIT]
        session.update(status=status, updated=now, message=message)
        if cwd:
            session["cwd"] = cwd
        if pid is not None and pid_start is not None:
            session["pid"], session["pid_start"] = pid, pid_start
            session.pop("launching", None)  # the agent has started and claimed it
        for key in self.EXTRA:
            if extra.get(key) is not None:
                session[key] = extra[key]
        self.save()
        return session

    def prune(self, is_alive, now=None):
        """Drop sessions whose agent process is gone, and launches no agent
        claimed in time; return them by id.

        Other sessions without a recorded process are kept: there is nothing
        to check them against.
        """
        now = time.time() if now is None else now
        dead = {
            sid: s for sid, s in self.sessions.items()
            if ("pid" in s and not is_alive(s["pid"], s["pid_start"]))
            or ("pid" not in s and s.get("launching") and now - s.get("started", now) > self.LAUNCH_TIMEOUT)
        }
        for sid in dead:
            del self.sessions[sid]
        if dead:
            self.save()
        return dead

    def attach_process(self, session_id, pid, pid_start):
        """Record a launched agent's process before its first hook arrives
        (it stays `launching` until then)."""
        session = self.sessions.get(session_id)
        if session is None:
            return None
        session["pid"], session["pid_start"] = pid, pid_start
        self.save()
        return session

    def remove(self, session_id):
        removed = self.sessions.pop(session_id, None)
        if removed:
            self.save()
        return removed

    def list(self):
        return sorted(self.sessions.values(), key=lambda s: s["started"])

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.sessions, indent=2))
        os.replace(tmp, self.path)
