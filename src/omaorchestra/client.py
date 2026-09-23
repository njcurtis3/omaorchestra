import json
import socket

from . import paths


class DaemonUnavailable(Exception):
    pass


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
