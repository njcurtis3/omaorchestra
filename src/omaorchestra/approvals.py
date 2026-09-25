"""Answering an agent's permission prompts while you are away.

Claude Code runs a PermissionRequest hook when it shows a permission prompt,
and takes the hook's answer if one comes: {"behavior": "allow"} or
{"behavior": "deny", "message": ...}. The prompt stays on screen meanwhile
and whichever answers first wins, so at the desk nothing changes.

In away mode (away.py) omaorchestra's hook asks the daemon and waits: the
request shows in `omaorchestra top` and `omaorchestra approvals`, and
`approve` / `deny` answers it. The wait ends when you answer, when the
prompt is answered at the terminal (the session moves on), when the agent
gives up on the hook, or after [remote] answer_wait seconds; then the hook
says nothing and the terminal prompt is all there is.

One answer per request, never a standing rule: an allow carries no
updatedPermissions, so the next request asks again. Every remote request and
how it ended is recorded in approvals.jsonl, with where the answer came from.

This is the one place a hook waits. Everywhere else hooks report and return
at once.
"""

import json
import os
import secrets
import time

from . import client, permissions
from .app import present

# Claude Code's timeout for the PermissionRequest hook (settings.json). The
# prompt is not held up meanwhile, so a long timeout costs nothing; the real
# limit is [remote] answer_wait, read when the hook runs.
HOOK_TIMEOUT = 3660
DENY_MESSAGE = "The user denied this from omaorchestra, away from the desk."
SUMMARY_LIMIT = 300


def describe(tool, tool_input, mcp_server=None):
    """One line saying what the agent wants to do: "Bash: npm test"."""
    tool = tool or "a tool"
    data = tool_input if isinstance(tool_input, dict) else {}
    detail = None
    for key in ("command", "file_path", "notebook_path", "url", "pattern", "path", "query", "description"):
        if isinstance(data.get(key), str) and data[key].strip():
            detail = data[key]
            break
    if detail is None and data:
        detail = json.dumps(data, ensure_ascii=False, sort_keys=True)
    name = tool
    if mcp_server and tool.startswith("mcp__"):
        name = f"{mcp_server}: {tool.split('__')[-1]}"
    text = " ".join(f"{name}: {detail}".split()) if detail else name
    return text if len(text) <= SUMMARY_LIMIT else text[:SUMMARY_LIMIT - 1] + "…"


def where_from(env=os.environ):
    """How an answer reached the machine: "over SSH from 100.x.y.z", or ""."""
    connection = env.get("SSH_CONNECTION") or env.get("SSH_CLIENT") or ""
    return f"over SSH from {connection.split()[0]}" if connection.split() else ""


def hook_output(decision):
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}


def ask(event, settings, request=client.request):
    """From the hook: ask the daemon, and wait for an answer if you are away.
    Returns what the hook should print, or None to leave it to the terminal."""
    if not settings["answer_prompts"]:
        return None
    payload = {"cmd": "approval-ask", "session_id": event.get("session_id"), "tool": event.get("tool_name"),
               "summary": describe(event.get("tool_name"), event.get("tool_input"), event.get("mcp_server")),
               "cwd": event.get("cwd")}
    try:
        response = request(payload, timeout=settings["answer_wait"] + 15)
    except (client.DaemonUnavailable, ValueError):
        return None
    decision = response.get("decision") if response.get("ok") else None
    if not decision or decision.get("behavior") not in ("allow", "deny"):
        return None
    clean = {"behavior": decision["behavior"]}  # never updatedPermissions: one answer, not a rule
    if clean["behavior"] == "deny":
        clean["message"] = decision.get("message") or DENY_MESSAGE
    return hook_output(clean)


class Pending:
    """Requests waiting for a remote answer, in the daemon."""

    def __init__(self):
        self.items = {}  # id -> request, with its future

    def add(self, request, future, now=None):
        rid = secrets.token_hex(3)
        while rid in self.items:
            rid = secrets.token_hex(3)
        self.items[rid] = {"id": rid, "session_id": request.get("session_id"), "tool": request.get("tool") or "",
                           "summary": request.get("summary") or request.get("tool") or "a tool",
                           "cwd": request.get("cwd") or "", "asked": time.time() if now is None else now,
                           "future": future}
        return self.items[rid]

    def public(self):
        """For clients: oldest first, without the future."""
        return [{k: v for k, v in item.items() if k != "future"}
                for item in sorted(self.items.values(), key=lambda i: i["asked"])]

    def find(self, rid):
        rid = (rid or "").strip().lower()
        matches = [item for key, item in self.items.items() if rid and key.startswith(rid)]
        if not matches:
            raise KeyError(f"no request {rid} is waiting (it may have been answered at the terminal already)")
        if len(matches) > 1:
            raise KeyError(f"{rid} matches {len(matches)} requests; give more of the id")
        return matches[0]

    def answer(self, rid, behavior, message=None, source="cli"):
        if behavior not in ("allow", "deny"):
            raise KeyError(f"unknown answer {behavior!r} (allow or deny)")
        item = self.find(rid)
        if item["future"].done():
            raise KeyError(f"request {item['id']} was already answered")
        item["future"].set_result({"behavior": behavior, "message": message, "source": source})
        return item

    def settle_session(self, session_id, reason):
        """The session moved on (answered at the terminal, or ended): its
        requests are no longer ours to answer."""
        for item in self.items.values():
            if item["session_id"] == session_id and not item["future"].done():
                item["future"].set_result({"behavior": None, "reason": reason})

    def remove(self, rid):
        return self.items.pop(rid, None)


def record(item, outcome, source=""):
    """Into approvals.jsonl, beside the asked/answered entries."""
    permissions.log({"kind": "remote", "session": item["session_id"], "id": item["id"],
                     "project": item["cwd"], "tool": item["tool"], "summary": item["summary"],
                     "outcome": outcome, "source": source})


def outcome_text(outcome, source):
    """"allowed from top over SSH from 100.x.y.z"."""
    return f"{outcome} from {source}" if source else outcome


def project(item):
    return present.project(item.get("cwd"))
