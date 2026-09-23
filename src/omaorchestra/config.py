"""~/.config/omaorchestra/config.toml: load, validate, fill in defaults.

A missing file means all defaults. Any problem in the file (bad TOML, an
unknown key, a wrong type) is a ConfigError naming the key, never a silent
fallback: a typo should not quietly do nothing.
"""

import copy
import os
import re
import shutil
import tomllib
from pathlib import Path

KNOWN_AGENTS = ("claude",)


class ConfigError(Exception):
    pass


def _int_between(low, high):
    def check(value):
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            return f"must be a whole number from {low} to {high}"
    return check


def _bool(value):
    if not isinstance(value, bool):
        return "must be true or false"


def _agents(value):
    if not isinstance(value, list) or not all(isinstance(a, str) for a in value):
        return "must be a list of agent names"
    unknown = [a for a in value if a not in KNOWN_AGENTS]
    if unknown:
        return f"unknown agent {', '.join(unknown)} (known: {', '.join(KNOWN_AGENTS)})"


_RANGES = {("daemon", "prune_interval"): (5, 3600), ("notifications", "finished_after"): (0, 86400),
           ("tasks", "max_parallel"): (1, 64), ("tasks", "pause_at_usage"): (0, 100)}

# section -> key -> (default, validator)
SCHEMA = {
    "daemon": {
        "prune_interval": (30, _int_between(*_RANGES[("daemon", "prune_interval")])),
        "verbose": (False, _bool),
    },
    "agents": {
        "enabled": (["claude"], _agents),
    },
    "notifications": {
        "waiting": (True, _bool),
        "finished_after": (120, _int_between(*_RANGES[("notifications", "finished_after")])),
    },
    "tasks": {
        "max_parallel": (2, _int_between(*_RANGES[("tasks", "max_parallel")])),
        "isolate_with_worktrees": (True, _bool),
        "pause_at_usage": (90, _int_between(*_RANGES[("tasks", "pause_at_usage")])),
    },
}


# What each setting means, for the app's settings screen and `config set`.
METADATA = {
    "daemon": {
        "title": "Daemon",
        "help": "The background service that tracks sessions.",
        "keys": {
            "prune_interval": ("Check for ended agents every (seconds)",
                               "How often sessions whose agent process has gone are removed."),
            "verbose": ("Verbose log", "Log every request to the journal, not just session changes."),
        },
    },
    "agents": {
        "title": "Agents",
        "help": "Which agents' sessions are tracked.",
        "keys": {"enabled": ("Tracked agents", "Hook events from other agents are ignored.")},
    },
    "notifications": {
        "title": "Notifications",
        "help": "Desktop notifications. Omarchy's do-not-disturb silences them too.",
        "keys": {
            "waiting": ("When an agent waits for you", "Closed again once it stops waiting."),
            "finished_after": ("When an agent finishes after working (seconds)",
                               "Only for work at least this long; 0 turns it off."),
        },
    },
    "tasks": {
        "title": "Tasks",
        "help": "Agents started from omaorchestra (`omaorchestra run`, or New task in the app).",
        "keys": {
            "max_parallel": ("Agents at once", "Queued tasks start while fewer agents than this are busy "
                                               "(working or waiting for you)."),
            "isolate_with_worktrees": ("Separate worktree per task",
                                       "In a git repository, give each task its own worktree and branch."),
            "pause_at_usage": ("Hold the queue at usage (%)",
                               "Start no queued task while any of the agent's subscription limits is at or "
                               "above this (from Omarchy's usage records); 0 turns it off."),
        },
    },
}


def describe(config):
    """Every setting with its value, default, type and help, for UIs."""
    sections = []
    for section, keys in SCHEMA.items():
        meta = METADATA[section]
        fields = []
        for key, (default, _check) in keys.items():
            label, help_text = meta["keys"][key]
            if isinstance(default, bool):
                kind = "bool"
            elif isinstance(default, int):
                kind = "int"
            else:
                kind = "agents"
            field = {"section": section, "key": key, "label": label, "help": help_text, "kind": kind,
                     "value": config[section][key], "default": default}
            if kind == "int":
                field["min"], field["max"] = _RANGES[(section, key)]
            if kind == "agents":
                field["options"] = list(KNOWN_AGENTS)
            fields.append(field)
        sections.append({"section": section, "title": meta["title"], "help": meta["help"], "fields": fields})
    return sections


def parse_value(section, key, text):
    """A command-line string as the setting's type (for `config set`)."""
    if section not in SCHEMA or key not in SCHEMA[section]:
        raise ConfigError(f"unknown setting {section}.{key}")
    default = SCHEMA[section][key][0]
    if isinstance(default, bool):
        if text.lower() in ("true", "yes", "on", "1"):
            return True
        if text.lower() in ("false", "no", "off", "0"):
            return False
        raise ConfigError(f"{section}.{key} must be true or false")
    if isinstance(default, int):
        try:
            return int(text)
        except ValueError:
            raise ConfigError(f"{section}.{key} must be a whole number") from None
    return [part.strip() for part in text.split(",") if part.strip()]


def toml_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(f'"{v}"' for v in value) + "]"
    return str(value)


_SECTION = re.compile(r"^\s*\[([A-Za-z0-9_.-]+)\]\s*(#.*)?$")
_KEY = re.compile(r"^(\s*)([A-Za-z0-9_-]+)(\s*=\s*)(.*)$")


def edit_text(text, changes):
    """Set `changes` ({section: {key: value}}) in TOML text, keeping comments,
    order and everything else as it was. Keys are replaced where they are,
    or added at the end of their section (or a new section at the end)."""
    lines = text.splitlines()
    pending = {s: dict(v) for s, v in changes.items() if v}
    section = None
    section_end = {}  # section -> index after its last non-blank line
    for i, line in enumerate(lines):
        m = _SECTION.match(line)
        if m:
            section = m.group(1)
            section_end.setdefault(section, i + 1)
            continue
        if section is None:
            continue
        if line.strip():
            section_end[section] = i + 1
        k = _KEY.match(line)
        if k and k.group(2) in pending.get(section, {}):
            value_text = k.group(4)
            if value_text.count("[") != value_text.count("]"):
                raise ConfigError(f"{section}.{k.group(2)} spans several lines; edit it by hand")
            # Keep a trailing comment. Values here are booleans, numbers or
            # lists of plain strings, so a comment starts after the last "]"
            # of a list, or anywhere after a scalar.
            rest = value_text[value_text.rfind("]") + 1:] if value_text.lstrip().startswith("[") else value_text
            cm = re.search(r"\s+#.*$", rest) or (re.search(r"^\s*#.*$", rest) if rest is not value_text else None)
            comment = cm.group(0) if cm else ""
            value = pending[section].pop(k.group(2))
            lines[i] = f"{k.group(1)}{k.group(2)}{k.group(3)}{toml_value(value)}{comment}"
    # Insert the rest, last section first so earlier indexes stay valid.
    for sec in sorted((s for s in pending if pending[s] and s in section_end), key=lambda s: -section_end[s]):
        new = [f"{key} = {toml_value(v)}" for key, v in pending[sec].items()]
        lines[section_end[sec]:section_end[sec]] = new
        pending[sec] = {}
    for sec, values in pending.items():
        if values:
            if lines and lines[-1].strip():
                lines.append("")
            lines.append(f"[{sec}]")
            lines += [f"{key} = {toml_value(v)}" for key, v in values.items()]
    return "\n".join(lines) + "\n"


def save(changes, config_path=None):
    """Write changed settings to the config file, keeping its comments.

    The result is validated before anything is written; the old file is kept
    as config.toml.bak. Returns the new effective config.
    """
    config_path = Path(config_path) if config_path else path()
    try:
        text = config_path.read_text()
    except FileNotFoundError:
        text = "# omaorchestra settings. See `omaorchestra config show` for every setting.\n"
    for section, values in changes.items():
        for key, value in values.items():
            if section not in SCHEMA or key not in SCHEMA[section]:
                raise ConfigError(f"unknown setting {section}.{key}")
    new_text = edit_text(text, changes)
    try:
        result = validate(tomllib.loads(new_text))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"the edited file would not be valid TOML ({e}); nothing was written") from e
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.exists():
        shutil.copy2(config_path, config_path.with_name(config_path.name + ".bak"))
    tmp = config_path.with_name(f".{config_path.name}.tmp")
    tmp.write_text(new_text)
    os.replace(tmp, config_path)
    return result


def defaults():
    return {section: {key: copy.deepcopy(spec[0]) for key, spec in keys.items()} for section, keys in SCHEMA.items()}


def path():
    if os.environ.get("OMAORCHESTRA_CONFIG"):
        return Path(os.environ["OMAORCHESTRA_CONFIG"])
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "omaorchestra" / "config.toml"


def validate(data):
    """Merge parsed TOML over the defaults, or raise ConfigError."""
    config = defaults()
    for section, values in data.items():
        if section not in SCHEMA:
            raise ConfigError(f"unknown section [{section}] (known: {', '.join(SCHEMA)})")
        if not isinstance(values, dict):
            raise ConfigError(f"[{section}] must be a table")
        for key, value in values.items():
            if key not in SCHEMA[section]:
                raise ConfigError(f"unknown key {section}.{key} (known: {', '.join(SCHEMA[section])})")
            problem = SCHEMA[section][key][1](value)
            if problem:
                raise ConfigError(f"{section}.{key} {problem}")
            config[section][key] = value
    return config


def load(config_path=None):
    config_path = Path(config_path) if config_path else path()
    try:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        return defaults()
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{config_path}: {e}") from e
    except OSError as e:
        raise ConfigError(f"{config_path}: {e.strerror}") from e
    try:
        return validate(data)
    except ConfigError as e:
        raise ConfigError(f"{config_path}: {e}") from e


def load_or_defaults(config_path=None):
    """For callers that must never fail (agent hooks)."""
    try:
        return load(config_path)
    except ConfigError:
        return defaults()


def to_toml(config):
    lines = []
    for section, values in config.items():
        lines.append(f"[{section}]")
        for key, value in values.items():
            if isinstance(value, bool):
                text = "true" if value else "false"
            elif isinstance(value, list):
                text = "[" + ", ".join(f'"{v}"' for v in value) + "]"
            else:
                text = str(value)
            lines.append(f"{key} = {text}")
        lines.append("")
    return "\n".join(lines)
