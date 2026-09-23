"""Acting on an agent's process."""

import os
import signal
import time

from . import procs


class ControlError(Exception):
    pass


def stop(session, sig=signal.SIGTERM, is_alive=procs.is_alive, kill=os.kill):
    """Ask the session's agent process to exit.

    The PID is checked against the recorded start time first, so a PID that
    has since been reused by another process is never signalled.
    """
    pid, started = session.get("pid"), session.get("pid_start")
    if pid is None or started is None:
        raise ControlError(f"session {session['id'][:8]} has no recorded process")
    if not is_alive(pid, started):
        raise ControlError(f"the agent for session {session['id'][:8]} has already exited")
    try:
        kill(pid, sig)
    except ProcessLookupError as e:
        raise ControlError(f"the agent for session {session['id'][:8]} has already exited") from e
    except PermissionError as e:
        raise ControlError(f"not allowed to stop process {pid}") from e


def wait_until_gone(session, timeout=3.0, is_alive=procs.is_alive):
    """True once the session's process has exited (checked for `timeout` s)."""
    deadline = time.monotonic() + timeout
    while is_alive(session["pid"], session["pid_start"]):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.1)
    return True
