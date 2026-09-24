"""Which model a task uses when none is given.

Order: the folder's own default (set per project, matched by the folder or
any folder above it), then `tasks.default_model`, then none at all (the
agent's own default). Per-folder defaults live in the state directory.
"""

import json
import os
from pathlib import Path

from . import config, paths


def _path():
    return paths.state_dir() / "model-defaults.json"


def load():
    try:
        data = json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}


def _save(data):
    target = _path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, target)


def set_for(folder, model):
    """Set (or, with an empty model, clear) the default for a folder."""
    data = load()
    key = str(Path(folder).expanduser().resolve())
    if model:
        data[key] = model
    else:
        data.pop(key, None)
    _save(data)


def for_folder(folder):
    """(model, where it came from) for a folder: "folder", "global" or None."""
    data = load()
    here = Path(folder).expanduser().resolve()
    for candidate in (here, *here.parents):
        if str(candidate) in data:
            return data[str(candidate)], "folder"
    fallback = config.load_or_defaults()["tasks"]["default_model"]
    return (fallback, "global") if fallback else (None, None)
