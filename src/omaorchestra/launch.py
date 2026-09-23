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

from . import client

# Same window class as Omarchy's own agent windows (omarchy-agent).
APP_ID = "org.omarchy.agent"


class LaunchError(Exception):
    pass


def claude_bin():
    # OMAORCHESTRA_CLAUDE lets tests launch a stand-in agent.
    return os.environ.get("OMAORCHESTRA_CLAUDE") or "claude"


def claude_command(task, session_id, permission_mode=None, model=None, extra=()):
    command = [claude_bin(), "--session-id", session_id]
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


def run(task, cwd, permission_mode=None, model=None, extra=(), spawn=subprocess.Popen, request=client.request):
    """Launch the agent; returns (session_id, tracked). `tracked` is False
    when the daemon was not running to register it."""
    if not task.strip():
        raise LaunchError("the task is empty")
    cwd = Path(cwd).expanduser().resolve()
    if not cwd.is_dir():
        raise LaunchError(f"{cwd} is not a directory")
    if not shutil.which(claude_bin()):
        raise LaunchError(f"{claude_bin()} is not installed")
    session_id = str(uuid.uuid4())
    tracked = True
    try:
        request({"cmd": "update", "session_id": session_id, "agent": "claude", "status": "working",
                 "cwd": str(cwd), "title": short(task), "task": task, "launching": True})
    except client.DaemonUnavailable:
        tracked = False
    command = terminal_command(cwd, claude_command(task, session_id, permission_mode, model, extra))
    try:
        spawn(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              start_new_session=True)
    except OSError as e:
        if tracked:
            try:
                request({"cmd": "remove", "session_id": session_id, "reason": "did-not-start"})
            except client.DaemonUnavailable:
                pass
        raise LaunchError(f"could not open a terminal: {e}") from e
    return session_id, tracked
