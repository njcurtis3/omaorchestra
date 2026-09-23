"""Keep one app window: a second launch asks the first to show itself."""

import os

from PySide6.QtNetwork import QLocalServer, QLocalSocket

ACTIVATE = b"activate\n"


def server_name():
    return f"omaorchestra-app-{os.getuid()}"


def ask_running_instance(name=None, timeout_ms=300):
    """True if another instance answered (and was asked to activate)."""
    sock = QLocalSocket()
    sock.connectToServer(name or server_name())
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(ACTIVATE)
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    return True


def listen(on_activate, name=None, parent=None):
    """Serve activation requests; returns the server (keep a reference)."""
    name = name or server_name()
    QLocalServer.removeServer(name)  # a stale socket from a crashed instance
    server = QLocalServer(parent)
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)

    def accept():
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()

            def read(conn=conn):
                if bytes(conn.readAll().data()).startswith(ACTIVATE.strip()):
                    on_activate()
                conn.disconnectFromServer()

            conn.readyRead.connect(read)

    server.newConnection.connect(accept)
    if not server.listen(name):
        raise RuntimeError(f"cannot listen on {name}: {server.errorString()}")
    return server
