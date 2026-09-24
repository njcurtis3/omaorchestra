"""What agents may do without asking, and a record of what they asked.

Rules are read (never changed) from each agent's own settings:
  Claude Code  managed settings, ~/.claude/settings(.local).json and each
               known project's .claude/settings(.local).json: permissions
               allow/ask/deny rules, the default mode, and the MCP server
               allow and deny lists. Rules for MCP tools (mcp__<server>__...)
               are grouped per server.
  Codex        approval_policy and sandbox_mode in ~/.codex/config.toml
  opencode     "permission" in opencode.json

The record: the daemon logs each time an agent starts waiting for the user
(with the prompt) and how the wait ended.
"""

import json
import os
import time
import tomllib
from pathlib import Path

from . import paths
from .mcp import inventory

MANAGED_SETTINGS = Path("/etc/claude-code/managed-settings.json")
MCP_LISTS = ("allowedMcpServers", "deniedMcpServers", "disabledMcpServers", "enabledMcpServers",
             "enabledMcpjsonServers", "disabledMcpjsonServers")
RECORD_KEPT = 500


def _json(path):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def mcp_server_of(rule):
    """"mcp__github__create_issue" -> "github" (None for other rules)."""
    tool = rule.split("(", 1)[0]
    if not tool.startswith("mcp__"):
        return None
    return tool[len("mcp__"):].split("__", 1)[0] or None


def claude_sources(projects=()):
    home = inventory.home()
    sources = [("managed", MANAGED_SETTINGS), ("user", home / ".claude" / "settings.json"),
               ("user (local)", home / ".claude" / "settings.local.json")]
    for project in sorted({str(p) for p in projects}):
        sources += [(f"project {project}", Path(project) / ".claude" / "settings.json"),
                    (f"project {project} (local)", Path(project) / ".claude" / "settings.local.json")]
    return sources


def claude(projects=()):
    found = []
    for label, path in claude_sources(projects):
        data = _json(path)
        if data is None:
            continue
        perms = data.get("permissions") or {}
        rules = {kind: [r for r in perms.get(kind) or [] if isinstance(r, str)] for kind in ("allow", "ask", "deny")}
        by_server = {}
        for kind, items in rules.items():
            for rule in items:
                server = mcp_server_of(rule)
                if server:
                    by_server.setdefault(server, {"allow": [], "ask": [], "deny": []})[kind].append(rule)
        found.append({"agent": "claude", "source": label, "path": str(path),
                      "default_mode": perms.get("defaultMode"), "rules": rules, "mcp_rules": by_server,
                      "mcp_lists": {k: data[k] for k in MCP_LISTS if data.get(k)}})
    return found


def codex():
    try:
        with open(inventory.home() / ".codex" / "config.toml", "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return []
    return [{"agent": "codex", "source": "user", "approval_policy": data.get("approval_policy"),
             "sandbox_mode": data.get("sandbox_mode")}]


def opencode():
    data = inventory._read_json(inventory.home() / ".config" / "opencode" / "opencode.json")
    if not data or "permission" not in data:
        return []
    return [{"agent": "opencode", "source": "user", "permission": data["permission"]}]


def everything(projects=()):
    return {"claude": claude(projects), "codex": codex(), "opencode": opencode(), "record": record()}


# ---------------------------------------------------------------- the record

def _record_path():
    return paths.state_dir() / "approvals.jsonl"


def log(entry):
    target = _record_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a") as f:
        f.write(json.dumps({"at": time.time(), **entry}) + "\n")
    lines = target.read_text().splitlines()
    if len(lines) > RECORD_KEPT * 1.2:  # trim now and then, not on every write
        tmp = target.with_suffix(".tmp")
        tmp.write_text("\n".join(lines[-RECORD_KEPT:]) + "\n")
        os.replace(tmp, target)


def record(limit=50):
    """Requests for the user's approval, newest first, each with how it
    ended ({"at", "session", "project", "message", "outcome", "waited"})."""
    try:
        lines = _record_path().read_text().splitlines()
    except OSError:
        return []
    open_requests, done = {}, []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("kind") == "asked":
            open_requests[e["session"]] = {**e, "outcome": "waiting", "waited": None}
        elif e.get("kind") == "answered" and e.get("session") in open_requests:
            asked = open_requests.pop(e["session"])
            done.append({**asked, "outcome": e.get("outcome"), "waited": round(e["at"] - asked["at"])})
    return sorted(done + list(open_requests.values()), key=lambda e: -e["at"])[:limit]
