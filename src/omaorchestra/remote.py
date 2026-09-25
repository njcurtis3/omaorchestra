"""Push notifications to a phone, through ntfy (ntfy.sh or a self-hosted server).

Outbound only: one HTTPS POST per notification, from the standard library.
Nothing listens. The topic (which is the address, and on ntfy.sh the only
thing guarding it) and an optional access token live in the system keyring,
never in config.toml or the log.

What leaves the machine depends on [remote] content:
  minimal  the project folder's name and what happened (the default)
  summary  adds the task's title
  full     adds the agent's question, which can quote commands and code
"""

import asyncio
import json
import logging
import secrets
import urllib.error
import urllib.request

from . import config, keys, notify, usage
from .log import event

TIMEOUT = 10

# event -> (ntfy priority 1-5, ntfy tag, which shows as an emoji)
STYLE = {
    "needs-you": (4, "bell"),
    "finished": (3, "white_check_mark"),
    "failed": (4, "x"),
    "usage-limit": (3, "hourglass"),
    "queue-blocked": (3, "pause_button"),
}


class RemoteError(Exception):
    pass


def new_topic():
    """A topic nobody will guess: on ntfy.sh anyone who knows it can read it."""
    return "omaorchestra-" + secrets.token_urlsafe(18).replace("_", "").replace("-", "")[:20].lower()


def _task_title(item):
    text = (item.get("title") or item.get("task") or "").strip()
    return text.splitlines()[0][:120] if text else ""


def session_message(kind, previous, session, level, now=None):
    """(title, body) for a needs-you or finished push, at a content level."""
    name = notify.project(session)
    if kind == "needs-you":
        title, body = f"{name} needs you", "Waiting for your input"
        if level == "full" and session.get("message"):
            body = session["message"]
    else:
        title, body = notify.content("finished", previous, session, now=now)[:2]
    if level in ("summary", "full"):
        task = _task_title(session)
        if task:
            body = f"{task}\n{body}"
    return title, body


def failed_message(item, error, level):
    """A queued task that could not start."""
    name = notify.project({"cwd": item.get("cwd")})
    body = "A queued task could not start"
    if level in ("summary", "full"):
        task = _task_title(item)
        body = f"{task}\n{body}" if task else body
    if level == "full" and error:
        body += f": {error}"
    return f"{name}: task failed to start", body


def limit_message(block):
    return f"{block['name']} reached its usage limit", usage.describe(block)


def blocked_message(block):
    return "Queue is waiting", (block.get("text") or usage.describe(block)).capitalize()


def payload(topic, kind, title, body):
    priority, tag = STYLE[kind]
    return {"topic": topic, "title": title, "message": body, "priority": priority, "tags": [tag]}


def post(server, data, token=None, opener=urllib.request.urlopen):
    """Publish one message (ntfy's JSON form: POST to the server root)."""
    if token and config.plain_http(server):
        raise RemoteError(f"refusing to send the access token to {server} over plain http; use https")
    request = urllib.request.Request(server.rstrip("/") + "/", data=json.dumps(data).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    if token:
        request.add_header("Authorization", f"Bearer {token.strip()}")
    try:
        with opener(request, timeout=TIMEOUT) as response:
            response.read()
    except urllib.error.HTTPError as e:
        hint = {401: "the server wants a token (`omaorchestra remote token`)",
                403: "the token may not publish to this topic",
                429: "too many messages; the server is rate limiting"}.get(e.code, e.reason)
        raise RemoteError(f"{server} answered {e.code}: {hint}") from None
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", None) or e
        raise RemoteError(f"could not reach {server}: {reason}") from None


def send(settings, kind, title, body, lookup=keys.lookup_remote, opener=urllib.request.urlopen):
    """Push one notification now; raises RemoteError. Ignores `push` and
    `events` (for `remote test`)."""
    topic = (lookup("topic") or "").strip()
    if not topic:
        raise RemoteError("no topic yet; set one with `omaorchestra remote topic --new`")
    post(settings["server"], payload(topic, kind, title, body), token=lookup("token"), opener=opener)


class Pusher:
    """Sends pushes from the daemon without ever holding it up or failing it.

    `send` is injectable for tests; it is called as send(settings, kind,
    title, body) in a worker thread. `away`, when given, is awaited before
    each push: False (you are at the desk) drops it.
    """

    def __init__(self, settings, notifications, send=send, away=None):
        self.settings = settings  # the [remote] section
        self.notifications = notifications  # [notifications], for finished_after
        self.send = send
        self.away = away
        self.tasks = set()
        self.failing = False  # log the first failure of a run, not every one

    def wants(self, kind):
        return self.settings["push"] and kind in self.settings["events"]

    def changed(self, previous, session):
        """Called by the daemon after every session update or removal."""
        kind = notify.decide(previous, session, {"waiting": True,
                                                 "finished_after": self.notifications["finished_after"]})
        kind = {"waiting": "needs-you", "finished": "finished"}.get(kind)
        if kind and self.wants(kind):
            self.push(kind, *session_message(kind, previous, session, self.settings["content"]))

    def task_failed(self, item, error):
        if self.wants("failed"):
            self.push("failed", *failed_message(item, error, self.settings["content"]))

    def limit_reached(self, block):
        if self.wants("usage-limit"):
            self.push("usage-limit", *limit_message(block))

    def queue_blocked(self, block):
        if self.wants("queue-blocked"):
            self.push("queue-blocked", *blocked_message(block))

    def push(self, kind, title, body):
        settings = dict(self.settings)

        async def run():
            if self.away is not None and not await self.away():
                event(logging.DEBUG, "push held", kind=kind, reason="at the desk")
                return
            try:
                await asyncio.to_thread(self.send, settings, kind, title, body)
            except RemoteError as e:
                if not self.failing:
                    event(logging.WARNING, "push failed", kind=kind, error=str(e))
                self.failing = True
                return
            if self.failing:
                event(logging.INFO, "push working again")
            self.failing = False
            event(logging.INFO, "pushed", kind=kind)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no event loop (a synchronous test of the daemon)
        task = loop.create_task(run())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
