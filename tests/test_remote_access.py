import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from omaorchestra import __main__ as cli, remote_access as ra, setup

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICYiyXi1C/RxWDxEWo3vzzvCjSzKfcg+cXj7qtvuy5sE phone"
OTHER = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGl1cW9xZ2dxZ2Z4Y2Z4Z2Z4Z2Z4Z2Z4Z2Z4Z2Z4Z2Z4Z2Z4 laptop"


def fake_run(outputs):
    def run(args, **kw):
        out = outputs.get(" ".join(args))
        if out is None:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")
    return run


class TailscaleTest(unittest.TestCase):
    def test_running_with_ssh(self):
        run = fake_run({
            "tailscale status --json": json.dumps({"BackendState": "Running", "Self": {
                "DNSName": "desk.tail1.ts.net.", "TailscaleIPs": ["100.70.1.2", "fd7a:115c:a1e0::1"]}}),
            "tailscale debug prefs": json.dumps({"RunSSH": True}),
        })
        with mock.patch("shutil.which", return_value="/usr/bin/tailscale"):
            state = ra.tailscale(run)
        self.assertEqual(state, {"installed": True, "running": True, "name": "desk.tail1.ts.net",
                                 "ips": ["100.70.1.2", "fd7a:115c:a1e0::1"], "ssh": True})

    def test_stopped_or_missing(self):
        with mock.patch("shutil.which", return_value="/usr/bin/tailscale"):
            self.assertFalse(ra.tailscale(fake_run({}))["running"])
        with mock.patch("shutil.which", return_value=None):
            self.assertFalse(ra.tailscale(fake_run({}))["installed"])

    def test_tailnet_addresses(self):
        self.assertTrue(ra.on_tailnet("100.70.151.110"))
        self.assertTrue(ra.on_tailnet("[fd7a:115c:a1e0::1801:97d2]"))
        self.assertFalse(ra.on_tailnet("192.168.1.5"))
        self.assertFalse(ra.on_tailnet("*"))


class SshdTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_value_wins_through_includes_and_match_is_skipped(self):
        (self.dir / "d").mkdir()
        (self.dir / "d" / "50-keys.conf").write_text("PasswordAuthentication no\nListenAddress 100.70.1.2\n")
        (self.dir / "d" / "99-dist.conf").write_text("PasswordAuthentication yes\nKbdInteractiveAuthentication no\n")
        main = self.dir / "sshd_config"
        main.write_text(f"Include {self.dir}/d/*.conf\nPort=2222\n# PermitRootLogin yes\n"
                        "Match User guest\n  PasswordAuthentication yes\n  PermitRootLogin yes\n")
        settings = ra.sshd_settings(main)
        self.assertEqual((settings["passwords"], settings["keyboard"], settings["root"]),
                         (False, False, "prohibit-password"))
        self.assertEqual((settings["ports"], settings["listen"]), ([2222], ["100.70.1.2"]))

    def test_defaults(self):
        main = self.dir / "sshd_config"
        main.write_text("Subsystem sftp /usr/lib/ssh/sftp-server\n")
        settings = ra.sshd_settings(main)
        self.assertEqual((settings["ports"], settings["passwords"]), ([22], True))

    def test_listening(self):
        run = fake_run({"ss -Htln": "LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\nLISTEN 0 128 [::]:22 [::]:*\n"
                                    "LISTEN 0 5 127.0.0.1:631 0.0.0.0:*\nLISTEN 0 128 100.70.1.2:2222 0.0.0.0:*\n"})
        self.assertEqual(ra.listening([22], run), ["0.0.0.0", "[::]"])
        self.assertEqual(ra.listening([2222], run), ["100.70.1.2"])
        self.assertTrue(ra.everywhere(["[::]"]))


class UfwTest(unittest.TestCase):
    def rules(self, *tuples, enabled=True):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conf, rules = Path(tmp.name) / "ufw.conf", Path(tmp.name) / "user.rules"
        conf.write_text(f"ENABLED={'yes' if enabled else 'no'}\n")
        rules.write_text("*filter\n" + "".join(f"### tuple ### {t}\n" for t in tuples))
        return ra.ufw(22, conf, [rules])

    def test_open_where(self):
        self.assertEqual(self.rules("allow tcp 22 0.0.0.0/0 any 0.0.0.0/0 in_tailscale0")["open"], ["tailscale0"])
        self.assertEqual(self.rules("allow tcp 22 0.0.0.0/0 any 0.0.0.0/0 OpenSSH - in")["open"], [""])
        self.assertEqual(self.rules("allow any 20:30 0.0.0.0/0 any 0.0.0.0/0 in")["open"], [""])
        self.assertEqual(self.rules("allow udp 22 0.0.0.0/0 any 0.0.0.0/0 in",
                                    "deny tcp 22 0.0.0.0/0 any 0.0.0.0/0 in",
                                    "allow tcp 47984 0.0.0.0/0 any 0.0.0.0/0 in_tailscale0")["open"], [])
        self.assertFalse(self.rules(enabled=False)["enabled"])

    def test_unreadable(self):
        self.assertFalse(ra.ufw(22, "/no/such/ufw.conf")["known"])


class KeyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ssh" / "authorized_keys"

    def tearDown(self):
        self.tmp.cleanup()

    def test_line_runs_only_top(self):
        line = ra.key_line("/usr/bin/omaorchestra", KEY + "\n")
        self.assertEqual(line, 'command="/usr/bin/omaorchestra top",restrict,pty ' + KEY)
        self.assertIn(" my-phone", ra.key_line("/usr/bin/omaorchestra", KEY, comment="my-phone"))

    def test_rejects_what_is_not_a_public_key(self):
        for bad in ("", "hello", "-----BEGIN OPENSSH PRIVATE KEY-----", "ssh-ed25519 not*base64"):
            with self.assertRaises(ra.RemoteAccessError):
                ra.key_line("/usr/bin/omaorchestra", bad)

    def test_add_once_with_safe_modes_and_a_backup(self):
        self.path.parent.mkdir(mode=0o700)
        self.path.write_text(OTHER)  # no final newline
        line = ra.key_line("/usr/bin/omaorchestra", KEY)
        self.assertTrue(ra.add_key(line, self.path))
        self.assertFalse(ra.add_key(line, self.path), "a key is added once")
        self.assertEqual(self.path.read_text(), OTHER + "\n" + line + "\n")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(len(list(self.path.parent.glob("authorized_keys.bak-*"))), 1)
        self.assertEqual(ra.keys(self.path), {"top": 1, "shell": 1})

    def test_add_creates_the_folder(self):
        ra.add_key(ra.key_line("/usr/bin/omaorchestra", KEY), self.path)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        self.assertEqual(ra.keys(self.path), {"top": 1, "shell": 0})

    def test_counts_ignore_other_forced_commands(self):
        self.path.parent.mkdir()
        self.path.write_text(f'# comment\ncommand="/usr/bin/backup" {OTHER}\nno-pty,command="omaorchestra top" {KEY}\n')
        self.assertEqual(ra.keys(self.path), {"top": 1, "shell": 0})


class CheckTest(unittest.TestCase):
    TS = {"installed": True, "running": True, "name": "desk.tail1.ts.net", "ips": ["100.70.1.2"], "ssh": False}
    SSHD_ON = {"installed": True, "active": True, "enabled": True}
    CONFIG = {"readable": True, "ports": [22], "listen": [], "passwords": False, "keyboard": False,
              "root": "prohibit-password"}

    def levels(self, **parts):
        args = {"ts": self.TS, "sshd": self.SSHD_ON, "config": self.CONFIG,
                "firewall": {"known": True, "enabled": True, "open": ["tailscale0"]},
                "key_counts": {"top": 1, "shell": 0}, "listen": ["100.70.1.2"], **parts}
        return ra.check(**args)

    def test_all_good(self):
        out = self.levels()
        self.assertEqual([level for level, _ in out if level != "ok"], ["note"], out)  # only: Tailscale SSH is off
        self.assertIn("listens on the tailnet only", " ".join(t for _, t in out))

    def test_problems_are_explained(self):
        out = self.levels(config={**self.CONFIG, "passwords": True, "root": "yes"},
                          firewall={"known": True, "enabled": True, "open": [""]},
                          listen=["0.0.0.0"], key_counts={"top": 0, "shell": 2})
        text = " ".join(t for _, t in out)
        self.assertIn("PasswordAuthentication no", text)
        self.assertIn("PermitRootLogin no", text)
        self.assertIn("ListenAddress 100.70.1.2", text)
        self.assertIn("ufw allow in on tailscale0 to any port 22", text)
        self.assertIn("remote ssh-key --add", text)
        self.assertIn("2 other key(s)", text)
        self.assertEqual([level for level, t in out if "passwords" in t or "root" in t], ["fix", "fix"])

    def test_firewall_closed_while_sshd_runs(self):
        levels = [level for level, t in self.levels(firewall={"known": True, "enabled": True, "open": []})
                  if "firewall" in t]
        self.assertEqual(levels, ["fix"])

    def test_no_tailscale(self):
        out = ra.check(ts={"installed": False, "running": False, "name": "", "ips": [], "ssh": False},
                       sshd={"installed": False, "active": False, "enabled": False})
        self.assertEqual(out[0][0], "fix")
        self.assertIn("omarchy-install-service-tailscale", out[0][1])

    def test_tailscale_ssh_needs_nothing_else(self):
        out = ra.check(ts={**self.TS, "ssh": True}, sshd={"installed": True, "active": False, "enabled": False},
                       config=self.CONFIG, firewall={"known": True, "enabled": True, "open": []},
                       key_counts={"top": 0, "shell": 0})
        text = " ".join(t for _, t in out)
        self.assertIn("ssh ", text)
        self.assertNotIn("does not let port", text)
        self.assertNotIn("ssh-key", text, "no sshd advice when sshd is not the way in")
        self.assertEqual({level for level, _ in out}, {"ok"})


class SetupStepTest(unittest.TestCase):
    def test_remote_runs_only_when_asked(self):
        self.assertNotIn("remote", setup.selected())
        self.assertEqual(setup.selected(only=["remote"]), ["remote"])
        self.assertNotIn("remote", setup.selected(skip=["menu"]))

    def test_setup_only_remote_changes_nothing(self):
        with mock.patch.object(ra, "check", return_value=[("ok", "on the tailnet"), ("fix", "do this")]):
            lines = setup.setup("/usr/bin/omaorchestra", only=["remote"])
        self.assertEqual(lines[:2], ["remote: ok   on the tailnet", "remote: fix  do this"])
        self.assertIn("changed nothing", lines[-1])
        self.assertIn("nothing to undo", setup.teardown(only=["remote"])[0])


class CliTest(unittest.TestCase):
    def test_prints_the_line_or_adds_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            pub = Path(tmp) / "phone.pub"
            pub.write_text(KEY + "\n")
            target = Path(tmp) / "ssh" / "authorized_keys"
            out = io.StringIO()
            with mock.patch.object(cli, "own_binary", return_value="/usr/bin/omaorchestra"), \
                    mock.patch.object(ra, "authorized_keys_path", return_value=target), \
                    redirect_stdout(out), mock.patch("sys.stderr", io.StringIO()):
                self.assertEqual(cli.main(["remote", "ssh-key", str(pub)]), 0)
                self.assertFalse(target.exists(), "printing adds nothing")
                self.assertEqual(cli.main(["remote", "ssh-key", "--add", str(pub)]), 0)
                self.assertEqual(cli.main(["remote", "ssh-key", "--add", str(pub)]), 0)
            self.assertIn('command="/usr/bin/omaorchestra top",restrict,pty', out.getvalue())
            self.assertIn("already in", out.getvalue())
            self.assertEqual(ra.keys(target), {"top": 1, "shell": 0})


if __name__ == "__main__":
    unittest.main()
