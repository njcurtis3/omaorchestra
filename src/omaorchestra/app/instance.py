"""Keep one app window: a second launch asks the first to show itself,
optionally on a given session or fleet run ("activate <session id>",
"activate fleet=<run id>")."""

from PySide6.QtNetwork import QLocalServer, QLocalSocket

ACTIVATE = b"activate\n"


def server_name():
    """A full path in the user's own runtime folder: a bare name would put the
    socket in the shared /tmp, where another user could take the name first."""
    from .. import paths
    return str(paths.runtime_dir() / "omaorchestra-app.sock")


def ask_running_instance(name=None, timeout_ms=300, session="", fleet=""):
    """True if another instance answered (and was asked to activate)."""
    sock = QLocalSocket()
    sock.connectToServer(name or server_name())
    if not sock.waitForConnected(timeout_ms):
        return False
    words = [ACTIVATE.strip()] + ([session.encode()] if session else []) + ([b"fleet=" + fleet.encode()] if fleet else [])
    sock.write(b" ".join(words) + b"\n")
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    return True


def listen(on_activate, name=None, parent=None):
    """Serve activation requests, calling on_activate(session_id or "",
    fleet run id or ""); returns the server (keep a reference)."""
    name = name or server_name()
    QLocalServer.removeServer(name)  # a stale socket from a crashed instance
    server = QLocalServer(parent)
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)

    def accept():
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()

            def read(conn=conn):
                words = bytes(conn.readAll().data()).decode(errors="replace").split()
                if words and words[0] == ACTIVATE.strip().decode():
                    rest = words[1:]
                    fleet_id = next((w[6:] for w in rest if w.startswith("fleet=")), "")
                    session_id = next((w for w in rest if "=" not in w), "")
                    on_activate(session_id, fleet_id)
                conn.disconnectFromServer()

            conn.readyRead.connect(read)

    server.newConnection.connect(accept)
    if not server.listen(name):
        raise RuntimeError(f"cannot listen on {name}: {server.errorString()}")
    return server
