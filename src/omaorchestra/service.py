"""Run omaorchestrad as a systemd user service.

A packaged install ships its own unit in /usr/lib/systemd/user; a source
checkout gets a unit written to ~/.config/systemd/user that points at it.
Only a unit carrying MARKER is ever overwritten or deleted.
"""

import os
import re
import subprocess
from pathlib import Path

UNIT_NAME = "omaorchestrad.service"
PACKAGED_BINARY = "/usr/bin/omaorchestra"
PACKAGED_UNIT = Path("/usr/lib/systemd/user") / UNIT_NAME
MARKER = "# Written by `omaorchestra service install`; `omaorchestra service uninstall` removes it."

# Exit codes restarting cannot fix: a bad config file, and another daemon
# already owning the socket. The unit tells systemd not to restart on them,
# so neither can throw the service into a restart loop.
CONFIG_ERROR_EXIT = 2
ALREADY_RUNNING_EXIT = 3


class ServiceError(Exception):
    pass


def user_unit_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "systemd" / "user"


def systemd_quote(word):
    if re.search(r'[\s"\'\\]', word):
        return '"' + word.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return word


def render_unit(binary, marker=True):
    header = MARKER + "\n" if marker else ""
    return f"""{header}[Unit]
Description=omaorchestra agent coordinator daemon
Documentation=https://github.com/njcurtis3/omaorchestra

[Service]
ExecStart={systemd_quote(str(binary))} daemon
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=2
RestartPreventExitStatus={CONFIG_ERROR_EXIT} {ALREADY_RUNNING_EXIT}
NoNewPrivileges=yes

[Install]
WantedBy=default.target
"""


def uses_packaged_unit(binary, packaged_unit=PACKAGED_UNIT):
    return os.path.realpath(binary) == PACKAGED_BINARY and Path(packaged_unit).exists()


def written_by_us(path):
    try:
        return MARKER in Path(path).read_text()
    except OSError:
        return False


def systemctl(*args, run=subprocess.run, check=True):
    result = run(["systemctl", "--user", *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise ServiceError(f"systemctl --user {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def install(binary, unit_dir=None, packaged_unit=PACKAGED_UNIT, run=subprocess.run):
    """Enable and start the service; returns a list of what was done."""
    done = []
    if uses_packaged_unit(binary, packaged_unit):
        systemctl("enable", "--now", UNIT_NAME, run=run)
        return [f"enabled packaged {packaged_unit}"]

    unit = Path(unit_dir or user_unit_dir()) / UNIT_NAME
    text = render_unit(binary)
    existed = unit.exists()
    changed = not existed or unit.read_text() != text
    # Only an update can leave an old version running; a fresh install is
    # started by `enable --now` below.
    was_active = existed and systemctl("is-active", "--quiet", UNIT_NAME, run=run, check=False).returncode == 0
    if changed:
        if unit.exists() and not written_by_us(unit):
            raise ServiceError(f"{unit} exists and was not written by omaorchestra; not overwriting it")
        unit.parent.mkdir(parents=True, exist_ok=True)
        tmp = unit.with_name(f".{unit.name}.tmp")
        tmp.write_text(text)
        os.replace(tmp, unit)
        done.append(f"wrote {unit}")
        systemctl("daemon-reload", run=run)
    systemctl("enable", "--now", UNIT_NAME, run=run)
    done.append(f"enabled {UNIT_NAME}")
    if changed and was_active:
        # enable --now leaves an already-running old version alone
        systemctl("restart", UNIT_NAME, run=run)
        done.append("restarted it to pick up the new unit")
    return done


def uninstall(unit_dir=None, run=subprocess.run):
    done = []
    if systemctl("disable", "--now", UNIT_NAME, run=run, check=False).returncode == 0:
        done.append(f"stopped and disabled {UNIT_NAME}")
    unit = Path(unit_dir or user_unit_dir()) / UNIT_NAME
    if written_by_us(unit):
        unit.unlink()
        systemctl("daemon-reload", run=run)
        done.append(f"removed {unit}")
    elif unit.exists():
        done.append(f"left {unit} alone: not written by omaorchestra")
    return done


def status(run=subprocess.run):
    enabled = systemctl("is-enabled", UNIT_NAME, run=run, check=False).stdout.strip() or "not installed"
    active = systemctl("is-active", UNIT_NAME, run=run, check=False).stdout.strip() or "unknown"
    return enabled, active
