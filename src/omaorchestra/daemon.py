import asyncio
import json
import logging
import os
import signal
import socket

from . import __version__, config, notify, paths, procs, transcript, windows
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
# Events a subscriber may fall behind by before it is disconnected (it can
# reconnect and get a fresh snapshot).
SUBSCRIBER_BACKLOG = 1000
# Kept in step with service.py: the unit's RestartPreventExitStatus lists both,
# because restarting cannot fix either.
CONFIG_ERROR_EXIT = 2
ALREADY_RUNNING_EXIT = 3


class Daemon:
    def __init__(self, registry, is_alive=procs.is_alive, notifier=None, backlog=SUBSCRIBER_BACKLOG):
        self.registry = registry
        self.is_alive = is_alive
        self.notifier = notifier
        self.backlog = backlog
        self.subscribers = set()

    def changed(self, previous, session, reason=None):
        if self.notifier:
            self.notifier.changed(previous, session)
        if session is not None:
            self.publish({"event": "session", "session": session})
        else:
            self.publish({"event": "removed", "id": previous["id"], "reason": reason})

    def publish(self, message):
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # Too far behind: drop it rather than grow without bound.
                self.subscribers.discard(queue)
                queue.overflowed = True
                event(logging.WARNING, "subscriber dropped", reason="backlog-full", backlog=self.backlog)

    async def stream(self, reader, writer):
        """Serve a subscription: a snapshot, then every change until the client leaves."""
        queue = asyncio.Queue(maxsize=self.backlog)
        queue.overflowed = False
        # Snapshot and registration happen with no await in between, so no
        # change can fall between them.
        self.subscribers.add(queue)
        snapshot = {"ok": True, "sessions": self.registry.list()}
        event(logging.DEBUG, "subscribed", subscribers=len(self.subscribers))
        client_gone = asyncio.ensure_future(reader.read())  # EOF when the client disconnects
        try:
            writer.write(json.dumps(snapshot).encode() + b"\n")
            await writer.drain()
            while not client_gone.done():
                if queue.overflowed and queue.empty():
                    break
                next_message = asyncio.ensure_future(queue.get())
                done, _ = await asyncio.wait({next_message, client_gone}, return_when=asyncio.FIRST_COMPLETED)
                if next_message not in done:
                    next_message.cancel()
                    break
                writer.write(json.dumps(next_message.result()).encode() + b"\n")
                await writer.drain()
        except (ConnectionError, BrokenPipeError):
            pass
        finally:
            self.subscribers.discard(queue)
            client_gone.cancel()
            event(logging.DEBUG, "unsubscribed", subscribers=len(self.subscribers))

    async def focus(self, sid):
        session = self.registry.sessions.get(sid)
        if not session:
            return
        try:
            await asyncio.to_thread(windows.focus_session, session)
        except windows.WindowError as e:
            event(logging.WARNING, "focus failed", id=sid, error=str(e))

    def transcript_facts(self, before, request):
        """Model and branch: from the request, else from the transcript, read only when they may have
        changed (a new status) or are still unknown, so the frequent
        same-status updates cost nothing."""
        # An agent adapter may report these itself; that wins over the transcript.
        given = {k: request[k] for k in ("model", "branch", "title") if request.get(k)}
        path = request.get("transcript_path") or (before or {}).get("transcript_path")
        if not path or (before and before.get("status") == request.get("status") and before.get("model")):
            return given
        return {**transcript.info(path), **given}

    def prune(self):
        removed = self.registry.prune(self.is_alive)
        for sid, session in removed.items():
            event(logging.INFO, "session ended", id=sid, reason="process-gone", pid=session["pid"])
            self.changed(session, None, reason="process-gone")
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
            before = self.registry.sessions.get(request["session_id"])
            before = dict(before) if before else None
            previous = before["status"] if before else None
            session = self.registry.update(
                request["session_id"], request.get("agent", "unknown"), request["status"],
                cwd=request.get("cwd"), message=request.get("message"),
                pid=request.get("pid"), pid_start=request.get("pid_start"),
                transcript_path=request.get("transcript_path"),
                **self.transcript_facts(before, request),
            )
            if previous is None:
                event(logging.INFO, "session started", id=session["id"], agent=session["agent"],
                      status=session["status"], pid=session.get("pid"), cwd=session.get("cwd"))
            elif previous != session["status"]:
                event(logging.INFO, "session changed", id=session["id"], status=session["status"],
                      message=session.get("message"))
            self.changed(before, dict(session))
            return {"ok": True, "session": session}
        if cmd == "remove":
            removed = self.registry.remove(request["session_id"])
            if removed:
                reason = request.get("reason", "session-end")
                event(logging.INFO, "session ended", id=request["session_id"], reason=reason)
                self.changed(removed, None, reason=reason)
            return {"ok": True, "removed": removed is not None}
        event(logging.WARNING, "bad request", error=f"unknown command: {cmd}")
        return {"ok": False, "error": f"unknown command: {cmd}"}

    async def serve_client(self, reader, writer):
        try:
            while line := await reader.readline():
                try:
                    request = json.loads(line)
                    if isinstance(request, dict) and request.get("cmd") == "subscribe":
                        event(logging.DEBUG, "request", cmd="subscribe")
                        await self.stream(reader, writer)
                        return
                    response = self.handle(request)
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
        daemon.notifier = notify.Notifier(settings["notifications"], focus=daemon.focus)
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
