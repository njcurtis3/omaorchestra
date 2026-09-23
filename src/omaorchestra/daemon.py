import asyncio
import json
import os
import socket
import sys

from . import __version__, paths
from .registry import Registry


class AlreadyRunning(Exception):
    pass


def claim_socket(path):
    """Remove a stale socket, but refuse to start if a daemon is answering on it."""
    if not path.exists():
        return
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.connect(str(path))
    except OSError:
        path.unlink()
    else:
        raise AlreadyRunning(f"another omaorchestrad is already listening on {path}")
    finally:
        probe.close()


class Daemon:
    def __init__(self, registry):
        self.registry = registry

    def handle(self, request):
        cmd = request.get("cmd")
        if cmd == "ping":
            return {"ok": True, "version": __version__}
        if cmd == "list":
            return {"ok": True, "sessions": self.registry.list()}
        if cmd == "update":
            session = self.registry.update(
                request["session_id"], request.get("agent", "unknown"), request["status"],
                cwd=request.get("cwd"), message=request.get("message"),
            )
            return {"ok": True, "session": session}
        if cmd == "remove":
            return {"ok": True, "removed": self.registry.remove(request["session_id"]) is not None}
        return {"ok": False, "error": f"unknown command: {cmd}"}

    async def serve_client(self, reader, writer):
        try:
            while line := await reader.readline():
                try:
                    response = self.handle(json.loads(line))
                except (ValueError, KeyError, TypeError) as e:
                    response = {"ok": False, "error": str(e)}
                writer.write(json.dumps(response).encode() + b"\n")
                await writer.drain()
        finally:
            writer.close()


async def serve(sock_path, registry):
    claim_socket(sock_path)
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    old_umask = os.umask(0o177)
    try:
        server = await asyncio.start_unix_server(Daemon(registry).serve_client, path=str(sock_path))
    finally:
        os.umask(old_umask)
    os.chmod(sock_path, 0o600)
    return server


def run():
    sock_path = paths.socket_path()
    registry = Registry(paths.state_dir() / "sessions.json")

    async def main():
        server = await serve(sock_path, registry)
        print(f"omaorchestrad {__version__} listening on {sock_path}", flush=True)
        async with server:
            await server.serve_forever()

    try:
        asyncio.run(main())
    except AlreadyRunning as e:
        print(f"omaorchestrad: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass
    return 0
