import asyncio
import json
import logging
import os
import shutil
import signal
import socket
import struct
import time
from pathlib import Path

import subprocess

from . import (__version__, adapters, approvals, away, chain, config, costs, fleet, fleet_close, fleet_graph, fleet_scope,
               history,
               launch, notify,
               paths, procs, remote, roles, schedules, taskqueue, transcript, usage, windows, worktrees)
from .log import event
from .registry import CARRIED, Registry


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
# How often schedules are checked for a time that has come round.
SCHEDULE_CHECK_INTERVAL = 30
NOT_STARTED_MESSAGE = adapters.Claude.silent_message

# Events a subscriber may fall behind by before it is disconnected (it can
# reconnect and get a fresh snapshot).
SUBSCRIBER_BACKLOG = 1000
# Kept in step with service.py: the unit's RestartPreventExitStatus lists both,
# because restarting cannot fix either.
CONFIG_ERROR_EXIT = 2
ALREADY_RUNNING_EXIT = 3


# A person's answers at a fleet run's gates: refused from inside an agent.
FLEET_GATE_COMMANDS = ("fleet-approve", "fleet-send-back", "fleet-drop", "fleet-shape", "fleet-close",
                       "fleet-accept-scope", "fleet-scope-back", "fleet-limits", "fleet-retry", "fleet-pause",
                       "fleet-resume")

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
        self.schedules = schedules.Schedules(registry.path.parent / "schedules.json")
        self.fleet_seen = {}  # run id -> (status, gate, reason) last told about
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
        self.away.answers = self.settings["remote"]["answer_prompts"]
        self.is_locked = away.is_locked  # tests replace it
        self.idle_watch = None
        self.lock_watch = None
        self.approvals = approvals.Pending()  # permission prompts waiting for a remote answer
        self.git_head = history.head  # tests replace it
        self.history_path = registry.path.parent / "history.jsonl"  # beside sessions.json
        self.history_append = lambda record: history.append(record, self.history_path)
        self.history_tasks = set()
        self._history_tended = 0

    # ----------------------------------------------------------------- away

    def watching_away(self):
        return self.away.active and self.away.mode == "auto"

    def sync_away(self):
        """Watch the lock screen and input only while that decides anything:
        pushes or remote answers on, and away mode automatic."""
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

    # --------------------------------------------------------------- history

    def record_history(self, session, reason):
        """Append the ended session to history.jsonl, off the event loop (its
        cost comes from reading the transcript)."""
        settings = self.settings["history"]

        def write():
            try:
                self.history_append(history.build(session, reason, cost_fn=costs.session_cost,
                                                  titles=settings["titles"]))
            except OSError as e:
                event(logging.WARNING, "could not record history", id=session.get("id"), error=str(e))

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            write()  # no event loop (a synchronous test)
            return
        task = loop.create_task(asyncio.to_thread(write))
        self.history_tasks.add(task)
        task.add_done_callback(self.history_tasks.discard)

    def tend_history(self, now=None):
        """Retention: drop old records (once a day), and task text if titles
        is off."""
        now = time.time() if now is None else now
        if now - self._history_tended < 86400:
            return
        self._history_tended = now
        settings = self.settings["history"]
        try:
            dropped = history.prune(settings["keep_days"], now, self.history_path)
            if not settings["titles"]:
                history.forget_titles(self.history_path)
        except OSError as e:
            event(logging.WARNING, "could not prune history", error=str(e))
            return
        if dropped:
            event(logging.INFO, "history pruned", dropped=dropped, keep_days=settings["keep_days"])

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
                self.registry.sessions[sid]["stopping"] = True
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
                mcp_profile=item.get("mcp_profile"), agent=agent, role=item.get("role"),
                spawn=self.spawn, request=self.handle, session_fields=self.chain_fields(item),
            )
        except launch.LaunchError as e:
            self.queue.fail(item, str(e))
            event(logging.WARNING, "task failed to start", task=item["id"], error=str(e))
            self.fleet_node_launched(item, None, agent, error=str(e))
            if self.pusher:
                self.pusher.task_failed(item, str(e))
            return None
        self.queue.tasks.remove(item)
        self.queue.mark_started(item, result["id"])
        self.fleet_node_launched(item, result["id"], agent)
        self.schedule_task_started(item, result["id"])
        for child in self.queue.children(parent_id=item["id"]):
            child["parent_session"] = result["id"]  # it now waits on the session
        self.queue.save()
        event(logging.INFO, "task started", task=item["id"], id=result["id"])
        return result["id"]

    # ---------------------------------------------------------------- chains

    @staticmethod
    def chain_fields(item):
        """What the started session carries of its chain."""
        fields = {k: item.get(k) for k in ("chain", "step", "review", "fleet", "node") if item.get(k)}
        if item.get("worktree_path"):
            fields["worktree"] = item["worktree_path"]  # it runs in the step before's worktree
        return fields

    def link_chain(self, fields):
        """Fill in a new chained task's place: the task or session it follows
        (by id or prefix), its chain and step; raises QueueError."""
        after = (fields.get("after") or "").strip()
        if not after:
            return None
        parent_session = None
        try:
            parent = self.queue.find(after)
            parent.setdefault("chain", parent["id"])
            parent["chain"] = parent["chain"] or parent["id"]
            parent["step"] = parent.get("step") or 1
            fields.update(after=parent["id"], chain=parent["chain"], step=parent["step"] + 1)
        except taskqueue.QueueError:
            # A task that already started: follow the session it became.
            sid = self.queue.started_session(after)
            matches = ([self.registry.sessions[sid]] if sid in self.registry.sessions else
                       [s for key, s in self.registry.sessions.items() if key.startswith(after)])
            if len(matches) != 1:
                if sid:
                    raise taskqueue.QueueError(f"task {after} already ran and its session has ended; queue the "
                                               "next step on its own") from None
                raise taskqueue.QueueError(f"no queued task or session matching {after}") from None
            parent_session = matches[0]
            if not parent_session.get("chain"):
                # It becomes the first step of a chain, named after its task if it was one.
                parent_session["chain"] = next((tid for tid, s in self.queue.started.items()
                                                if s == parent_session["id"]), parent_session["id"])
                parent_session["step"] = 1
                self.registry.save()
            fields.update(after=None, parent_session=parent_session["id"], chain=parent_session["chain"],
                          step=(parent_session.get("step") or 1) + 1)
        limit = self.settings["tasks"]["max_chain_steps"]
        if fields["step"] > limit:
            raise taskqueue.QueueError(f"that would be step {fields['step']} of a chain; the limit is {limit} "
                                       "(tasks.max_chain_steps)")
        return parent_session

    def prepare_step(self, child, parent):
        """Make a chained task ready to run after `parent` (a session): the
        brief, the worktree, or the diff to review. Raises ChainError."""
        base = child.setdefault("base_task", child["task"])
        if child.get("same_worktree") and parent.get("cwd") and Path(parent["cwd"]).is_dir():
            child.update(cwd=parent["cwd"], worktree=False, worktree_path=parent.get("worktree"))
        if child.get("review"):
            diff, truncated = chain.diff_for(parent)
            child["task"] = chain.review_task(base, diff, truncated)
        elif child.get("brief", True) is not False:
            child["task"] = base + "\n\n" + chain.parent_brief(parent)

    def step_ended(self, session, finished, outcome=None):
        """A session some chained tasks wait on has finished, or not: release
        them, or hold them with the reason."""
        if finished and session.get("review"):
            self.save_review(session)
        waiting = self.queue.children(session_id=session["id"])
        for child in waiting:
            if not finished:
                child.update(state="held", error=f"the step before it {outcome}")
                event(logging.INFO, "chain held", task=child["id"], reason=child["error"])
                continue
            try:
                self.prepare_step(child, session)
            except chain.ChainError as e:
                child.update(state="held", error=str(e))
                event(logging.INFO, "chain held", task=child["id"], reason=str(e))
                continue
            child.update(state="pending", error=None)
            event(logging.INFO, "chain step released", task=child["id"], after=session["id"])
        if waiting:
            self.queue.save()
            self.publish_queue()
            self.dispatch()

    def save_review(self, session):
        def write():
            try:
                review = chain.record_review(session)
            except OSError as e:
                event(logging.WARNING, "could not save a review", id=session["id"], error=str(e))
                return
            if review:
                event(logging.INFO, "review saved", id=session["id"], verdict=review["verdict"], file=review["file"])
        try:
            asyncio.get_running_loop().create_task(asyncio.to_thread(write))
        except RuntimeError:
            write()

    # ---------------------------------------------------------------- fleets

    def fleet_activity(self, previous, session, reason=None):
        """A fleet node's session changed: one line in its run's activity."""
        current = session or previous
        if not current or not current.get("fleet") or not current.get("node"):
            return
        if session is None:
            entry = {"event": "ended", "reason": reason}
        elif previous is None or previous.get("status") != session.get("status"):
            entry = {"event": session.get("status")}
        else:
            return
        try:
            fleet.activity(current["fleet"], {**entry, "node": current["node"], "session": current["id"]})
        except (fleet.RunError, OSError) as e:
            event(logging.WARNING, "fleet activity not recorded", run=current["fleet"], error=str(e))
            return
        waiting = bool(session and session.get("status") == "needs-input")
        if waiting or (previous and previous.get("status") == "needs-input"):
            # The run pauses while a node waits for you: say which one.
            try:
                state = fleet.load(current["fleet"])
                n = fleet.node(state, current["node"])
            except fleet.RunError:
                return
            if n.get("session") == current["id"] and bool(n.get("waiting")) != waiting:
                n["waiting"] = waiting
                self.fleet_saved(state, f"{n['id']} " + ("waits for you" if waiting else "goes on"))

    def fleet_watch(self, now=None):
        """Flag a running node whose agent has been working with no sign of
        life (no hook report) for the run's stall_minutes; clear the flag
        once it reports again. Flagged, never stopped."""
        now = now or time.time()
        for state in fleet.runs():
            if state["status"] not in ("running", "held", "at-gate"):
                continue
            minutes = state["template"].get("stall_minutes", 20)
            stalled, changed = [], False
            for n in state["nodes"].values():
                if n["status"] != "running":
                    continue
                session = self.registry.sessions.get(n.get("session")) or {}
                quiet = session.get("status") == "working" and now - session.get("updated", now) >= minutes * 60
                if quiet and not n.get("stalled"):
                    n["stalled"] = session["updated"]
                    stalled.append(n["id"])
                    fleet.activity(state["id"], {"event": "stalled", "node": n["id"]}, now)
                    changed = True
                elif not quiet and n.get("stalled"):
                    n["stalled"] = None
                    changed = True
            if changed:
                self.fleet_saved(state, "stalled: " + ", ".join(stalled) if stalled else "no longer stalled")
            if stalled and self.notifier:
                self.notifier.tell(f"{Path(state['folder']).name}: {', '.join(stalled)} looks stalled",
                                   f"No sign of life for {minutes} minutes. Check its window, or stop it.")

    def fleet_node_ended(self, session, finished, outcome=None):
        """A fleet node's session finished its work (its reply is read, off
        the event loop, then checked and stored) or ended without finishing
        (the node fails and the run holds)."""
        run_id, nid = session.get("fleet"), session.get("node")
        if not run_id or not nid:
            return
        try:
            state = fleet.load(run_id)
            n = fleet.node(state, nid)
        except fleet.RunError as e:
            event(logging.WARNING, "fleet node not found", run=run_id, node=nid, error=str(e))
            return
        if n.get("session") != session["id"] or n["status"] not in ("running", "held"):
            return  # another attempt's session, or a node already settled
        if finished and n["status"] == "held" and n.get("error_kind") != "reply":
            return
        builder = n["role"] == "builder"

        def read():
            cost = None
            try:
                found = costs.session_cost(session) if session.get("transcript_path") else None
                cost = {"usd": round(found["usd"], 4), "real": bool(found.get("real"))} if found else None
            except (OSError, KeyError, TypeError, ValueError):
                pass
            if not finished:
                return None, None, None, cost
            git = fleet.git_facts(session)
            changed = fleet_scope.changed(session["cwd"], git["base"]) if builder and git and git.get("base") else None
            return fleet.read_reply(session), git, changed, cost

        def record(text, git, changed, cost):
            try:
                state = fleet.load(run_id)
                n = fleet.node(state, nid)
            except fleet.RunError:
                return
            if n.get("session") != session["id"] or n["status"] not in ("running", "held"):
                return
            if not finished:
                fleet.node_failed(state, nid, outcome or "ended", cost=cost)
                self.fleet_saved(state, f"{nid} failed: its session {outcome}")
                return
            n = fleet.record_reply(state, nid, text, git=git, changed=changed, cost=cost)
            self.fleet_advance(state, f"{nid} " + ("finished" if n["status"] == "done" else f"held: {n['error']}"))

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            record(*read())
            return

        async def later():
            record(*await asyncio.to_thread(read))
        loop.create_task(later())

    def handle_fleet(self, cmd, request, peer=None):
        if cmd in FLEET_GATE_COMMANDS and not (cmd == "fleet-close" and request.get("check")):
            agent = self.from_agent(peer)
            if agent:
                event(logging.WARNING, "fleet answer refused", run=request.get("run"), reason=f"sent from inside {agent}")
                return {"ok": False, "error": f"refused: this answer came from inside an agent ({agent}). Answer from "
                                              "your own terminal, or from omaorchestra top."}
        if cmd == "fleet-start":
            template = fleet_graph.get(request.get("fleet") or "auto")
            if request.get("shape"):
                if request["shape"] not in fleet_graph.SHAPES:
                    raise fleet.RunError(f"the shape is one of {', '.join(fleet_graph.SHAPES)}")
                template = {**template, "shape": request["shape"]}
            state = fleet.create(request.get("goal") or "", request.get("folder") or "", template,
                                 path=request.get("path") or self.user_path, budget=request.get("budget"))
            event(logging.INFO, "fleet run started", run=state["id"], fleet=template["name"], folder=state["folder"])
            self.fleet_advance(state, "started")
            return {"ok": True, "run": state}
        if cmd == "fleet-list":
            return {"ok": True, "runs": fleet.runs()}
        state = fleet.find(request.get("run") or "")
        if cmd == "fleet-show":
            return {"ok": True, "run": state, "activity": fleet.read_activity(state["id"])}
        if cmd == "fleet-approve":
            gate = request.get("gate") or state.get("gate")
            fleet_graph.approve(state, gate, request.get("note"))
            if gate == "plan":
                self.fleet_prepare(state)
            self.fleet_advance(state, f"{gate} approved")
            return {"ok": True, "run": state}
        if cmd == "fleet-send-back":
            nid = fleet_graph.send_back(state, request.get("note"))
            self.fleet_enqueue(state, nid)
            self.fleet_advance(state, "plan sent back", queued=[nid])
            return {"ok": True, "run": state}
        if cmd in ("fleet-drop", "fleet-shape"):
            if cmd == "fleet-drop":
                fleet_graph.drop(state, request.get("slice"))
            else:
                fleet_graph.choose_shape(state, request.get("shape"))
            self.fleet_saved(state, f"{cmd[6:]} at the plan gate")
            return {"ok": True, "run": state}
        if cmd == "fleet-accept-scope":
            fleet_graph.accept_scope(state, request.get("node"), request.get("reason"))
            self.fleet_advance(state, f"{request.get('node')}'s extra files accepted")
            return {"ok": True, "run": state}
        if cmd == "fleet-scope-back":
            nid = fleet_graph.scope_send_back(state, request.get("node"))
            self.fleet_enqueue(state, nid)
            self.fleet_advance(state, f"{request.get('node')} sent back for its extra files", queued=[nid])
            return {"ok": True, "run": state}
        if cmd == "fleet-retry":
            nid = fleet_graph.retry(state, request.get("note"))
            if nid:
                self.fleet_enqueue(state, nid)
            self.fleet_advance(state, f"retried: {nid or 'going on'}", queued=[nid] if nid else [])
            return {"ok": True, "run": state, "node": nid}
        if cmd in ("fleet-pause", "fleet-resume"):
            (fleet_graph.pause if cmd == "fleet-pause" else fleet_graph.resume)(state)
            self.fleet_advance(state, cmd[6:] + "d")
            return {"ok": True, "run": state}
        if cmd == "fleet-limits":
            fleet_graph.set_limits(state, request.get("budget"), request.get("max_steps"))
            self.fleet_advance(state, "limits changed")
            return {"ok": True, "run": state}
        if cmd == "fleet-close":
            if request.get("check"):
                return {"ok": True, "run": state, "checks": fleet_close.check(state)}
            if state.get("closed"):
                raise fleet.RunError(f"run {state['id']} is already closed")
            checks, notes = fleet_close.close(state)
            self.fleet_saved(state, "closed")
            return {"ok": True, "run": state, "checks": checks, "notes": notes}
        if cmd == "fleet-cancel":
            waiting = {n["id"] for n in fleet_graph.cancel(state)}
            for item in [t for t in self.queue.tasks if t.get("fleet") == state["id"] and t.get("node") in waiting]:
                self.queue.tasks.remove(item)
            self.queue.save()
            self.publish_queue()
            self.fleet_saved(state, "cancelled")
            return {"ok": True, "run": state}
        return {"ok": False, "error": f"unknown command: {cmd}"}

    def fleet_prepare(self, state):
        """Once the plan is approved: the run's own worktree and branch, which
        the builders of a single loop and the integrator work in."""
        if not state.get("repo") or state.get("worktree"):
            return
        try:
            record = worktrees.create(state["folder"], state["goal"], None, name=f"fleet-{state['id']}",
                                      extra={"fleet": state["id"]})
        except worktrees.WorktreeError as e:
            fleet.hold(state, f"could not make the run's worktree: {e}")
            return
        state["worktree"] = {k: record[k] for k in ("path", "workdir", "branch", "base")}
        state["branch"] = record["branch"]

    def fleet_workdir(self, state, n):
        """Where a node works, and the worktree it belongs to (or None). A
        diamond's slice gets a worktree of its own, from the run's branch, or
        from the slice it depends on."""
        own = state.get("worktree")
        if n["role"] in ("scout", "architect"):
            return state["folder"], None
        if state["shape"] != "diamond" or not n.get("slice"):
            return (own["workdir"], own["path"]) if own else (state["folder"], None)
        slice_id = fleet.root_slice(state, n["slice"])  # a split slice works in the worktree of the one it came from
        record = state["slice_worktrees"].get(slice_id)
        if record is None:
            depends = fleet_graph.depends_on(fleet.live_plan(state), slice_id)
            start = state["slice_worktrees"][depends[0]]["branch"] if depends else own["branch"]
            made = worktrees.create(state["folder"], state["goal"], None, name=f"fleet-{state['id']}-{slice_id}",
                                    start=start, extra={"fleet": state["id"], "slice": slice_id,
                                                        "base_branch": own["branch"]})
            record = state["slice_worktrees"][slice_id] = {k: made[k] for k in ("path", "workdir", "branch", "base")}
        return record["workdir"], record["path"]

    def fleet_enqueue(self, state, nid):
        """Queue a node's task, so the parallel limit, the budget and usage
        limits apply to it as to any task. A node that cannot be queued holds
        the run."""
        n = fleet.node(state, nid)
        role_name = state["template"]["roles"][n["role"]]
        try:
            workdir, worktree = self.fleet_workdir(state, n)
            role = roles.get(role_name, workdir)
            task = fleet.task_for(state, nid)
            item = self.queue.add({"task": task, "cwd": workdir, "worktree": False, "worktree_path": worktree,
                                   "extra": [], "role": role_name, "agent": role.agent, "fleet": state["id"],
                                   "node": nid, "path": state.get("path")})
        except (worktrees.WorktreeError, roles.RoleError, fleet.RunError, taskqueue.QueueError) as e:
            fleet.hold(state, f"{nid} cannot start: {e}", by=nid)
            return
        n.update(runs_as=role_name, agent=role.agent, queued=item["id"])
        fleet.activity(state["id"], {"event": "queued", "node": nid, "task": item["id"]})

    def fleet_advance(self, state, what, queued=()):
        """Move a run on (fleet_graph.advance), queue the nodes it is ready
        for, save it, and let the queue start them (and any `queued` already)."""
        try:
            ready = fleet_graph.advance(state)
        except fleet.RunError as e:
            fleet.hold(state, str(e))
            ready = []
        for nid in ready:
            self.fleet_enqueue(state, nid)
        self.fleet_saved(state, what)
        if ready or queued:
            self.publish_queue()
            self.dispatch()

    def fleet_node_launched(self, item, session_id, agent, error=None, cancelled=False):
        """A fleet node's queued task started (the node is running), or could
        not start or was cancelled (the run holds, with the reason)."""
        if not item.get("fleet") or not item.get("node"):
            return
        try:
            state = fleet.load(item["fleet"])
            n = fleet.node(state, item["node"])
        except fleet.RunError as e:
            event(logging.WARNING, "fleet node not found", run=item["fleet"], node=item["node"], error=str(e))
            return
        if n["status"] not in ("waiting", "held"):
            return
        if error:
            if cancelled:
                n["status"] = "cancelled"
            fleet.hold(state, f"{n['id']}: {error}", by=n["id"])
            self.fleet_saved(state, f"{n['id']}: {error}")
            return
        fleet.node_started(state, n["id"], session_id, agent)
        self.fleet_saved(state, f"{n['id']} started")

    def fleet_saved(self, state, what):
        try:
            fleet.save(state)
        except OSError as e:
            event(logging.WARNING, "fleet run not saved", run=state["id"], error=str(e))
            return
        event(logging.INFO, "fleet run", run=state["id"], what=what)
        self.fleet_summary()
        self.publish({"event": "fleet", "run": state["id"], "status": state["status"], "reason": state.get("reason"),
                      "state": state})
        seen = (state["status"], state.get("gate"), state.get("reason"))
        if self.fleet_seen.get(state["id"]) != seen:
            self.fleet_seen[state["id"]] = seen
            if state["status"] in ("at-gate", "held", "done"):
                self.fleet_tell(state)

    def fleet_summary(self):
        try:
            fleet.write_summary()
        except OSError as e:
            event(logging.WARNING, "fleets.json not written", error=str(e))

    def fleet_tell(self, state):
        """A run reached a gate, held, or finished: a notification here (Open
        shows it in omafleet), and a push when you are away."""
        if self.notifier:
            title, body = remote.fleet_message(state, "summary")
            run_id = state["id"]

            async def open_app():
                command = os.environ.get("OMAORCHESTRA_BIN") or shutil.which("omaorchestra") or "omaorchestra"
                try:
                    await asyncio.create_subprocess_exec(command, "app", "--fleet", run_id,
                                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except OSError as e:
                    event(logging.WARNING, "could not open the app", error=str(e))
            self.notifier.offer(title, body, "Open", open_app,
                                urgency="critical" if state["status"] == "held" else "normal")
        if self.pusher:
            self.pusher.fleet(state)

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
            item = self.enqueue(dict(request.get("item") or {}), paused=bool(request.get("paused")))
        elif cmd == "queue-cancel":
            item = q.remove(request["id"])
            event(logging.INFO, "task cancelled", task=item["id"])
            self.fleet_node_launched(item, None, None, error="its queued task was cancelled", cancelled=True)
            for child in q.children(parent_id=item["id"]):
                child.update(state="held", error="the task before it was cancelled")
            q.save()
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

    def enqueue(self, fields, paused=False):
        """Add a task to the queue, linked into its chain (`after`); what
        `queue add` does, and what a schedule does when its time comes."""
        parent_session = self.link_chain(fields)
        item = self.queue.add(fields, paused=paused)
        event(logging.INFO, "task queued", task=item["id"], cwd=item["cwd"],
              after=item.get("after") or item.get("parent_session"), schedule=item.get("schedule"))
        if parent_session and parent_session.get("status") == "idle":
            self.step_ended(parent_session, finished=True)  # what it follows is already done
        return item

    # ------------------------------------------------------------- schedules

    def schedule_status(self, s):
        """Where a schedule's last run stands, in a few words."""
        queued = [t for t in self.queue.tasks if t["id"] in s["tasks"]]
        bad = next((t for t in queued if t["state"] in ("failed", "held")), None)
        if bad:
            return f"{bad['state']}: {bad.get('error') or ''}".rstrip(": ")
        if queued:
            return "queued"
        live = [self.registry.sessions.get(sid) for sid in s["sessions"]]
        statuses = {x.get("status") for x in live if x}
        if "needs-input" in statuses:
            return "waiting for you"
        if "working" in statuses:
            return "running"
        return s.get("outcome") or s.get("last_note") or ""

    def schedule_running(self, s):
        """True while its last run is still queued or at work."""
        if any(t["id"] in s["tasks"] for t in self.queue.tasks):
            return True
        return any((self.registry.sessions.get(sid) or {}).get("status") in ("working", "needs-input")
                   for sid in s["sessions"])

    def schedules_snapshot(self):
        return {"enabled": self.settings["schedules"]["enabled"],
                "schedules": [{**s, "title": schedules.Schedules.title(s), "whenText": schedules.describe(s["when"]),
                               "status": self.schedule_status(s), "cwd": s["items"][0].get("cwd")}
                              for s in self.schedules.items]}

    def publish_schedules(self):
        self.publish({"event": "schedules", "schedules": self.schedules_snapshot()})

    def fire_schedule(self, s, now=None, by_hand=False):
        """Queue a schedule's tasks now (each after the one before). Returns
        the queued task ids; on a refusal, records it and returns []."""
        now = time.time() if now is None else now
        ids = []
        try:
            for fields in s["items"]:
                fields = {**fields, "schedule": s["id"]}
                if ids:
                    fields["after"] = ids[-1]
                ids.append(self.enqueue(fields)["id"])
        except taskqueue.QueueError as e:
            event(logging.WARNING, "schedule could not queue", schedule=s["id"], error=str(e))
            if by_hand:
                raise
            self.schedules.ran(s, now, note=f"could not queue: {e}")
            if self.pusher:
                self.pusher.task_failed({"task": s["task"], "cwd": s["items"][0].get("cwd")}, str(e))
            return []
        if by_hand:
            s["tasks"], s["sessions"], s["outcome"], s["last"], s["last_note"] = ids, [], None, now, "queued by hand"
            self.schedules.save()
        else:
            self.schedules.ran(s, now, task_ids=ids, note="queued")
        event(logging.INFO, "schedule queued", schedule=s["id"], tasks=len(ids), by_hand=by_hand)
        return ids

    def run_schedules(self, now=None):
        """Queue every schedule whose time has come; a run missed while the
        daemon was down runs once, and one whose last run is still going
        skips its time."""
        if not self.settings["schedules"]["enabled"]:
            return
        now = time.time() if now is None else now
        due = self.schedules.due(now)
        for s in due:
            if self.schedule_running(s):
                event(logging.INFO, "schedule skipped", schedule=s["id"], reason="its last run is still going")
                self.schedules.ran(s, now, note="skipped: the last run was still going")
            else:
                self.fire_schedule(s, now)
        if due:
            self.publish_schedules()
            self.publish_queue()
            self.dispatch()

    def schedule_task_started(self, item, session_id):
        s = self.schedules.by_task(item["id"]) if item.get("schedule") else None
        if s:
            s["sessions"].append(session_id)
            self.schedules.save()
            self.publish_schedules()

    def schedule_session_changed(self, previous, session, reason=None):
        """Keep a schedule's last outcome: finished once its agent goes idle
        after working, or how it ended."""
        sid = (previous or session or {}).get("id")
        s = self.schedules.by_session(sid) if sid else None
        if not s:
            return
        if session is None:
            s["outcome"] = history.outcome(previous, reason)
        elif (previous or {}).get("status") == "working" and session.get("status") == "idle":
            s["outcome"] = "finished"
        else:
            return
        self.schedules.save()
        self.publish_schedules()

    def handle_schedule(self, cmd, request):
        if cmd == "schedule-list":
            return {"ok": True, "schedules": self.schedules_snapshot()}
        if cmd == "schedule-add":
            items = request.get("items") or []
            for fields in items:  # what queue-add would refuse, refused now
                if not fields.get("cwd") or not os.path.isdir(fields["cwd"]):
                    raise schedules.ScheduleError(f"{fields.get('cwd')} is not a directory")
            s = self.schedules.add(request.get("when"), [dict(i) for i in items], name=request.get("name"),
                                   task=request.get("task"))
            event(logging.INFO, "schedule added", schedule=s["id"], when=schedules.describe(s["when"]))
        elif cmd == "schedule-remove":
            s = self.schedules.remove(request["id"])
            event(logging.INFO, "schedule removed", schedule=s["id"])
        elif cmd in ("schedule-pause", "schedule-resume"):
            s = self.schedules.set_paused(request["id"], cmd == "schedule-pause")
        elif cmd == "schedule-run":
            s = self.schedules.find(request["id"])
            try:
                self.fire_schedule(s, by_hand=True)
            except taskqueue.QueueError as e:
                raise schedules.ScheduleError(str(e)) from None
            self.publish_queue()
            self.dispatch()
        else:
            return {"ok": False, "error": f"unknown command: {cmd}"}
        self.publish_schedules()
        return {"ok": True, "schedule": s, "schedules": self.schedules_snapshot()}

    def start_schedule_watch(self, interval=SCHEDULE_CHECK_INTERVAL):
        async def watch():
            while True:
                await asyncio.sleep(interval)
                self.run_schedules()
        self.schedule_watch = asyncio.get_running_loop().create_task(watch())

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
        self.away.update(push=new["remote"]["push"], answers=new["remote"]["answer_prompts"])
        if new["history"] != old["history"]:
            self._history_tended = 0
            self.tend_history()
        self.sync_away()
        if self.pruner and new["daemon"]["prune_interval"] != old["daemon"]["prune_interval"]:
            self.start_pruner()
        self.publish_queue()
        self.publish_schedules()
        self.run_schedules()  # schedules may have been turned back on
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
        self.fleet_activity(previous, session, reason)
        if reason != "adopted":
            self.schedule_session_changed(previous, session, reason)
        if session is None and previous and reason != "adopted":
            self.record_history(previous, reason)
            outcome = history.outcome(previous, reason)
            self.step_ended(previous, outcome == "finished", outcome)
            self.fleet_node_ended(previous, outcome == "finished", outcome)
        elif previous and session and previous.get("status") == "working" and session.get("status") == "idle":
            self.step_ended(session, finished=True)  # idle after working: this step is done
            self.fleet_node_ended(session, finished=True)
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
                    "schedules": self.schedules_snapshot(),
                    "away": self.away.state(), "approvals": self.approvals.public(),
                    "fleets": fleet.current(fleet.runs())}
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
        given = {k: request[k] for k in ("model", "branch", "title", "task", "launching", "worktree", "provider",
                                         *CARRIED)
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
            status = request["status"]
            if request.get("start") and previous == "working":
                # Launched with its prompt, so already at work: its SessionStart
                # hook comes after and must not read as a finished turn.
                status = "working"
            session = self.registry.update(
                request["session_id"], request.get("agent", "unknown"), status,
                cwd=request.get("cwd"), message=request.get("message"),
                pid=request.get("pid"), pid_start=request.get("pid_start"),
                transcript_path=request.get("transcript_path"),
                **self.transcript_facts(before, request),
            )
            if before is None and "git_start" not in session:
                # Where its folder stood, so history can tell which commits it made.
                session["git_start"] = self.git_head(session.get("cwd"))
                self.registry.save()
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
        if cmd.startswith("fleet-"):
            try:
                return self.handle_fleet(cmd, request)
            except (fleet.RunError, fleet_graph.FleetError) as e:
                return {"ok": False, "error": str(e)}
        if cmd.startswith("queue-"):
            try:
                return self.handle_queue(cmd, request)
            except taskqueue.QueueError as e:
                return {"ok": False, "error": str(e)}
        if cmd.startswith("schedule-"):
            try:
                return self.handle_schedule(cmd, request)
            except schedules.ScheduleError as e:
                return {"ok": False, "error": str(e)}
        if cmd == "handoff":
            try:
                return {"ok": True, **self.handoff(request)}
            except (ValueError, launch.LaunchError) as e:
                return {"ok": False, "error": str(e)}
        if cmd == "away":
            return self.handle_away(request)
        if cmd == "stopping":
            session = self.registry.sessions.get(request.get("session_id"))
            if session:
                session["stopping"] = True
                self.registry.save()
            return {"ok": True, "known": session is not None}
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
                    if isinstance(request, dict) and request.get("cmd") in FLEET_GATE_COMMANDS:
                        try:
                            response = self.handle_fleet(request["cmd"], request, self.peer_pid(writer))
                        except (fleet.RunError, fleet_graph.FleetError) as e:
                            response = {"ok": False, "error": str(e)}
                        writer.write(json.dumps(response).encode() + b"\n")
                        await writer.drain()
                        continue
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
        daemon.tend_history()
        daemon.offer_handoffs()
        daemon.check_limits()
        daemon.fleet_watch()
        # A queue waiting on a usage limit gets another look (limits reset).
        if daemon.queue.next_pending():
            daemon.dispatch()


async def serve(sock_path, registry, daemon=None):
    daemon = daemon or Daemon(registry)
    claim_socket(sock_path)
    # Write the registry up front so file watchers (the bar widget) see it
    # exist from the moment the daemon runs; fleets.json likewise.
    registry.save()
    daemon.fleet_summary()
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
    registry = Registry(paths.private_state_dir() / "sessions.json")

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
        daemon.start_schedule_watch()
        daemon.tend_history()
        daemon.away.save()  # for the bar widget, as with the registry
        daemon.sync_away()
        daemon.run_schedules()  # times that came round while the daemon was down
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
            daemon.schedule_watch.cancel()
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
