import asyncio
import json
import logging
import os
import signal
import socket
import struct
import time

import subprocess

from . import (__version__, adapters, approvals, away, config, costs, launch, notify, paths, procs, remote, taskqueue, transcript,
               usage, windows)
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
# How often the lock screen is checked while away mode is automatic (and
# again right before each push).
LOCK_CHECK_INTERVAL = 10
# How often launched agents are looked for until their first hook arrives, and
# how long a found agent may stay silent before it is shown as waiting for you
# (a new agent often starts by asking whether to trust its folder, and runs no
# hooks until that is answered).
LAUNCH_CHECK_INTERVAL = 2
LAUNCH_QUIET_SECONDS = 10
NOT_STARTED_MESSAGE = adapters.Claude.silent_message

# Events a subscriber may fall behind by before it is disconnected (it can
# reconnect and get a fresh snapshot).
SUBSCRIBER_BACKLOG = 1000
# Kept in step with service.py: the unit's RestartPreventExitStatus lists both,
# because restarting cannot fix either.
CONFIG_ERROR_EXIT = 2
ALREADY_RUNNING_EXIT = 3


class Daemon:
    def __init__(self, registry, is_alive=procs.is_alive, notifier=None, backlog=SUBSCRIBER_BACKLOG,
                 settings=None, force_verbose=False, pusher=None):
        self.registry = registry
        self.is_alive = is_alive
        self.notifier = notifier
        self.pusher = pusher  # phone pushes (remote.Pusher), beside the desktop notifier
        self.backlog = backlog
        self.subscribers = set()
        self.settings = settings or config.defaults()
        self.force_verbose = force_verbose  # --verbose on the command line wins over the config
        self.pruner = None
        self.queue = taskqueue.TaskQueue(registry.path.parent / "queue.json")
        self.spawn = subprocess.Popen  # how queued agents are started (tests replace it)
        self._dispatching = False
        self.usage_check = usage.blocking  # tests replace it
        self.usage_refresh = usage.refresh_in_background
        self.blocked = None  # the usage limit holding the queue, if any
        self._last_refresh = 0
        # The user's PATH, as the latest hook saw it. Kept in memory only; the
        # daemon's own (systemd's) PATH usually lacks version-manager folders,
        # which starting another agent needs.
        self.user_path = None
        self.offered = set()  # sessions already offered a hand-off
        self.limited = set()  # busy agents known to be at a usage limit (pushed once)
        self.away = away.Away(registry.path.parent / "away.json", self.settings["remote"]["away_after"],
                              on_change=lambda state: self.publish({"event": "away", "away": state}))
        self.away.push = self.settings["remote"]["push"]
        self.is_locked = away.is_locked  # tests replace it
        self.idle_watch = None
        self.lock_watch = None
        self.approvals = approvals.Pending()  # permission prompts waiting for a remote answer

    # ----------------------------------------------------------------- away

    def watching_away(self):
        return self.away.push and self.away.mode == "auto"

    def sync_away(self):
        """Watch the lock screen and input only while that decides anything:
        pushes on, and away mode automatic."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no event loop (a synchronous test)
        minutes = self.settings["remote"]["away_after"]
        self.away.minutes = minutes
        if not self.watching_away():
            if self.idle_watch:
                self.idle_watch.stop()
                self.lock_watch.cancel()
                self.idle_watch = self.lock_watch = None
            self.away.update(locked=None, idle=None)
            return
        if self.idle_watch:
            if self.idle_watch.minutes != minutes:
                self.idle_watch.notify_after(minutes)
            return
        self.idle_watch = away.IdleWatch(minutes, lambda idle: self.away.update(idle=idle))
        self.idle_watch.start()

        async def watch_lock():
            while True:
                await self.check_lock()
                await asyncio.sleep(LOCK_CHECK_INTERVAL)
        self.lock_watch = loop.create_task(watch_lock())

    async def check_lock(self):
        self.away.update(locked=await asyncio.to_thread(self.is_locked))

    async def is_away(self):
        """For the pusher: are you away right now? The lock screen is looked
        at afresh, so a push right after locking is not lost."""
        if self.watching_away():
            await self.check_lock()
        return self.away.away

    def handle_away(self, request):
        mode = request.get("mode")
        if mode is not None:
            if mode not in away.MODES:
                return {"ok": False, "error": f"unknown away mode {mode!r} (use {', '.join(away.MODES)})"}
            self.away.update(mode=mode)
            self.sync_away()
        return {"ok": True, "away": self.away.state()}

    # ------------------------------------------------------------- approvals

    def publish_approvals(self):
        self.publish({"event": "approvals", "approvals": self.approvals.public()})

    async def ask_approval(self, request, reader):
        """Hold a permission prompt open for a remote answer (approvals.py):
        only in away mode, and only until you answer, the prompt is answered
        at the terminal, the agent stops waiting, or answer_wait runs out."""
        settings = self.settings["remote"]
        if not settings["answer_prompts"] or not request.get("session_id"):
            return {"ok": True, "decision": None, "reason": "off"}
        if not await self.is_away():
            return {"ok": True, "decision": None, "reason": "at the desk"}
        item = self.approvals.add(request, asyncio.get_running_loop().create_future())
        event(logging.INFO, "approval asked", id=item["id"], session=item["session_id"], summary=item["summary"])
        self.publish_approvals()
        client_gone = asyncio.ensure_future(reader.read())  # EOF when the agent gives up on the hook
        try:
            await asyncio.wait({item["future"], client_gone}, timeout=settings["answer_wait"],
                               return_when=asyncio.FIRST_COMPLETED)
            answer = item["future"].result() if item["future"].done() else {}
            gone = client_gone.done()
        finally:
            client_gone.cancel()
            self.approvals.remove(item["id"])
            self.publish_approvals()
        behavior, source = answer.get("behavior"), answer.get("source") or ""
        if behavior:
            outcome = "allowed" if behavior == "allow" else "denied"
        else:
            outcome = answer.get("reason") or ("the agent stopped waiting" if gone else "no answer in time")
        event(logging.INFO, "approval " + outcome, id=item["id"], session=item["session_id"], source=source)
        try:
            approvals.record(item, outcome, source)
        except OSError as e:
            event(logging.WARNING, "could not record an approval", error=str(e))
        if not behavior:
            return {"ok": True, "decision": None, "reason": outcome}
        return {"ok": True, "decision": {"behavior": behavior, "message": answer.get("message")}}

    @staticmethod
    def peer_pid(writer):
        """The PID of the process at the other end of a client connection."""
        sock = writer.get_extra_info("socket")
        try:
            creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        except (OSError, AttributeError):
            return None
        return struct.unpack("3i", creds)[0]

    def from_agent(self, pid):
        """The agent a client runs under, if any: an agent must not answer
        permission prompts, its own or another's."""
        if not pid:
            return None
        names = {n for a in adapters.ADAPTERS.values() for n in a.process_names}
        return procs.under_agent(pid, names)

    def answer_approval(self, request, peer=None):
        agent = self.from_agent(peer)
        if agent:
            event(logging.WARNING, "approval answer refused", id=request.get("id"), reason=f"sent from inside {agent}")
            return {"ok": False, "error": f"refused: this answer came from inside an agent ({agent}). Answer from "
                                          "your own terminal, or from omaorchestra top."}
        try:
            item = self.approvals.answer(request.get("id"), request.get("behavior"), request.get("message"),
                                         request.get("source") or "the command line")
        except KeyError as e:
            return {"ok": False, "error": e.args[0]}
        return {"ok": True, "approval": {k: v for k, v in item.items() if k != "future"}}

    # ---------------------------------------------------------------- queue

    def busy(self):
        """Agents holding a slot: working, or waiting for the user."""
        return sum(1 for s in self.registry.sessions.values() if s.get("status") in ("working", "needs-input"))

    def queue_snapshot(self):
        blocked = None
        if self.blocked:
            text = self.blocked.get("text") or usage.describe(self.blocked)
            blocked = dict(self.blocked, text=text)
        return self.queue.snapshot(self.busy(), self.settings["tasks"]["max_parallel"], blocked)

    def record_spend(self, session):
        """Add a routed session's new spend to today's ledger (on each status
        change, so the ledger follows the work as it happens)."""
        try:
            now = costs.session_cost(session)["usd"]
        except OSError:
            return
        delta = now - session.get("cost", 0.0)
        if delta > 0:
            costs.record(session["provider"], delta)
            session["cost"] = now
            self.registry.save()

    def budget_block(self):
        budget = self.settings["tasks"]["daily_budget"]
        spent = costs.spent_today()
        if budget and spent >= budget:
            return {"kind": "budget", "spent": spent, "budget": budget,
                    "text": f"today's provider spend is ${spent:.2f}, at the ${budget} daily budget"}
        return None

    def next_startable(self):
        """The first pending task nothing holds back, or None and the reason.
        Returns (item, reason, agent to start it with).

        Subscription usage limits hold tasks that run on the subscription;
        the daily budget holds tasks that run through a provider. A task held
        by its agent's limit starts on tasks.fallback_agent instead, when that
        one is set, can run the task, and is not at its own limit.
        """
        reason = None
        fallback = self.settings["tasks"]["fallback_agent"]
        for item in self.queue.tasks:
            if item["state"] != "pending":
                continue
            agent = item.get("agent") or "claude"
            block = self.budget_block() if item.get("provider") else self.usage_block(agent)
            if block is None:
                return item, None, agent
            if (fallback and fallback != agent and not item.get("provider") and not item.get("mcp_profile")
                    and self.usage_block(fallback) is None):
                return item, None, fallback
            reason = reason or block
        return None, reason, None

    def handoff(self, request):
        """Start another agent on a session's work (see handoff.py)."""
        from . import handoff as handing
        sid = request["session_id"]
        session = self.registry.sessions.get(sid)
        if session is None:
            raise ValueError(f"no session {sid}")
        agent = request.get("agent") or self.settings["tasks"]["fallback_agent"] or "claude"
        result = launch.run(handing.brief(session), handing.workdir(session), model=request.get("model"),
                            provider=request.get("provider"), agent=agent, worktree=False,
                            path=request.get("path") or self.user_path, spawn=self.spawn, request=self.handle)
        event(logging.INFO, "handed off", id=sid, to=agent, new=result["id"])
        stopped = False
        if request.get("stop"):
            from . import control
            try:
                control.stop(session)
                stopped = True
            except control.ControlError:
                pass
        return {"session_id": result["id"], "agent": agent, "stopped": stopped}

    def offer_handoffs(self):
        """When an agent reaches its usage limit while it has sessions at
        work, offer (once per session) to hand each one to the fallback agent."""
        fallback = self.settings["tasks"]["fallback_agent"]
        if not fallback or not self.notifier:
            return
        for sid, s in list(self.registry.sessions.items()):
            agent = s.get("agent") or "claude"
            if (sid in self.offered or agent == fallback or s.get("provider")
                    or s.get("status") not in ("working", "needs-input")):
                continue
            block = self.usage_check(agent, 0.99)
            if not block:
                continue
            self.offered.add(sid)
            project = (s.get("cwd") or "").rstrip("/").split("/")[-1] or "a session"
            label = adapters.ADAPTERS[fallback].label if fallback in adapters.ADAPTERS else fallback

            async def accept(sid=sid):
                try:
                    self.handoff({"session_id": sid, "agent": fallback})
                except (ValueError, launch.LaunchError) as e:
                    event(logging.WARNING, "hand-off failed", id=sid, error=str(e))

            event(logging.INFO, "hand-off offered", id=sid, to=fallback, reason=usage.describe(block))
            self.notifier.offer(f"{project}: {usage.describe(block)}", f"Hand its work to {label}?",
                                f"Hand off to {label}", accept)

    def check_limits(self):
        """Push once when an agent with sessions at work reaches a usage
        limit; again only after it has dropped below and come back."""
        if not self.pusher:
            return
        busy = {s.get("agent") or "claude" for s in self.registry.sessions.values()
                if s.get("status") in ("working", "needs-input") and not s.get("provider")}
        for agent in busy | self.limited:
            block = self.usage_check(agent, 0.99) if agent in busy else None
            if block and agent not in self.limited:
                self.limited.add(agent)
                event(logging.INFO, "usage limit reached", agent=agent, reason=usage.describe(block))
                self.pusher.limit_reached(block)
            elif not block:
                self.limited.discard(agent)

    def usage_block(self, agent="claude"):
        """The subscription limit that should hold `agent`'s tasks now, if
        any. Also asks Omarchy to refresh an old record (at most every 5
        minutes)."""
        threshold = self.settings["tasks"]["pause_at_usage"] / 100
        block = self.usage_check(agent, threshold)
        rec_age = usage.age(usage.record(agent))
        if (rec_age is None or rec_age > usage.REFRESH_AFTER) and time.time() - self._last_refresh > 300:
            self._last_refresh = time.time()
            self.usage_refresh(agent)
        return block

    def publish_queue(self):
        self.publish({"event": "queue", "queue": self.queue_snapshot()})

    def start_task(self, item, agent=None):
        """Launch a queued task now; returns the session id, or None if it
        failed (the task then stays in the queue, marked failed). `agent`
        starts it on another agent (the fallback) than the one it names."""
        own = item.get("agent") or "claude"
        agent = agent or own
        if agent != own:
            event(logging.INFO, "task falls back", task=item["id"], agent=agent, reason=f"{own} is at its limit")
        try:
            result = launch.run(
                item["task"], item["cwd"], permission_mode=item.get("permission_mode") if agent == own else None,
                model=item.get("model") if agent == own else None,
                extra=(item.get("extra") or ()) if agent == own else (),
                worktree=item.get("worktree"), agent_bin=item.get("agent_bin") if agent == own else None,
                path=item.get("path") or self.user_path, provider=item.get("provider"),
                mcp_profile=item.get("mcp_profile"), agent=agent,
                spawn=self.spawn, request=self.handle,
            )
        except launch.LaunchError as e:
            self.queue.fail(item, str(e))
            event(logging.WARNING, "task failed to start", task=item["id"], error=str(e))
            if self.pusher:
                self.pusher.task_failed(item, str(e))
            return None
        self.queue.tasks.remove(item)
        self.queue.save()
        event(logging.INFO, "task started", task=item["id"], id=result["id"])
        return result["id"]

    def dispatch(self):
        """Start pending tasks while there are free slots."""
        if self._dispatching or self.queue.held:
            return
        self._dispatching = True
        try:
            changed = False
            blocked = self.blocked
            while self.busy() < self.settings["tasks"]["max_parallel"]:
                item, blocked, agent = self.next_startable()
                if item is None:
                    break
                self.start_task(item, agent)
                changed = True
            if blocked != self.blocked:
                if blocked:
                    event(logging.INFO, "queue waiting", reason=blocked.get("text") or usage.describe(blocked))
                    if not self.blocked and self.pusher:
                        self.pusher.queue_blocked(blocked)
                elif self.blocked:
                    event(logging.INFO, "queue resumed", reason="usage limit no longer reached")
                self.blocked = blocked
                changed = True
            if changed:
                self.publish_queue()
        finally:
            self._dispatching = False

    def handle_queue(self, cmd, request):
        q = self.queue
        if cmd == "queue-list":
            return {"ok": True, "queue": self.queue_snapshot()}
        if cmd == "queue-add":
            item = q.add(request.get("item") or {}, paused=bool(request.get("paused")))
            event(logging.INFO, "task queued", task=item["id"], cwd=item["cwd"])
        elif cmd == "queue-cancel":
            item = q.remove(request["id"])
            event(logging.INFO, "task cancelled", task=item["id"])
        elif cmd == "queue-move":
            item = q.move(request["id"], int(request["position"]))
        elif cmd in ("queue-pause", "queue-resume"):
            item = q.set_state(request["id"], "paused" if cmd == "queue-pause" else "pending")
        elif cmd in ("queue-hold", "queue-release"):
            q.held = cmd == "queue-hold"
            q.save()
            item = None
            event(logging.INFO, "queue " + ("held" if q.held else "released"))
        elif cmd == "queue-run":
            item = q.find(request["id"])
            session_id = self.start_task(item)
            self.publish_queue()
            if session_id is None:
                return {"ok": False, "error": item["error"]}
            return {"ok": True, "session_id": session_id}
        else:
            return {"ok": False, "error": f"unknown command: {cmd}"}
        self.publish_queue()
        self.dispatch()
        return {"ok": True, "item": item, "queue": self.queue_snapshot()}

    def start_pruner(self):
        if self.pruner:
            self.pruner.cancel()
        self.pruner = asyncio.get_running_loop().create_task(
            prune_forever(self, self.settings["daemon"]["prune_interval"]))

    def reload(self):
        """Re-read the config and apply it; returns an error message, or "".

        A bad file leaves the running settings untouched.
        """
        from . import log
        try:
            new = config.load()
        except config.ConfigError as e:
            event(logging.WARNING, "reload failed", error=str(e))
            return str(e)
        old, self.settings = self.settings, new
        log.set_verbose(self.force_verbose or new["daemon"]["verbose"])
        if self.notifier:
            self.notifier.settings = new["notifications"]
        if self.pusher:
            self.pusher.settings, self.pusher.notifications = new["remote"], new["notifications"]
        self.away.update(push=new["remote"]["push"])
        self.sync_away()
        if self.pruner and new["daemon"]["prune_interval"] != old["daemon"]["prune_interval"]:
            self.start_pruner()
        self.publish_queue()
        self.dispatch()  # max_parallel may have grown
        event(logging.INFO, "config reloaded", prune_interval=new["daemon"]["prune_interval"],
              verbose=new["daemon"]["verbose"], waiting=new["notifications"]["waiting"],
              finished_after=new["notifications"]["finished_after"], push=new["remote"]["push"])
        return ""

    def changed(self, previous, session, reason=None):
        # A prompt answered at the terminal (the session moves on) or a
        # session gone: its remote requests are over.
        if previous and (session or {}).get("status") != "needs-input":
            self.approvals.settle_session(previous["id"], "answered at the terminal" if session else "session ended")
        if self.notifier:
            self.notifier.changed(previous, session)
        if self.pusher:
            self.pusher.changed(previous, session)
        self.record_approval(previous, session, reason)
        if session is not None:
            self.publish({"event": "session", "session": session})
        else:
            self.publish({"event": "removed", "id": previous["id"], "reason": reason})
        # A slot may have freed up (or filled): keep the queue moving, and
        # keep subscribers' busy count current.
        if not self._dispatching:
            busy_before = (previous or {}).get("status") in ("working", "needs-input")
            busy_after = (session or {}).get("status") in ("working", "needs-input")
            if busy_before != busy_after:
                self.publish_queue()
                self.dispatch()

    def record_approval(self, previous, session, reason=None):
        """Log requests for the user's approval, and how each ended."""
        from . import permissions
        before = (previous or {}).get("status")
        after = (session or {}).get("status")
        current = session or previous or {}
        try:
            if after == "needs-input" and before != "needs-input":
                permissions.log({"kind": "asked", "session": current["id"], "project": current.get("cwd"),
                                 "message": current.get("message")})
            elif before == "needs-input" and after != "needs-input":
                outcome = "continued" if after == "working" else "stopped waiting" if after == "idle" else \
                    f"session ended ({reason or 'ended'})"
                permissions.log({"kind": "answered", "session": current["id"], "outcome": outcome})
        except OSError as e:
            event(logging.WARNING, "could not record an approval", error=str(e))

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
        snapshot = {"ok": True, "sessions": self.registry.list(), "queue": self.queue_snapshot(),
                    "away": self.away.state(), "approvals": self.approvals.public()}
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
        given = {k: request[k] for k in ("model", "branch", "title", "task", "launching", "worktree", "provider")
                 if request.get(k)}
        path = request.get("transcript_path") or (before or {}).get("transcript_path")
        if not path or (before and before.get("status") == request.get("status") and before.get("model")):
            return given
        return {**transcript.info(path), **given}

    def adopt_launch(self, request):
        """An agent that cannot be told its session id reports its own, with
        the launch id omaorchestra gave it: move the placeholder session to
        the agent's id, keeping what the launch recorded (task, worktree...)."""
        launch_id, session_id = request.get("launch_id"), request.get("session_id")
        if not launch_id or launch_id == session_id:
            return
        placeholder = self.registry.sessions.get(launch_id)
        if not placeholder or not placeholder.get("launching") or session_id in self.registry.sessions:
            return
        self.registry.sessions.pop(launch_id)
        self.registry.sessions[session_id] = {**placeholder, "id": session_id}
        self.registry.save()
        event(logging.INFO, "session adopted", launch=launch_id, id=session_id)
        self.changed(placeholder, None, reason="adopted")

    def find_launched(self, sid, session):
        adapter = adapters.ADAPTERS.get(session.get("agent"), adapters.ADAPTERS["claude"])
        if adapter.supports_session_id:
            found = procs.find_session_process(sid)
            if found:
                return found
        return procs.find_env_process(launch.LAUNCH_VARIABLE, sid, adapter.process_names)

    @staticmethod
    def quiet_seconds(session):
        adapter = adapters.ADAPTERS.get(session.get("agent"))
        return adapter.quiet_seconds if adapter else LAUNCH_QUIET_SECONDS

    def claim_launches(self, now=None, find=None):
        """Find the processes of launched agents that have not reported yet,
        and flag the ones that stay silent as waiting for the user."""
        now = time.time() if now is None else now
        for sid, s in list(self.registry.sessions.items()):
            if not s.get("launching"):
                continue
            if "pid" not in s:
                found = find(sid) if find else self.find_launched(sid, s)
                if found:
                    self.registry.attach_process(sid, *found)
                    event(logging.INFO, "agent found", id=sid, pid=found[0])
                    self.changed(dict(s), dict(self.registry.sessions[sid]))
            elif now - s.get("started", now) > self.quiet_seconds(s) and s["status"] != "needs-input":
                before = dict(s)
                message = adapters.ADAPTERS.get(s.get("agent"), adapters.ADAPTERS["claude"]).silent_message
                session = self.registry.update(sid, s["agent"], "needs-input", message=message)
                event(logging.INFO, "session changed", id=sid, status="needs-input", message=message)
                self.changed(before, dict(session))

    def start_launch_watch(self, interval=LAUNCH_CHECK_INTERVAL):
        async def watch():
            while True:
                await asyncio.sleep(interval)
                if any(s.get("launching") for s in self.registry.sessions.values()):
                    self.claim_launches()
        self.launch_watch = asyncio.get_running_loop().create_task(watch())

    def prune(self):
        removed = self.registry.prune(self.is_alive)
        for sid, session in removed.items():
            reason = "process-gone" if "pid" in session else "did-not-start"
            event(logging.INFO, "session ended", id=sid, reason=reason, pid=session.get("pid"))
            self.changed(session, None, reason=reason)
            if reason == "did-not-start" and self.pusher:
                self.pusher.task_failed(session, "the agent did not start")
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
            self.user_path = request.pop("path", None) or self.user_path
            self.adopt_launch(request)
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
            if session.get("provider") and previous != session["status"] and session.get("transcript_path"):
                self.record_spend(session)
            if previous is None:
                event(logging.INFO, "session started", id=session["id"], agent=session["agent"],
                      status=session["status"], pid=session.get("pid"), cwd=session.get("cwd"))
            elif previous != session["status"]:
                event(logging.INFO, "session changed", id=session["id"], status=session["status"],
                      message=session.get("message"))
            self.changed(before, dict(session))
            return {"ok": True, "session": session}
        if cmd.startswith("queue-"):
            try:
                return self.handle_queue(cmd, request)
            except taskqueue.QueueError as e:
                return {"ok": False, "error": str(e)}
        if cmd == "handoff":
            try:
                return {"ok": True, **self.handoff(request)}
            except (ValueError, launch.LaunchError) as e:
                return {"ok": False, "error": str(e)}
        if cmd == "away":
            return self.handle_away(request)
        if cmd == "approvals":
            return {"ok": True, "approvals": self.approvals.public()}
        if cmd == "approval-answer":
            return self.answer_approval(request)
        if cmd == "reload":
            error = self.reload()
            return {"ok": not error, "error": error} if error else {"ok": True}
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
                    if isinstance(request, dict) and request.get("cmd") == "approval-answer":
                        response = self.answer_approval(request, self.peer_pid(writer))
                        writer.write(json.dumps(response).encode() + b"\n")
                        await writer.drain()
                        continue
                    if isinstance(request, dict) and request.get("cmd") == "approval-ask":
                        response = await self.ask_approval(request, reader)
                        try:
                            writer.write(json.dumps(response).encode() + b"\n")
                            await writer.drain()
                        except (ConnectionError, BrokenPipeError):
                            pass  # the agent stopped waiting for the hook
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
        daemon.offer_handoffs()
        daemon.check_limits()
        # A queue waiting on a usage limit gets another look (limits reset).
        if daemon.queue.next_pending():
            daemon.dispatch()


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
        daemon = Daemon(registry, settings=settings, force_verbose=verbose)
        daemon.notifier = notify.Notifier(settings["notifications"], focus=daemon.focus)
        daemon.pusher = remote.Pusher(settings["remote"], settings["notifications"], away=daemon.is_away)
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
        # `systemctl --user reload omaorchestrad` sends SIGHUP.
        loop.add_signal_handler(signal.SIGHUP, daemon.reload)
        daemon.start_pruner()
        daemon.start_launch_watch()
        daemon.away.save()  # for the bar widget, as with the registry
        daemon.sync_away()
        daemon.dispatch()  # tasks may have been waiting while the daemon was down
        try:
            async with server:
                await stop.wait()
                # Subscriptions never end on their own, and the server waits
                # for every connection before it finishes closing (Python
                # 3.12+), so close them, or stopping hangs while an app is open.
                server.close()
                server.close_clients()
        finally:
            daemon.pruner.cancel()
            daemon.launch_watch.cancel()
            if daemon.idle_watch:
                daemon.idle_watch.stop()
                daemon.lock_watch.cancel()
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
    except OSError as e:
        # Unix socket paths are capped at 107 bytes; a deep XDG_RUNTIME_DIR
        # or an unwritable one ends up here.
        event(logging.ERROR, "cannot listen", socket=sock_path, error=e.strerror or str(e))
        return 1
    return 0
