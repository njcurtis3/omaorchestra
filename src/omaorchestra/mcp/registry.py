"""MCP servers omaorchestra manages, and writing them into agents' configs.

The list lives in ~/.config/omaorchestra/mcp.json: each server's transport,
command or URL, plain environment variables and headers, the names of its
secret ones (their values are in the system keyring), the agents it is
installed in, and whether it is enabled.

Secrets never go into an agent's config:
  - a stdio server is installed as `omaorchestra mcp exec <name>`, which
    adds its secrets from the keyring and then starts the real server;
  - an HTTP server's secret headers reach Claude Code through a
    headersHelper (`omaorchestra mcp headers <name>`). Codex and opencode
    have no equivalent, so they only get HTTP servers without secrets.

Each agent's config file is backed up before every change.
"""

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from .. import keys, paths
from . import inventory

AGENTS = ("claude", "codex", "opencode")
CLAUDE_SCOPES = ("user", "local")  # project scope writes .mcp.json, a file teams share: not ours to write
BACKUPS_KEPT = 10


class McpError(Exception):
    pass


def path():
    if os.environ.get("OMAORCHESTRA_MCP"):
        return Path(os.environ["OMAORCHESTRA_MCP"])
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "omaorchestra" / "mcp.json"


def _read():
    try:
        data = json.loads(path().read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise McpError(f"{path()} is not valid JSON ({e})") from e
    return data if isinstance(data, dict) else {}


def _write(data):
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, target)


def load():
    servers = _read().get("servers")
    return servers if isinstance(servers, dict) else {}


def _save(servers):
    _write({**_read(), "servers": servers})


# ---------------------------------------------------------------- profiles
# A profile is a named set of managed servers a task can be started with,
# instead of whatever servers the agent itself is configured with. "none"
# (no servers at all) is built in.

NONE_PROFILE = "none"


def profiles():
    found = _read().get("profiles")
    return found if isinstance(found, dict) else {}


def set_profile(name, server_names):
    if not NAME.fullmatch(name) or name == NONE_PROFILE:
        raise McpError(f"a profile name is letters, digits, - and _ (and not {NONE_PROFILE})")
    known = load()
    unknown = [s for s in server_names if s not in known]
    if unknown:
        raise McpError(f"not managed by omaorchestra: {', '.join(unknown)} (add them with `omaorchestra mcp add`)")
    data = _read()
    data.setdefault("profiles", {})[name] = list(dict.fromkeys(server_names))
    _write(data)


def remove_profile(name):
    data = _read()
    if name not in (data.get("profiles") or {}):
        raise McpError(f"no profile {name}")
    del data["profiles"][name]
    _write(data)


def profile_config(name):
    """{"mcpServers": {...}} for Claude Code's --mcp-config: the profile's
    servers as Claude entries (no secrets, as everywhere)."""
    if name == NONE_PROFILE:
        return {"mcpServers": {}}
    members = profiles().get(name)
    if members is None:
        raise McpError(f"no profile {name} (see `omaorchestra mcp profile list`)")
    servers = load()
    return {"mcpServers": {s: agent_entry("claude", s, servers[s]) for s in members if s in servers}}


def write_profile_config(name, session_id):
    """Write the profile's config where only this user can read it, and
    return its path. Files older than a day are cleared on the way."""
    folder = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}") / "omaorchestra" / "mcp"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    for old in folder.glob("*.json"):
        if time.time() - old.stat().st_mtime > 86400:
            old.unlink(missing_ok=True)
    target = folder / f"{session_id}.json"
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(profile_config(name), f)
    return target


def get(name):
    servers = load()
    if name not in servers:
        raise McpError(f"no managed MCP server {name} (see `omaorchestra mcp managed`)")
    return servers[name]


def secret_id(name, key):
    return f"mcp:{name}:{key}"


def secrets(name, spec):
    """{variable or header name: value} from the keyring."""
    out = {}
    for key in spec.get("secret_env", []) + spec.get("secret_headers", []):
        value = keys.lookup(secret_id(name, key))
        if value is None:
            raise McpError(f"the secret {key} for {name} is not in the keyring")
        out[key] = value
    return out


# ---------------------------------------------------------------- targets

def parse_target(text):
    """"claude", "claude:user", "claude:local:/path", "codex", "opencode"."""
    agent, _, rest = text.partition(":")
    if agent not in AGENTS:
        raise McpError(f"unknown agent {agent} (known: {', '.join(AGENTS)})")
    if agent != "claude":
        if rest and rest != "user":
            raise McpError(f"{agent} only has a user scope")
        return {"agent": agent, "scope": "user", "project": None}
    scope, _, project = rest.partition(":")
    scope = scope or "user"
    if scope not in CLAUDE_SCOPES:
        raise McpError("Claude Code servers go in the user or local scope (project scope is .mcp.json, "
                       "which teams share; edit that by hand)")
    if scope == "local":
        if not project:
            raise McpError("local scope needs a project: claude:local:/path/to/project")
        project = str(Path(project).expanduser().resolve())
    return {"agent": "claude", "scope": scope, "project": project or None}


def target_label(t):
    return t["agent"] + (f" ({t['scope']}" + (f" {t['project']}" if t["project"] else "") + ")" if t["agent"] == "claude" else "")


# ---------------------------------------------------------------- agent entries

def own_command():
    """How agents should call omaorchestra: an absolute path, since agents
    may start without ~/.local/bin on PATH."""
    launcher = os.environ.get("OMAORCHESTRA_BIN") or shutil.which("omaorchestra")
    return os.path.realpath(launcher) if launcher else "omaorchestra"


def agent_entry(agent, name, spec):
    """The config entry to write for `agent`; never contains a secret."""
    if spec["transport"] == "stdio":
        command = [own_command(), "mcp", "exec", name]
        if agent == "opencode":
            return {"type": "local", "command": command, "enabled": True}
        return {"type": "stdio", "command": command[0], "args": command[1:], "env": {}}
    headers = dict(spec.get("headers") or {})
    if spec.get("secret_headers") and agent != "claude":
        raise McpError(f"{agent} cannot fetch {name}'s secret headers at connection time; "
                       "install it for Claude Code, or use a stdio server")
    if agent == "opencode":
        return {"type": "remote", "url": spec["url"], "headers": headers, "enabled": True}
    entry = {"type": spec["transport"], "url": spec["url"]}
    if spec.get("secret_headers"):
        entry["headersHelper"] = f"{own_command()} mcp headers {name}"
    elif headers:
        entry["headers"] = headers
    return entry


# ---------------------------------------------------------------- writing

def backup(agent):
    source = {"claude": inventory.home() / ".claude.json",
              "codex": inventory.home() / ".codex" / "config.toml",
              "opencode": inventory.home() / ".config" / "opencode" / "opencode.json"}[agent]
    if not source.exists():
        return None
    folder = paths.state_dir() / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{agent}-{time.strftime('%Y%m%d-%H%M%S')}-{source.name}"
    shutil.copy2(source, target)
    for old in sorted(folder.glob(f"{agent}-*"))[:-BACKUPS_KEPT]:
        old.unlink()
    return target


def _cli(run, argv, cwd=None):
    try:
        result = run(argv, cwd=cwd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError as e:
        raise McpError(f"{argv[0]} is not installed") from e
    except subprocess.TimeoutExpired as e:
        raise McpError(f"`{' '.join(argv[:3])}` took too long") from e
    return result


def install(name, spec, target, run=subprocess.run):
    """Write the server into one agent (replacing an entry of that name)."""
    agent = target["agent"]
    entry = agent_entry(agent, name, spec)
    backup(agent)
    if agent == "claude":
        cwd = target["project"] if target["scope"] == "local" else None
        _cli(run, ["claude", "mcp", "remove", "-s", target["scope"], name], cwd)
        r = _cli(run, ["claude", "mcp", "add-json", "-s", target["scope"], name, json.dumps(entry)], cwd)
    elif agent == "codex":
        _cli(run, ["codex", "mcp", "remove", name])
        if spec["transport"] == "stdio":
            r = _cli(run, ["codex", "mcp", "add", name, "--", entry["command"], *entry["args"]])
        else:
            if entry.get("headers"):
                raise McpError("Codex's `mcp add` takes no headers; install this server for Claude Code instead")
            r = _cli(run, ["codex", "mcp", "add", name, "--url", entry["url"]])
    else:
        _opencode_edit(lambda servers: servers.__setitem__(name, entry))
        return
    if r.returncode != 0:
        raise McpError(f"{agent} refused: {(r.stderr or r.stdout).strip()}")


def uninstall(name, target, run=subprocess.run):
    agent = target["agent"]
    backup(agent)
    if agent == "claude":
        cwd = target["project"] if target["scope"] == "local" else None
        _cli(run, ["claude", "mcp", "remove", "-s", target["scope"], name], cwd)
    elif agent == "codex":
        _cli(run, ["codex", "mcp", "remove", name])
    else:
        _opencode_edit(lambda servers: servers.pop(name, None))


def _opencode_edit(change):
    config_path = inventory.home() / ".config" / "opencode" / "opencode.json"
    try:
        data = json.loads(config_path.read_text())
    except FileNotFoundError:
        data = {"$schema": "https://opencode.ai/config.json"}
    except ValueError as e:
        raise McpError(f"{config_path} is not valid JSON; not touching it") from e
    servers = data.setdefault("mcp", {})
    change(servers)
    if not servers:
        del data["mcp"]
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = config_path.with_name(f".{config_path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, config_path)


# ---------------------------------------------------------------- lifecycle

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def add(name, spec, targets, secret_values, run=subprocess.run):
    """Register a server, store its secrets, and install it in `targets`."""
    if not NAME.fullmatch(name):
        raise McpError("a server name is letters, digits, - and _")
    servers = load()
    if name in servers:
        raise McpError(f"there is already a managed server {name}")
    if spec["transport"] == "stdio" and not spec.get("command"):
        raise McpError("a stdio server needs a command")
    if spec["transport"] in ("http", "sse") and not str(spec.get("url", "")).startswith(("http://", "https://")):
        raise McpError("an HTTP server needs an http(s) URL")
    missing = [k for k in spec.get("secret_env", []) + spec.get("secret_headers", []) if not secret_values.get(k)]
    if missing:
        raise McpError(f"no value given for {', '.join(missing)}")
    for t in targets:  # fail before storing anything
        agent_entry(t["agent"], name, spec)
    for key, value in secret_values.items():
        keys.store(secret_id(name, key), value)
    spec = {**spec, "targets": targets, "enabled": True}
    servers[name] = spec
    _save(servers)
    for t in targets:
        install(name, spec, t, run)
    return spec


def set_enabled(name, enabled, run=subprocess.run):
    servers = load()
    spec = get(name)
    for t in spec["targets"]:
        (install(name, spec, t, run) if enabled else uninstall(name, t, run))
    spec["enabled"] = enabled
    servers[name] = spec
    _save(servers)


def remove(name, run=subprocess.run):
    servers = load()
    spec = get(name)
    for t in spec["targets"]:
        uninstall(name, t, run)
    for key in spec.get("secret_env", []) + spec.get("secret_headers", []):
        keys.clear(secret_id(name, key))
    del servers[name]
    _save(servers)


# ---------------------------------------------------------------- run-time helpers

def exec_server(name):
    """Replace this process with the real server, its secrets in its
    environment (for `omaorchestra mcp exec`)."""
    spec = get(name)
    if spec["transport"] != "stdio":
        raise McpError(f"{name} is an HTTP server")
    env = {**os.environ, **(spec.get("env") or {}), **secrets(name, spec)}
    command = [spec["command"], *spec.get("args", [])]
    try:
        os.execvpe(command[0], command, env)
    except OSError as e:
        raise McpError(f"cannot start {command[0]}: {e.strerror}") from e


def headers_json(name):
    """All of an HTTP server's headers, secrets included (for headersHelper)."""
    spec = get(name)
    return json.dumps({**(spec.get("headers") or {}), **secrets(name, spec)})
