"""Handing a session's work to another agent or model.

The new session starts where the work is (the same folder, or the same
worktree, on the same branch) with a brief: the original task, where
things stand in git, and the session's latest prompts, replies and tool
calls, followed by an instruction to carry on. The old session keeps
running unless asked to stop.
"""

import subprocess
from pathlib import Path

from . import adapters, transcript

ACTIVITY_ITEMS = 20


def _git(cwd, *args):
    try:
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def brief(session):
    """The task text for the agent taking over."""
    agent = adapters.ADAPTERS.get(session.get("agent"))
    who = agent.label if agent else (session.get("agent") or "another agent")
    cwd = session.get("cwd") or ""
    lines = [f"You are taking over work that {who} was doing in {cwd}. Continue it; do not start over.", ""]
    task = session.get("task") or session.get("title")
    if task:
        lines += ["The task was:", task.strip(), ""]
    in_repo = bool(cwd) and _git(cwd, "rev-parse", "--is-inside-work-tree") == "true"
    if in_repo:
        # --show-current works before the first commit, and is empty when detached.
        branch = _git(cwd, "branch", "--show-current")
        where = f"on branch {branch}" if branch else f"detached at {_git(cwd, 'rev-parse', '--short', 'HEAD')}"
        status = _git(cwd, "status", "--short")
        lines.append(f"Git: {where}" + (" (a worktree made for this task)" if session.get("worktree") else ""))
        lines += (["Uncommitted changes:", status, ""] if status else ["No uncommitted changes.", ""])
    recent = transcript.activity(session["transcript_path"], limit=ACTIVITY_ITEMS) if session.get("transcript_path") else []
    if recent:
        lines.append("Its latest activity, oldest first:")
        lines += [f"- {item['kind']}: {item['text']}" for item in recent]
        lines.append("")
    lines.append("Check the current state of the files before changing anything, then carry on from where it stopped.")
    return "\n".join(lines)


def workdir(session):
    """Where the new agent works: the session's own folder."""
    cwd = session.get("cwd")
    if not cwd or not Path(cwd).is_dir():
        raise ValueError(f"session {session['id'][:8]} has no folder to continue in")
    return cwd
