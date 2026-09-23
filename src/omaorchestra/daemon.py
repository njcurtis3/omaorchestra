import asyncio
import json
import os
import socket
import sys

from . import __version__, config, paths, procs
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


PRUNE_INTERVAL = 30
# Kept in step with service.py: the unit's RestartPreventExitStatus lists both,
# because restarting cannot fix either.
CONFIG_ERROR_EXIT = 2
ALREADY_RUNNING_EXIT = 3


class Daemon:
    def __init__(self, registry, is_alive=procs.is_alive):
        self.registry = registry
        self.is_alive = is_alive

    def prune(self):
        return self.registry.prune(self.is_alive)

    def handle(self, request):
        cmd = request.get("cmd")
        if cmd == "ping":
            return {"ok": True, "version": __version__}
        if cmd == "list":
            self.prune()
            return {"ok": True, "sessions": self.registry.list()}
        if cmd == "update":
            session = self.registry.update(
                request["session_id"], request.get("agent", "unknown"), request["status"],
                cwd=request.get("cwd"), message=request.get("message"),
                pid=request.get("pid"), pid_start=request.get("pid_start"),
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


async def prune_forever(daemon, interval=PRUNE_INTERVAL):
    while True:
        await asyncio.sleep(interval)
        daemon.prune()


async def serve(sock_path, registry, daemon=None):
    daemon = daemon or Daemon(registry)
    claim_socket(sock_path)
    # Write the registry up front so file watchers (the bar widget) see it
    # exist from the moment the daemon runs.
    registry.save()
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    old_umask = os.umask(0o177)
    try:
        server = await asyncio.start_unix_server(daemon.serve_client, path=str(sock_path))
    finally:
        os.umask(old_umask)
    os.chmod(sock_path, 0o600)
    return server


def run():
    try:
        settings = config.load()
    except config.ConfigError as e:
        print(f"omaorchestrad: {e}", file=sys.stderr)
        return CONFIG_ERROR_EXIT
    sock_path = paths.socket_path()
    registry = Registry(paths.state_dir() / "sessions.json")

    async def main():
        daemon = Daemon(registry)
        # Catch sessions that died while the daemon was down.
        daemon.prune()
        server = await serve(sock_path, registry, daemon)
        print(f"omaorchestrad {__version__} listening on {sock_path}", flush=True)
        pruner = asyncio.create_task(prune_forever(daemon, settings["daemon"]["prune_interval"]))
        try:
            async with server:
                await server.serve_forever()
        finally:
            pruner.cancel()

    try:
        asyncio.run(main())
    except AlreadyRunning as e:
        print(f"omaorchestrad: {e}", file=sys.stderr)
        return ALREADY_RUNNING_EXIT
    except KeyboardInterrupt:
        pass
    return 0
