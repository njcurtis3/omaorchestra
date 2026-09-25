"""Install and remove omaorchestra's hooks in a Claude Code settings.json.

Only hooks whose command runs `omaorchestra ... hook claude` are ours; every
other hook, and every other setting, is left exactly as it was.
"""

import copy
import json
import os
import shlex
import shutil
import time
from pathlib import Path


HOOK_TIMEOUT = 5


class SettingsError(Exception):
    pass


def default_path():
    base = os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude"
    return Path(base) / "settings.json"


def hook_command(binary, agent="claude"):
    return f"{shlex.quote(str(binary))} hook {agent}"


def is_ours(hook):
    """True for a hook entry that runs omaorchestra's hook (for any agent), from any path."""
    if not isinstance(hook, dict):
        return False
    try:
        argv = shlex.split(str(hook.get("command", "")))
    except ValueError:
        return False
    # Exactly the form install() writes: <path to omaorchestra> hook <agent>
    return (
        len(argv) == 3
        and os.path.basename(argv[0]) == "omaorchestra"
        and argv[1] == "hook"
        and argv[2] in ("claude", "codex", "opencode")
    )


def remove(settings):
    """Settings with every omaorchestra hook removed, and emptied groups dropped."""
    result = copy.deepcopy(settings)
    hooks = result.get("hooks")
    if not isinstance(hooks, dict):
        return result
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept = []
        for group in groups:
            if isinstance(group, dict) and isinstance(group.get("hooks"), list):
                remaining = [h for h in group["hooks"] if not is_ours(h)]
                if not remaining:
                    continue
                group = {**group, "hooks": remaining}
            kept.append(group)
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if not hooks:
        del result["hooks"]
    return result


def install(settings, command, events=None, timeouts=None):
    """Settings with omaorchestra's hooks for every event, replacing old copies.
    The same shape serves Claude Code's settings.json and Codex's hooks.json.
    `timeouts` gives some events a longer timeout than HOOK_TIMEOUT."""
    if events is None:
        from .adapters import Claude  # here, not at the top: adapters import this module
        events = Claude.events
    result = remove(settings)
    hooks = result.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise SettingsError('"hooks" in settings.json is not an object')
    for event in events:
        hooks.setdefault(event, []).append(
            {"hooks": [{"type": "command", "command": command,
                        "timeout": (timeouts or {}).get(event, HOOK_TIMEOUT)}]}
        )
    return result


def installed_events(settings):
    """{event: command} for the omaorchestra hooks present."""
    found = {}
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return found
    for event, groups in hooks.items():
        for group in groups if isinstance(groups, list) else []:
            for hook in group.get("hooks", []) if isinstance(group, dict) else []:
                if is_ours(hook):
                    found[event] = hook["command"]
    return found


def change(path, transform):
    """Apply `transform` to a hooks settings file; returns the backup path,
    "unchanged", or None when there was no file before."""
    before = load(path)
    after = transform(before)
    if after == before:
        return "unchanged"
    return save(path, after)


def load(path):
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return {}
    try:
        settings = json.loads(text) if text.strip() else {}
    except ValueError as e:
        raise SettingsError(f"{path} is not valid JSON ({e}); not touching it") from e
    if not isinstance(settings, dict):
        raise SettingsError(f"{path} does not hold a JSON object; not touching it")
    return settings


def save(path, settings):
    """Write settings atomically, backing up the old file first.

    Returns the backup path, or None when there was no previous file.
    """
    path = Path(path)
    backup = None
    if path.exists():
        backup = path.with_name(f"{path.name}.omaorchestra-backup-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.omaorchestra-tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n")
    if backup:
        shutil.copymode(backup, tmp)
    os.replace(tmp, path)
    return backup
