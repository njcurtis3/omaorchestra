"""~/.config/omaorchestra/config.toml: load, validate, fill in defaults.

A missing file means all defaults. Any problem in the file (bad TOML, an
unknown key, a wrong type) is a ConfigError naming the key, never a silent
fallback: a typo should not quietly do nothing.
"""

import copy
import os
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


# section -> key -> (default, validator)
SCHEMA = {
    "daemon": {
        "prune_interval": (30, _int_between(5, 3600)),
        "verbose": (False, _bool),
    },
    "agents": {
        "enabled": (["claude"], _agents),
    },
    "tasks": {
        "max_parallel": (2, _int_between(1, 64)),
        "isolate_with_worktrees": (True, _bool),
    },
}


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
