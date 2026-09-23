"""Find and focus the Hyprland window an agent session runs in.

The agent's terminal window is found by walking up the process tree from the
agent's PID to the nearest process that owns a window. Terminals that run one
process per window (foot, alacritty, kitty by default) give an exact match;
single-process terminals (ghostty, foot --server) own several windows, and
then the match is only a best guess.
"""

import json
import subprocess

from . import procs


class WindowError(Exception):
    pass


def hyprctl(*args, run=subprocess.run):
    try:
        result = run(["hyprctl", *args], capture_output=True, text=True)
    except FileNotFoundError as e:
        raise WindowError("hyprctl not found; focusing needs Hyprland") from e
    return result


def clients(run=subprocess.run):
    result = hyprctl("clients", "-j", run=run)
    try:
        return json.loads(result.stdout)
    except ValueError as e:
        raise WindowError(f"could not read Hyprland windows: {result.stderr.strip() or result.stdout.strip()}") from e


def find_window(agent_pid, windows, parent=procs.parent, max_depth=32):
    """(window, exact) for the agent's window, or None.

    `exact` is False when the owning process has several windows.
    """
    by_pid = {}
    for w in windows:
        by_pid.setdefault(w.get("pid"), []).append(w)
    pid = agent_pid
    for _ in range(max_depth):
        if pid in by_pid:
            owned = by_pid[pid]
            return owned[0], len(owned) == 1
        info = parent(pid)
        if not info or info[1] <= 1:
            return None
        pid = info[1]
    return None


def dispatch(lua, classic, run=subprocess.run):
    """Run a dispatcher, in Hyprland's Lua syntax (0.55+) or the classic one."""
    for expr in (lua, classic):
        result = hyprctl("dispatch", expr, run=run)
        if result.returncode == 0 and result.stdout.strip() == "ok":
            return
    raise WindowError(f"hyprctl dispatch failed: {result.stdout.strip() or result.stderr.strip()}")


def active_workspace(run=subprocess.run):
    result = hyprctl("activeworkspace", "-j", run=run)
    try:
        return json.loads(result.stdout)["id"]
    except (ValueError, KeyError) as e:
        raise WindowError("could not read the active workspace") from e


def focus(window, run=subprocess.run):
    """Focus a window, first bringing it to the current workspace if it is
    parked on a special one (a scratchpad, or minimized)."""
    address = window["address"]
    if str(window.get("workspace", {}).get("name", "")).startswith("special:"):
        ws = active_workspace(run=run)
        dispatch(
            f'hl.dsp.window.move({{ window = "address:{address}", workspace = "{ws}", follow = false }})',
            f"movetoworkspacesilent {ws},address:{address}",
            run=run,
        )
    dispatch(
        f'hl.dsp.focus({{ window = "address:{address}" }})',
        f"focuswindow address:{address}",
        run=run,
    )
