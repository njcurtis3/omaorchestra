"""Every MCP server the agents on this machine are configured with, read-only.

Sources:
  Claude Code  ~/.claude.json (user scope, and local scope per project) and
               each known project's .mcp.json (project scope)
  Codex        ~/.codex/config.toml, [mcp_servers.<name>]
  opencode     ~/.config/opencode/opencode.json(c), "mcp"

Each server is normalised to {agent, scope, project, name, transport,
command, args, url, env_keys, header_keys, enabled, managed}. Only the names
of environment variables and headers are kept, never their values, which
often hold tokens.
"""

import json
import os
import re
import tomllib
from pathlib import Path

MANAGED_COMMAND = "omaorchestra"  # servers omaorchestra wrote run `omaorchestra mcp exec <name>`


def home():
    return Path(os.environ.get("OMAORCHESTRA_AGENT_HOME") or Path.home())


def _entry(agent, scope, name, cfg, project=None, enabled=True):
    cfg = cfg if isinstance(cfg, dict) else {}
    kind = cfg.get("type")
    command = cfg.get("command")
    args = cfg.get("args") or []
    if agent == "opencode":  # "local" servers give the command as a list
        kind = {"local": "stdio", "remote": "http"}.get(kind, kind)
        if isinstance(command, list):
            command, args = (command[0] if command else None), command[1:]
    transport = kind or ("http" if cfg.get("url") else "stdio")
    env = cfg.get("env") or cfg.get("environment") or {}
    headers = cfg.get("headers") or {}
    managed = (os.path.basename(str(command or "")) == MANAGED_COMMAND and list(args[:2]) == ["mcp", "exec"]) or \
        str(cfg.get("headersHelper") or "").startswith(f"{MANAGED_COMMAND} mcp headers")
    return {
        "agent": agent, "scope": scope, "project": str(project) if project else None, "name": name,
        "transport": transport, "command": command, "args": list(args) if isinstance(args, list) else [],
        "url": cfg.get("url"), "env_keys": sorted(env) if isinstance(env, dict) else [],
        "header_keys": sorted(headers) if isinstance(headers, dict) else [],
        "headers_helper": bool(cfg.get("headersHelper")), "enabled": bool(cfg.get("enabled", enabled)),
        "managed": managed,
    }


def _read_json(path):
    try:
        text = Path(path).read_text()
    except OSError:
        return None
    if str(path).endswith(".jsonc"):
        text = re.sub(r"^\s*//.*$", "", text, flags=re.M)
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def claude(projects=()):
    """Claude Code's servers; `projects` adds folders whose .mcp.json to read
    (besides the projects Claude itself knows)."""
    found = []
    data = _read_json(home() / ".claude.json") or {}
    for name, cfg in (data.get("mcpServers") or {}).items():
        found.append(_entry("claude", "user", name, cfg))
    known = data.get("projects") or {}
    for project, pd in known.items():
        if isinstance(pd, dict):
            for name, cfg in (pd.get("mcpServers") or {}).items():
                found.append(_entry("claude", "local", name, cfg, project))
    for project in sorted(set(known) | {str(p) for p in projects}):
        mcp_json = _read_json(Path(project) / ".mcp.json")
        if not mcp_json:
            continue
        disabled = set((known.get(project) or {}).get("disabledMcpjsonServers") or [])
        for name, cfg in (mcp_json.get("mcpServers") or {}).items():
            found.append(_entry("claude", "project", name, cfg, project, enabled=name not in disabled))
    return found


def codex():
    try:
        with open(home() / ".codex" / "config.toml", "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return []
    return [_entry("codex", "user", name, cfg) for name, cfg in (data.get("mcp_servers") or {}).items()]


def opencode():
    for name in ("opencode.json", "opencode.jsonc"):
        data = _read_json(home() / ".config" / "opencode" / name)
        if data is not None:
            return [_entry("opencode", "user", n, cfg) for n, cfg in (data.get("mcp") or {}).items()]
    return []


def raw(entry):
    """A server's own config entry, values included: only for starting it
    (health checks), never for showing."""
    if entry["agent"] == "claude":
        if entry["scope"] == "project":
            source = (_read_json(Path(entry["project"]) / ".mcp.json") or {}).get("mcpServers") or {}
        else:
            data = _read_json(home() / ".claude.json") or {}
            source = data.get("mcpServers") or {} if entry["scope"] == "user" else \
                ((data.get("projects") or {}).get(entry["project"]) or {}).get("mcpServers") or {}
        return source.get(entry["name"])
    if entry["agent"] == "codex":
        try:
            with open(home() / ".codex" / "config.toml", "rb") as f:
                return (tomllib.load(f).get("mcp_servers") or {}).get(entry["name"])
        except (OSError, tomllib.TOMLDecodeError):
            return None
    for name in ("opencode.json", "opencode.jsonc"):
        data = _read_json(home() / ".config" / "opencode" / name)
        if data is not None:
            cfg = (data.get("mcp") or {}).get(entry["name"])
            if cfg and cfg.get("type") == "local" and isinstance(cfg.get("command"), list):
                cfg = {**cfg, "command": cfg["command"][0], "args": cfg["command"][1:], "env": cfg.get("environment")}
            return cfg
    return None


def key(entry):
    return "/".join([entry["agent"], entry["scope"], entry["project"] or "", entry["name"]])


def everything(projects=()):
    return claude(projects) + codex() + opencode()
