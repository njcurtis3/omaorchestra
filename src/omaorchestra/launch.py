"""Start an agent on a task, in a new terminal window.

The session id is chosen here and passed to the agent, so the session can be
registered before the agent starts and shows up at once; the agent's first
hook then attaches its process. A registration that no agent ever claims is
dropped after LAUNCH_TIMEOUT (see Registry.prune).
"""

import os
import shutil
import subprocess
import uuid
from pathlib import Path

from . import client, config, recent, worktrees

# Same window class as Omarchy's own agent windows (omarchy-agent).
APP_ID = "org.omarchy.agent"


class LaunchError(Exception):
    pass


def claude_bin():
    # OMAORCHESTRA_CLAUDE lets tests launch a stand-in agent.
    return os.environ.get("OMAORCHESTRA_CLAUDE") or "claude"


def claude_command(task, session_id, permission_mode=None, model=None, extra=(), agent_bin=None):
    command = [agent_bin or claude_bin(), "--session-id", session_id]
    if permission_mode:
        command += ["--permission-mode", permission_mode]
    if model:
        command += ["--model", model]
    return command + list(extra) + ["--", task]


def terminal_command(cwd, command):
    """Open `command` in the user's default terminal, as Omarchy does."""
    return ["setsid", "uwsm-app", "--", "xdg-terminal-exec", f"--app-id={APP_ID}", f"--dir={cwd}", "-e", *command]


def short(task, limit=80):
    text = " ".join(task.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def agent_environment():
    """What a queued task needs to launch later as if launched now: the
    agent's full path and PATH (the daemon's own PATH, from systemd, usually
    lacks version-manager folders)."""
    return {"agent_bin": shutil.which(claude_bin()), "path": os.environ.get("PATH")}


def run(task, cwd, permission_mode=None, model=None, extra=(), worktree=None,
        spawn=subprocess.Popen, request=client.request, agent_bin=None, path=None):
    """Launch the agent. Returns {"id", "tracked", "worktree", "note"}:
    `tracked` is False when the daemon was not running to register it;
    `worktree` is the worktree record when the task got one.

    `worktree=None` follows the `tasks.isolate_with_worktrees` setting; a
    folder outside a git repository never gets one.
    """
    if not task.strip():
        raise LaunchError("the task is empty")
    cwd = Path(cwd).expanduser().resolve()
    if not cwd.is_dir():
        raise LaunchError(f"{cwd} is not a directory")
    agent_bin = agent_bin or claude_bin()
    if not shutil.which(agent_bin, path=path):
        raise LaunchError(f"{agent_bin} is not installed")
    if worktree is None:
        worktree = config.load_or_defaults()["tasks"]["isolate_with_worktrees"]
    session_id = str(uuid.uuid4())
    record, note, workdir = None, "", cwd
    if worktree:
        if worktrees.repo_root(cwd) is None:
            note = "not a git repository, so no separate worktree"
        else:
            try:
                record = worktrees.create(cwd, task, session_id)
            except worktrees.WorktreeError as e:
                raise LaunchError(f"could not create a worktree: {e}") from e
            workdir = Path(record["workdir"])
    tracked = True
    try:
        request({"cmd": "update", "session_id": session_id, "agent": "claude", "status": "working",
                 "cwd": str(workdir), "title": short(task), "task": task, "launching": True,
                 "worktree": record["path"] if record else None})
    except client.DaemonUnavailable:
        tracked = False
    command = terminal_command(workdir, claude_command(task, session_id, permission_mode, model, extra, agent_bin))
    env = {**os.environ, "PATH": path} if path else None
    try:
        spawn(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              start_new_session=True, env=env)
    except OSError as e:
        if tracked:
            try:
                request({"cmd": "remove", "session_id": session_id, "reason": "did-not-start"})
            except client.DaemonUnavailable:
                pass
        raise LaunchError(f"could not open a terminal: {e}") from e
    try:
        recent.add(cwd)
    except OSError:
        pass  # only a convenience
    return {"id": session_id, "tracked": tracked, "worktree": record, "note": note}
