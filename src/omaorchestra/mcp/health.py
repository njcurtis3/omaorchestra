"""Checking configured MCP servers, and remembering the results."""

import json
import os
import re
import time

from .. import paths
from . import inventory, probe


def _expand(value):
    """${VAR} and ${VAR:-default} as Claude Code expands them in .mcp.json."""
    def one(m):
        return os.environ.get(m.group(1)) or (m.group(3) or "")
    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-([^}]*))?\}", one, value) if isinstance(value, str) else value


def check(entry):
    cfg = inventory.raw(entry) or {}
    if entry["transport"] == "stdio":
        env = {k: _expand(v) for k, v in (cfg.get("env") or {}).items()}
        return probe.stdio(_expand(cfg.get("command") or entry["command"] or ""),
                           [_expand(a) for a in cfg.get("args") or entry["args"]], env)
    headers = {k: _expand(v) for k, v in (cfg.get("headers") or {}).items()}
    helper = cfg.get("headersHelper")
    if helper:
        import shlex
        import subprocess
        try:
            out = subprocess.run(shlex.split(helper), capture_output=True, text=True, timeout=10).stdout
            headers.update(json.loads(out))
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            return probe._result(False, time.monotonic(), error=f"its headersHelper failed: {e}")
    return probe.http(_expand(cfg.get("url") or entry["url"]), headers)


def _path():
    return paths.state_dir() / "mcp-health.json"


def results():
    try:
        data = json.loads(_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def check_all(entries):
    saved = results()
    for entry in entries:
        saved[inventory.key(entry)] = {**check(entry), "checked": time.time()}
    target = _path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(saved))
    os.replace(tmp, target)
    return saved
