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

from . import adapters, client, config, modeldefaults, providers, recent, routing, worktrees

LAUNCH_VARIABLE = "OMAORCHESTRA_LAUNCH_ID"

# Same window class as Omarchy's own agent windows (omarchy-agent).
APP_ID = "org.omarchy.agent"


class LaunchError(Exception):
    pass


def terminal_command(cwd, command):
    """Open `command` in the user's default terminal, as Omarchy does."""
    return ["setsid", "uwsm-app", "--", "xdg-terminal-exec", f"--app-id={APP_ID}", f"--dir={cwd}", "-e", *command]


def short(task, limit=80):
    text = " ".join(task.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def agent_environment(agent="claude"):
    """What a queued task needs to launch later as if launched now: the
    agent's full path and PATH (the daemon's own PATH, from systemd, usually
    lacks version-manager folders)."""
    return {"agent_bin": shutil.which(adapters.get(agent).binary()), "path": os.environ.get("PATH")}


def resume(record, spawn=subprocess.Popen, request=client.request, path=None):
    """Reopen an ended session's conversation (a history record) in its folder,
    in a new terminal. Returns {"id", "tracked", "folder"}.

    The session is registered up front under its old id, marked as resumed
    from its record; the agent reports under the same id once it starts."""
    from . import history
    adapter = adapters.get(record.get("agent") or "claude")
    folder = history.workdir(record)
    if not folder:
        raise LaunchError(f"its folder {record.get('cwd') or '(unknown)'} is gone (a removed worktree?)")
    agent_bin = adapter.binary()
    if not shutil.which(agent_bin, path=path):
        raise LaunchError(f"{agent_bin} is not installed")
    session_id = record["id"]
    tracked = True
    try:
        request({"cmd": "update", "session_id": session_id, "agent": adapter.name, "status": "idle",
                 "cwd": folder, "title": record.get("title"), "task": record.get("task"), "launching": True,
                 "model": record.get("model"), "provider": record.get("provider"),
                 "worktree": record.get("worktree"), "resumed_from": record.get("ended")})
    except client.DaemonUnavailable:
        tracked = False
    command = terminal_command(folder, adapter.resume_command(session_id, folder, agent_bin))
    env = {**os.environ, **({"PATH": path} if path else {}), LAUNCH_VARIABLE: session_id}
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
    return {"id": session_id, "tracked": tracked, "folder": folder}


def run(task, cwd, permission_mode=None, model=None, extra=(), worktree=None,
        spawn=subprocess.Popen, request=client.request, agent_bin=None, path=None, provider=None,
        mcp_profile=None, agent="claude", session_fields=None):
    """Launch the agent. Returns {"id", "tracked", "worktree", "note"}:
    `tracked` is False when the daemon was not running to register it;
    `worktree` is the worktree record when the task got one.

    `worktree=None` follows the `tasks.isolate_with_worktrees` setting; a
    folder outside a git repository never gets one. `provider` (an id) runs
    the agent through that API provider instead of its subscription; its
    key is read from the keyring now and passed only in the agent's
    environment.
    """
    try:
        adapter = adapters.get(agent)
    except KeyError as e:
        raise LaunchError(str(e.args[0])) from e
    if provider and not adapter.supports_routing:
        raise LaunchError(f"{adapter.label} cannot run through a provider from omaorchestra (yet)")
    if mcp_profile and not adapter.supports_mcp_profile:
        raise LaunchError(f"{adapter.label} cannot be started with an MCP profile (yet)")
    if not task.strip():
        raise LaunchError("the task is empty")
    cwd = Path(cwd).expanduser().resolve()
    if not cwd.is_dir():
        raise LaunchError(f"{cwd} is not a directory")
    agent_bin = agent_bin or adapter.binary()
    if not shutil.which(agent_bin, path=path):
        raise LaunchError(f"{agent_bin} is not installed")
    if worktree is None:
        worktree = config.load_or_defaults()["tasks"]["isolate_with_worktrees"]
    route_env = {}
    if provider:
        try:
            route_env = routing.claude_code_env(providers.get(provider), model)
        except (providers.ProviderError, routing.RoutingError) as e:
            raise LaunchError(str(e)) from e
    elif not model and adapter.name == "claude":
        # Folder and global defaults name Claude subscription models; a routed
        # task uses the provider's model ids, and other agents their own.
        model = modeldefaults.for_folder(cwd)[0]
    session_id = str(uuid.uuid4())
    if mcp_profile:
        # Exactly the profile's MCP servers, instead of the agent's own.
        from .mcp import registry as mcp_registry
        try:
            config_file = mcp_registry.write_profile_config(mcp_profile, session_id)
        except mcp_registry.McpError as e:
            raise LaunchError(str(e)) from e
        extra = ["--mcp-config", str(config_file), "--strict-mcp-config", *extra]
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
        request({"cmd": "update", "session_id": session_id, "agent": adapter.name, "status": "working",
                 "cwd": str(workdir), "title": short(task), "task": task, "launching": True,
                 "model": model or None, "provider": provider or None,
                 "worktree": record["path"] if record else None, **(session_fields or {})})
    except client.DaemonUnavailable:
        tracked = False
    command = terminal_command(workdir, adapter.command(task, session_id, workdir, model, permission_mode, extra,
                                                        agent_bin))
    # The launch id travels in the agent's environment: its hooks inherit it
    # and report it, which ties the agent's own session id to this placeholder.
    env = {**os.environ, **({"PATH": path} if path else {}), **route_env, LAUNCH_VARIABLE: session_id}
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
