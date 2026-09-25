"""Process identity for liveness checks, read from /proc."""

import os

PROC = "/proc"


def _stat_fields(pid, proc=PROC):
    """(comm, fields after comm) from /proc/<pid>/stat, or None if gone."""
    try:
        with open(f"{proc}/{pid}/stat") as f:
            stat = f.read()
    except (OSError, ValueError):
        return None
    # comm is in parentheses and may itself contain spaces or ")".
    head, _, tail = stat.rpartition(")")
    return head.partition("(")[2], tail.split()


def start_time(pid, proc=PROC):
    """Process start time in clock ticks since boot, or None if it is gone.

    A PID alone is not an identity: PIDs are reused. PID plus start time is.
    """
    fields = _stat_fields(pid, proc)
    if not fields or len(fields[1]) < 20:
        return None
    return int(fields[1][19])  # field 22 of stat; the tail starts at field 3


def is_alive(pid, pid_start, proc=PROC):
    return start_time(pid, proc) == pid_start


def parent(pid, proc=PROC):
    fields = _stat_fields(pid, proc)
    if not fields or len(fields[1]) < 2:
        return None
    return fields[0], int(fields[1][1])


def _argv(pid, proc=PROC):
    try:
        with open(f"{proc}/{pid}/cmdline", "rb") as f:
            return f.read().split(b"\0")
    except OSError:
        return []


def find_session_process(session_id, proc=PROC):
    """(pid, start_time) of the process started with `--session-id <id>`, or None.

    Its terminal's command line usually contains the same arguments (as
    `-e claude --session-id ...`), so of the matches, take the deepest: the
    one none of the other matches is a child of.
    """
    wanted = [b"--session-id", session_id.encode()]
    matches = set()
    try:
        pids = [int(p) for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return None
    for pid in pids:
        argv = _argv(pid, proc)
        if any(argv[i:i + 2] == wanted for i in range(len(argv) - 1)):
            matches.add(pid)
    parents = {info[1] for pid in matches if (info := parent(pid, proc))}
    leaves = sorted(pid for pid in matches if pid not in parents)
    for pid in reversed(leaves):
        started = start_time(pid, proc)
        if started is not None:
            return pid, started
    return None


def _environ(pid, proc=PROC):
    try:
        with open(f"{proc}/{pid}/environ", "rb") as f:
            return f.read().split(b"\0")
    except OSError:
        return []


def find_env_process(variable, value, names=(), proc=PROC):
    """(pid, start_time) of the agent launched with `variable=value` in its
    environment, or None.

    Its terminal has the variable too (it launched the agent), and so do the
    agent's own children. Prefer a process named like the agent; otherwise
    take a child of the topmost match (the terminal).
    """
    wanted = f"{variable}={value}".encode()
    try:
        pids = [int(p) for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return None
    matches = {pid for pid in pids if wanted in _environ(pid, proc)}
    if not matches:
        return None
    parents = {pid: (parent(pid, proc) or ("", 0)) for pid in matches}
    named = [pid for pid in matches if parents[pid][0] in names]
    if named:
        top = [pid for pid in named if parents[pid][1] not in named]
        pid = min(top or named)
    else:
        roots = [pid for pid in matches if parents[pid][1] not in matches]
        children = sorted(pid for pid in matches if parents[pid][1] in roots)
        pid = children[0] if children else min(roots)
    started = start_time(pid, proc)
    return (pid, started) if started is not None else None


def agent_process(env=None, start=None, names=("claude",), proc=PROC):
    """(pid, start_time) of the agent running this hook, or None.

    Claude Code exports CLAUDE_PID to its children; failing that, walk up the
    process tree from `start` looking for a process named like the agent.
    """
    env = os.environ if env is None else env
    claimed = env.get("CLAUDE_PID", "")
    if claimed.isdigit():
        started = start_time(int(claimed), proc)
        if started is not None:
            return int(claimed), started

    pid = os.getppid() if start is None else start
    for _ in range(32):
        if pid <= 1:
            break
        info = parent(pid, proc)
        if not info:
            break
        comm, ppid = info
        if comm in names:
            return pid, start_time(pid, proc)
        pid = ppid
    return None


# Set by Claude Code in the environment of everything it runs.
AGENT_MARKERS = (b"CLAUDECODE=1",)


def under_agent(pid, names, proc=PROC):
    """The name of the agent `pid` runs under (itself or an ancestor named in
    `names`, or with an agent's marker in its environment), or None."""
    if any(marker in _environ(pid, proc) for marker in AGENT_MARKERS):
        return "claude"
    for _ in range(64):
        if pid <= 1:
            return None
        info = parent(pid, proc)
        if not info:
            return None
        comm, ppid = info
        if comm in names:
            return comm.lstrip(".")
        pid = ppid
    return None
