"""Reaching this machine from a phone: checks for `setup --only remote`, and
the restricted SSH key line for `remote ssh-key`.

Two ways in, both over Tailscale so nothing is open to the internet (see
docs/remote.md):

  Tailscale SSH  tailscaled answers SSH on the tailnet address itself; who
                 may log in is the tailnet's access rules. Nothing to set up
                 here, but whoever gets in has a shell.
  sshd           the system's SSH server, reachable on the tailnet only, with
                 the phone's key limited to `omaorchestra top` (a forced
                 command), so a lost phone cannot open a shell.

Everything here only reads: config files, `tailscale status`, `ss`. It never
changes sshd, Tailscale or the firewall; it says what to run instead. The one
write is `remote ssh-key --add`, which appends a restricted key to
~/.ssh/authorized_keys because you asked it to.
"""

import base64
import glob
import ipaddress
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

SSHD_CONFIG = Path("/etc/ssh/sshd_config")
UFW_CONF = Path("/etc/ufw/ufw.conf")
UFW_RULES = (Path("/etc/ufw/user.rules"), Path("/etc/ufw/user6.rules"))
KEY_TYPES = ("ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521", "ssh-rsa",
             "sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com")
TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")


class RemoteAccessError(Exception):
    pass


def _run(run, args):
    try:
        result = run(args, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


# ---------------------------------------------------------------- Tailscale

def tailscale(run=subprocess.run):
    """{"installed", "running", "name", "ips", "ssh"} from the tailscale CLI
    (no root needed)."""
    state = {"installed": bool(shutil.which("tailscale")), "running": False, "name": "", "ips": [], "ssh": False}
    if not state["installed"]:
        return state
    try:
        status = json.loads(_run(run, ["tailscale", "status", "--json"]) or "{}")
    except ValueError:
        status = {}
    me = status.get("Self") or {}
    state["running"] = status.get("BackendState") == "Running"
    state["name"] = (me.get("DNSName") or "").rstrip(".")
    state["ips"] = list(me.get("TailscaleIPs") or [])
    try:
        prefs = json.loads(_run(run, ["tailscale", "debug", "prefs"]) or "{}")
    except ValueError:
        prefs = {}
    state["ssh"] = bool(prefs.get("RunSSH"))
    return state


def on_tailnet(address):
    try:
        ip = ipaddress.ip_address(address.strip("[]").split("%")[0])
    except ValueError:
        return False
    return ip in (TAILNET_V4 if ip.version == 4 else TAILNET_V6)


# ---------------------------------------------------------------- sshd

def sshd_settings(path=SSHD_CONFIG):
    """The sshd settings that matter here, as sshd reads them: the first
    value of each wins, Include pulls files in where it stands, and Match
    blocks (which apply to some logins only) are left out."""
    found = {}

    def read(file, depth=0):
        try:
            lines = Path(file).read_text().splitlines()
        except OSError:
            return False
        for line in lines:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            key, value = (re.split(r"[\s=]+", line, maxsplit=1) + [""])[:2]
            key, value = key.lower(), value.strip().strip('"')
            if key == "match":
                return True  # the rest of this file is conditional
            if key == "include" and depth < 8:
                for pattern in value.split():
                    pattern = pattern if pattern.startswith("/") else str(Path("/etc/ssh") / pattern)
                    for name in sorted(glob.glob(pattern)):
                        if read(name, depth + 1):
                            return True
            elif key in ("listenaddress", "port"):
                found.setdefault(key, []).append(value)
            else:
                found.setdefault(key, value)
        return False

    readable = os.access(path, os.R_OK)
    read(path)
    ports = [p for p in found.get("port", []) if p.isdigit()] or ["22"]
    return {
        "readable": readable,
        "ports": [int(p) for p in ports],
        "listen": found.get("listenaddress", []),
        "passwords": found.get("passwordauthentication", "yes").lower() != "no",
        "keyboard": found.get("kbdinteractiveauthentication", "yes").lower() != "no",
        "root": found.get("permitrootlogin", "prohibit-password").lower(),
    }


def sshd_state(run=subprocess.run):
    """{"installed", "active", "enabled"} for the sshd service."""
    return {
        "installed": bool(shutil.which("sshd") or Path("/usr/bin/sshd").exists()),
        "active": (_run(run, ["systemctl", "is-active", "sshd"]) or "").strip() == "active",
        "enabled": (_run(run, ["systemctl", "is-enabled", "sshd"]) or "").strip() == "enabled",
    }


def listening(ports, run=subprocess.run):
    """Local addresses a TCP socket listens on for any of `ports`."""
    out = _run(run, ["ss", "-Htln"]) or ""
    addresses = []
    for line in out.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        address, _, port = fields[3].rpartition(":")
        if port.isdigit() and int(port) in ports:
            addresses.append(address)
    return addresses


def everywhere(addresses):
    return any(a in ("0.0.0.0", "[::]", "*", "::") for a in addresses)


# ---------------------------------------------------------------- firewall

def _port_matches(spec, port):
    if spec == "any":
        return True
    for part in spec.split(","):
        low, _, high = part.partition(":")
        if low.isdigit() and (int(low) == port if not high else int(low) <= port <= int(high or low)):
            return True
    return False


def ufw(port=22, conf=UFW_CONF, rules=UFW_RULES):
    """{"known", "enabled", "open": [interfaces ("" = all) a rule lets `port` in on]}."""
    try:
        enabled = re.search(r"^ENABLED=yes", Path(conf).read_text(), re.M) is not None
    except OSError:
        return {"known": False, "enabled": False, "open": []}
    open_on = []
    for path in rules:
        try:
            text = Path(path).read_text()
        except OSError:
            continue
        for line in text.splitlines():
            if not line.startswith("### tuple ###"):
                continue
            fields = line.split()[3:]
            if len(fields) < 7 or fields[0] not in ("allow", "limit") or fields[1] not in ("tcp", "any"):
                continue
            direction = next((f for f in fields if f.startswith("in")), "")
            if not direction or not _port_matches(fields[2], port):
                continue
            interface = direction.partition("_")[2]
            if interface not in open_on:
                open_on.append(interface)
    return {"known": True, "enabled": enabled, "open": open_on}


# ---------------------------------------------------------------- keys

def authorized_keys_path():
    return Path.home() / ".ssh" / "authorized_keys"


def parse_key(text):
    """(type, base64 body, comment) of a public key line; RemoteAccessError if
    it is not one."""
    fields = text.strip().split()
    if len(fields) < 2 or fields[0] not in KEY_TYPES:
        raise RemoteAccessError("that is not an SSH public key (it starts with ssh-ed25519, ecdsa-sha2-... or "
                                "ssh-rsa); copy the public half, not the private key")
    try:
        base64.b64decode(fields[1], validate=True)
    except ValueError:
        raise RemoteAccessError("the key's body is not valid base64") from None
    return fields[0], fields[1], " ".join(fields[2:])


def key_line(binary, public_key, comment=None):
    """An authorized_keys line that can only run `omaorchestra top`:
    `restrict` turns off forwarding, agent and X11, `pty` gives the screen
    back, and `command` runs top whatever the client asks for."""
    kind, body, own_comment = parse_key(public_key)
    comment = comment or own_comment or "omaorchestra-top"
    return f'command="{binary} top",restrict,pty {kind} {body} {comment}'


def keys(path=None):
    """{"top": n keys limited to omaorchestra top, "shell": n keys that can
    open a shell}."""
    path = path or authorized_keys_path()
    counts = {"top": 0, "shell": 0}
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return counts
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        forced = re.match(r'^(?:\S+,)*command="([^"]*)"', line)
        if forced and re.search(r"omaorchestra\S*\s+top\b", forced.group(1)):
            counts["top"] += 1
        elif not forced:
            counts["shell"] += 1
    return counts


def add_key(line, path=None):
    """Append a key line to authorized_keys (creating ~/.ssh with the modes
    sshd insists on); a key already there is not added twice."""
    path = Path(path or authorized_keys_path())
    fields = line.split()
    body = next(fields[i + 1] for i, f in enumerate(fields[:-1]) if f in KEY_TYPES)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    existing = path.read_text() if path.exists() else ""
    if body in existing:
        return False
    if existing:
        backup = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        backup.write_text(existing)
        os.chmod(backup, 0o600)
    with open(path, "a") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write(line + "\n")
    os.chmod(path, 0o600)
    return True


# ---------------------------------------------------------------- the check

def check(run=subprocess.run, ts=None, sshd=None, config=None, firewall=None, key_counts=None, listen=None):
    """What `setup --only remote` prints: (level, text) with level "ok",
    "note" or "fix". Each piece can be passed in (tests)."""
    ts = ts if ts is not None else tailscale(run)
    out = []
    if not ts["installed"]:
        out.append(("fix", "Tailscale is not installed: `omarchy-install-service-tailscale` (or install the "
                           "tailscale package and run `sudo tailscale up`). It is how a phone reaches this machine without opening it to "
                           "the internet."))
    elif not ts["running"]:
        out.append(("fix", "Tailscale is installed but not connected: `sudo tailscale up`."))
    else:
        where = ts["name"] or (ts["ips"][0] if ts["ips"] else "this machine")
        out.append(("ok", f"on the tailnet as {where}" + (f" ({ts['ips'][0]})" if ts["ips"] and ts["name"] else "")))
        if ts["ssh"]:
            out.append(("ok", f"Tailscale SSH is on: from the phone, `ssh {os.environ.get('USER', 'you')}@{where}` "
                              "and run `omaorchestra top`. Who may log in is set by your tailnet's access rules; "
                              "whoever does gets a shell."))
        else:
            out.append(("note", "Tailscale SSH is off. The quick way in: `sudo tailscale set --ssh` (it answers "
                                "SSH on the tailnet address only; no keys to manage). Or use sshd with a "
                                "restricted key, below."))

    sshd = sshd if sshd is not None else sshd_state(run)
    if not sshd["installed"]:
        out.append(("note", "sshd is not installed; not needed with Tailscale SSH."))
        return out
    if not sshd["active"]:
        if ts.get("ssh"):
            # Tailscale SSH is the way in; sshd's settings and keys do not matter.
            out.append(("ok", "sshd is not running, and not needed with Tailscale SSH"))
            return out
        out.append(("note", "sshd is installed but not running: `sudo systemctl enable --now sshd` to use it "
                            "(after the checks below)."))
    config = config if config is not None else sshd_settings()
    addresses = listen if listen is not None else (listening(config["ports"], run) if sshd["active"] else [])
    port = config["ports"][0]
    if sshd["active"]:
        if everywhere(addresses) or any(not on_tailnet(a) and a not in ("127.0.0.1", "[::1]") for a in addresses):
            out.append(("note", f"sshd listens on every network (port {port}), not just the tailnet. To keep it "
                                "to the tailnet, add `ListenAddress " + (ts["ips"][0] if ts["ips"] else "<tailnet IP>")
                        + "` to /etc/ssh/sshd_config.d/50-tailnet.conf and restart sshd (it then needs "
                          "tailscaled up first), or let the firewall below do it."))
        elif addresses:
            out.append(("ok", f"sshd listens on the tailnet only ({', '.join(addresses)})"))
    if config["readable"] and (config["passwords"] or config["keyboard"]):
        out.append(("fix" if sshd["active"] else "note",
                    "sshd accepts passwords. Keys only is safer: add `PasswordAuthentication no` to "
                    "/etc/ssh/sshd_config.d/50-keys-only.conf and restart sshd."))
    elif config["readable"]:
        out.append(("ok", "sshd takes keys only (no passwords)"))
    if config["readable"] and config["root"] == "yes":
        out.append(("fix", "sshd lets root log in; set `PermitRootLogin no`."))

    firewall = firewall if firewall is not None else ufw(port)
    if firewall["known"] and firewall["enabled"]:
        if "" in firewall["open"]:
            out.append(("note", f"the firewall lets port {port} in on every network; to keep it to the tailnet: "
                                f"`sudo ufw delete allow {port}/tcp` and "
                                f"`sudo ufw allow in on tailscale0 to any port {port} proto tcp`"))
        elif "tailscale0" in firewall["open"]:
            out.append(("ok", f"the firewall lets port {port} in on the tailnet only"))
        elif sshd["active"]:
            out.append(("fix", f"the firewall does not let port {port} in, so sshd cannot be reached: "
                               f"`sudo ufw allow in on tailscale0 to any port {port} proto tcp`"))
        elif not ts.get("ssh"):
            out.append(("note", f"the firewall does not let port {port} in yet; for sshd, open it on the tailnet "
                                f"only: `sudo ufw allow in on tailscale0 to any port {port} proto tcp` "
                                "(Tailscale SSH needs no rule)"))

    counts = key_counts if key_counts is not None else keys()
    if counts["top"]:
        out.append(("ok", f"{counts['top']} key(s) in ~/.ssh/authorized_keys can only run omaorchestra top"))
    else:
        out.append(("note", "no phone key yet: make one in your SSH app, then "
                            "`omaorchestra remote ssh-key --add` and paste its public key. That key can "
                            "only run `omaorchestra top`."))
    if counts["shell"]:
        out.append(("note", f"{counts['shell']} other key(s) in ~/.ssh/authorized_keys can open a full shell"))
    return out
