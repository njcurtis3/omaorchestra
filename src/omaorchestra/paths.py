import os
from pathlib import Path


def runtime_dir():
    return Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")


def socket_path():
    return Path(os.environ.get("OMAORCHESTRA_SOCKET") or runtime_dir() / "omaorchestra.sock")


def state_dir():
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(os.environ.get("OMAORCHESTRA_STATE_DIR") or Path(base) / "omaorchestra")
