import json
import socket

from . import paths


class DaemonUnavailable(Exception):
    pass


def subscribe(connect_timeout=1.0):
    """Yield the snapshot (a list of sessions), then one event dict per change.

    Blocks between events; ends when the daemon closes the connection.
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(connect_timeout)
    try:
        sock.connect(str(paths.socket_path()))
        sock.sendall(json.dumps({"cmd": "subscribe"}).encode() + b"\n")
    except OSError as e:
        sock.close()
        raise DaemonUnavailable(str(e)) from e
    sock.settimeout(None)
    try:
        with sock.makefile("rb") as stream:
            first = True
            for line in stream:
                message = json.loads(line)
                yield message["sessions"] if first else message
                first = False
    finally:
        sock.close()


def request(payload, timeout=1.0):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(paths.socket_path()))
        sock.sendall(json.dumps(payload).encode() + b"\n")
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    except OSError as e:
        raise DaemonUnavailable(str(e)) from e
    finally:
        sock.close()
    return json.loads(data)
