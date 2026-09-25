import os
from pathlib import Path


def runtime_dir():
    return Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")


def socket_path():
    return Path(os.environ.get("OMAORCHESTRA_SOCKET") or runtime_dir() / "omaorchestra.sock")


def state_dir():
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(os.environ.get("OMAORCHESTRA_STATE_DIR") or Path(base) / "omaorchestra")


def private_state_dir():
    """The state folder, created or tightened to 0700: it holds prompts, task
    text, and the approvals record, which are nobody else's business even on
    a system where home folders are open."""
    folder = state_dir()
    folder.mkdir(parents=True, exist_ok=True)
    if folder.stat().st_uid == os.getuid() and folder.stat().st_mode & 0o077:
        folder.chmod(0o700)
    return folder
