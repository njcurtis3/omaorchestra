"""Keep one app window: a second launch asks the first to show itself,
optionally on a given session ("activate <session id>")."""

import os

from PySide6.QtNetwork import QLocalServer, QLocalSocket

ACTIVATE = b"activate\n"


def server_name():
    return f"omaorchestra-app-{os.getuid()}"


def ask_running_instance(name=None, timeout_ms=300, session=""):
    """True if another instance answered (and was asked to activate)."""
    sock = QLocalSocket()
    sock.connectToServer(name or server_name())
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(ACTIVATE.strip() + (b" " + session.encode() if session else b"") + b"\n")
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    return True


def listen(on_activate, name=None, parent=None):
    """Serve activation requests, calling on_activate(session_id or "");
    returns the server (keep a reference)."""
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
                    on_activate(words[1] if len(words) > 1 else "")
                conn.disconnectFromServer()

            conn.readyRead.connect(read)

    server.newConnection.connect(accept)
    if not server.listen(name):
        raise RuntimeError(f"cannot listen on {name}: {server.errorString()}")
    return server
