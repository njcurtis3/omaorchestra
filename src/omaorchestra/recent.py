"""Folders recently launched in, most recent first (for the new-task form)."""

import json
import os

from . import paths

LIMIT = 10


def _path():
    return paths.state_dir() / "recent-folders.json"


def load():
    try:
        folders = json.loads(_path().read_text())
    except (OSError, ValueError):
        return []
    return [f for f in folders if isinstance(f, str)] if isinstance(folders, list) else []


def add(folder):
    folders = [str(folder)] + [f for f in load() if f != str(folder)]
    target = _path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(folders[:LIMIT]))
    os.replace(tmp, target)
