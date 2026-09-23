import asyncio
import json
import logging
import os
import signal
import socket

from . import __version__, config, paths, procs
from .log import event
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
        removed = self.registry.prune(self.is_alive)
        for sid, session in removed.items():
            event(logging.INFO, "session ended", id=sid, reason="process-gone", pid=session["pid"])
        return list(removed)

    def handle(self, request):
        event(logging.DEBUG, "request", **{k: v for k, v in request.items() if k != "message"})
        cmd = request.get("cmd")
        if cmd == "ping":
            return {"ok": True, "version": __version__}
        if cmd == "list":
            self.prune()
            return {"ok": True, "sessions": self.registry.list()}
        if cmd == "update":
            previous = self.registry.sessions.get(request["session_id"], {}).get("status")
            session = self.registry.update(
                request["session_id"], request.get("agent", "unknown"), request["status"],
                cwd=request.get("cwd"), message=request.get("message"),
                pid=request.get("pid"), pid_start=request.get("pid_start"),
            )
            if previous is None:
                event(logging.INFO, "session started", id=session["id"], agent=session["agent"],
                      status=session["status"], pid=session.get("pid"), cwd=session.get("cwd"))
            elif previous != session["status"]:
                event(logging.INFO, "session changed", id=session["id"], status=session["status"],
                      message=session.get("message"))
            return {"ok": True, "session": session}
        if cmd == "remove":
            removed = self.registry.remove(request["session_id"]) is not None
            if removed:
                event(logging.INFO, "session ended", id=request["session_id"], reason="session-end")
            return {"ok": True, "removed": removed}
        event(logging.WARNING, "bad request", error=f"unknown command: {cmd}")
        return {"ok": False, "error": f"unknown command: {cmd}"}

    async def serve_client(self, reader, writer):
        try:
            while line := await reader.readline():
                try:
                    response = self.handle(json.loads(line))
                except (ValueError, KeyError, TypeError) as e:
                    event(logging.WARNING, "bad request", error=repr(e))
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


def run(verbose=False):
    from . import log
    log.setup(verbose)
    try:
        settings = config.load()
    except config.ConfigError as e:
        event(logging.ERROR, "bad config", error=str(e))
        return CONFIG_ERROR_EXIT
    log.set_verbose(verbose or settings["daemon"]["verbose"])
    sock_path = paths.socket_path()
    registry = Registry(paths.state_dir() / "sessions.json")

    async def main():
        daemon = Daemon(registry)
        # Catch sessions that died while the daemon was down.
        daemon.prune()
        server = await serve(sock_path, registry, daemon)
        socket_inode = os.stat(sock_path).st_ino
        event(logging.INFO, "started", version=__version__, socket=sock_path,
              registry=registry.path, sessions=len(registry.sessions),
              prune_interval=settings["daemon"]["prune_interval"], config=config.path())
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda s=sig: (event(logging.INFO, "stopping", signal=s.name), stop.set()))
        pruner = asyncio.create_task(prune_forever(daemon, settings["daemon"]["prune_interval"]))
        try:
            async with server:
                await stop.wait()
        finally:
            pruner.cancel()
            # Remove the socket only if it is still the one we bound.
            try:
                if os.stat(sock_path).st_ino == socket_inode:
                    os.unlink(sock_path)
            except OSError:
                pass
        event(logging.INFO, "stopped")

    try:
        asyncio.run(main())
    except AlreadyRunning as e:
        event(logging.ERROR, "already running", error=str(e))
        return ALREADY_RUNNING_EXIT
    return 0
