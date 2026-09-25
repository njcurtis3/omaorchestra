"""Nothing omaorchestra runs opens a network port (docs/security.md).

Two checks: the source uses no API that listens on the network, apart from
the two local sockets it is meant to have; and the running daemon (and the
app, where PySide6 is installed) holds no listening TCP socket and no UDP or
raw socket at all, going by the kernel's own tables.
"""

import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

ROOT = Path(__file__).resolve().parent.parent

# Anything that could listen on or bind a network socket.
LISTENING = re.compile(r"\.bind\(\s*\(|\.listen\(|start_server\(|create_server\(|HTTPServer|socketserver|"
                       r"SOCK_DGRAM|AF_INET|QTcpServer|QUdpSocket|WebSocketServer|QLocalServer|serve_forever")
# The two sockets omaorchestra has, both Unix sockets only its user can open.
ALLOWED = {
    ("src/omaorchestra/daemon.py", "start_unix_server("),    # the daemon's socket, 0600
    ("src/omaorchestra/app/instance.py", "QLocalServer"),     # the app's single-instance socket, user only
    ("src/omaorchestra/app/instance.py", ".listen("),
    ("src/omaorchestra/app/main.py", ".listen("),             # starts that one (instance.listen)
}


def sockets_of(pid):
    """Socket inodes a process holds."""
    inodes = set()
    for fd in os.listdir(f"/proc/{pid}/fd"):
        try:
            target = os.readlink(f"/proc/{pid}/fd/{fd}")
        except OSError:
            continue
        if target.startswith("socket:["):
            inodes.add(target[8:-1])
    return inodes


def network_sockets():
    """inode -> (table, local address) for listening TCP and every UDP or raw socket."""
    found = {}
    for table in ("tcp", "tcp6", "udp", "udp6", "raw", "raw6"):
        try:
            lines = Path(f"/proc/net/{table}").read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if table.startswith("tcp") and fields[3] != "0A":  # 0A: LISTEN
                continue
            found[fields[9]] = (table, fields[1])
    return found


def open_ports(pid):
    network = network_sockets()
    return [network[i] for i in sockets_of(pid) if i in network]


class SourceTest(unittest.TestCase):
    def test_nothing_listens_on_the_network(self):
        offending = []
        for folder in ("src", "plugin"):
            for path in sorted((ROOT / folder).rglob("*")):
                if path.suffix not in (".py", ".qml", ".js") or "__pycache__" in path.parts:
                    continue
                rel = str(path.relative_to(ROOT))
                for n, line in enumerate(path.read_text().splitlines(), 1):
                    code = line.split("#", 1)[0] if path.suffix == ".py" else line.split("//", 1)[0]
                    for match in LISTENING.finditer(code):
                        if (rel, match.group(0)) not in ALLOWED:
                            offending.append(f"{rel}:{n}: {line.strip()}")
        self.assertEqual(offending, [], "a network listener? see docs/security.md")


@unittest.skipUnless(Path("/proc/net/tcp").exists(), "needs Linux /proc")
class RunningTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        (tmp / "c.toml").write_text("[notifications]\nwaiting = false\nfinished_after = 0\n"
                                    "[remote]\npush = true\nanswer_prompts = true\n")
        self.env = {**os.environ, "OMAORCHESTRA_SOCKET": str(tmp / "o.sock"),
                    "OMAORCHESTRA_STATE_DIR": str(tmp / "state"), "OMAORCHESTRA_CONFIG": str(tmp / "c.toml"),
                    "XDG_STATE_HOME": str(tmp / "xdg"), "XDG_RUNTIME_DIR": str(tmp),
                    "DBUS_SESSION_BUS_ADDRESS": "unix:path=" + str(tmp / "no-bus")}
        for name in ("JOURNAL_STREAM", "OMARCHY_PATH"):
            self.env.pop(name, None)
        self.omaorchestra = str(ROOT / "bin" / "omaorchestra")
        self.log = open(tmp / "daemon.log", "w+")
        self.daemon = subprocess.Popen([self.omaorchestra, "daemon"], env=self.env, stderr=self.log)
        deadline = time.time() + 10
        while not (tmp / "o.sock").exists() and time.time() < deadline:
            time.sleep(0.05)

    def tearDown(self):
        self.daemon.terminate()
        self.daemon.wait(timeout=5)
        self.log.close()
        self.tmp.cleanup()

    def run_cli(self, *args, stdin=None):
        return subprocess.run([self.omaorchestra, *args], env=self.env, input=stdin, capture_output=True,
                              text=True, timeout=15)

    def test_the_daemon_opens_no_port(self):
        # Give it things to do: sessions, a waiting prompt, the queue, away mode.
        self.run_cli("hook", "claude", stdin='{"hook_event_name": "SessionStart", "session_id": "s1", "cwd": "/"}')
        self.run_cli("hook", "claude", stdin='{"hook_event_name": "Notification", "session_id": "s1", '
                                             '"cwd": "/", "message": "Allow?"}')
        self.run_cli("queue", "hold")
        self.run_cli("away", "on")
        time.sleep(1)
        self.assertIsNone(self.daemon.poll(), "the daemon stopped")
        self.assertTrue(sockets_of(self.daemon.pid), "no sockets at all? the check is not looking")
        self.assertEqual(open_ports(self.daemon.pid), [])

    def test_the_app_opens_no_port(self):
        try:
            subprocess.run(["/usr/bin/python3", "-c", "import PySide6"], check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("PySide6 not available")
        app = subprocess.Popen([self.omaorchestra, "app"], env={**self.env, "QT_QPA_PLATFORM": "offscreen"},
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(3)
            self.assertIsNone(app.poll(), "the app stopped")
            self.assertEqual(open_ports(app.pid), [])
        finally:
            app.terminate()
            app.wait(timeout=5)


class CheckTheCheckTest(unittest.TestCase):
    """The runtime check does see a listening port when there is one."""

    @unittest.skipUnless(Path("/proc/net/tcp").exists(), "needs Linux /proc")
    def test_a_listener_is_found(self):
        import socket
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen()
        try:
            self.assertEqual([table for table, _ in open_ports(os.getpid())], ["tcp"])
        finally:
            server.close()


if __name__ == "__main__":
    unittest.main()
