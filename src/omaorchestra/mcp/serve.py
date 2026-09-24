"""omaorchestra as an MCP server (`omaorchestra mcp serve`, over stdio).

Lets an agent see the other agents and the queue, and suggest work.
Everything is read-only except queue_task, and that only adds the task
paused and notifies the user: an agent can propose work, never start it.
"""

import json
import subprocess
import sys

from .. import __version__, client, launch, transcript

SUPPORTED = ("2025-06-18", "2025-03-26", "2024-11-05")

TOOLS = [
    {"name": "list_sessions",
     "description": "The agent sessions omaorchestra tracks on this machine: id, project folder, status "
                    "(working, waiting for the user, idle), title and model.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "get_session",
     "description": "One session in detail, with its latest prompts, replies and tool calls. "
                    "Accepts a session id or its first characters.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"],
                     "additionalProperties": False}},
    {"name": "list_queue",
     "description": "Tasks waiting for a free agent slot, in order, and whether the queue is held.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "queue_task",
     "description": "Suggest a task for another agent. It is added to the queue PAUSED and the user is "
                    "notified; it only runs if the user resumes it. Use handoff_from to pass along the "
                    "latest activity of a session.",
     "inputSchema": {"type": "object", "properties": {
         "task": {"type": "string", "description": "What the agent should do, self-contained."},
         "folder": {"type": "string", "description": "Absolute path to work in."},
         "model": {"type": "string"}, "provider": {"type": "string"},
         "handoff_from": {"type": "string", "description": "Session id whose recent activity to include."}},
         "required": ["task", "folder"], "additionalProperties": False}},
]


def _sessions():
    return client.request({"cmd": "list"})["sessions"]


def _brief(s):
    return {k: s.get(k) for k in ("id", "cwd", "status", "title", "model", "provider", "message")}


def _find(key):
    matches = [s for s in _sessions() if s["id"].startswith(key)]
    if len(matches) != 1:
        raise ValueError(f"{len(matches)} sessions match {key}")
    return matches[0]


def call(name, arguments):
    if name == "list_sessions":
        return [_brief(s) for s in _sessions()]
    if name == "get_session":
        s = _find(arguments["id"])
        recent = transcript.activity(s["transcript_path"], limit=15) if s.get("transcript_path") else []
        return {**_brief(s), "branch": s.get("branch"), "worktree": s.get("worktree"), "recent": recent}
    if name == "list_queue":
        q = client.request({"cmd": "queue-list"})["queue"]
        return {"held": q["held"], "busy": q["busy"], "limit": q["limit"],
                "tasks": [{k: t.get(k) for k in ("id", "task", "cwd", "state", "model", "provider")} for t in q["tasks"]]}
    if name == "queue_task":
        task = arguments["task"]
        if arguments.get("handoff_from"):
            s = _find(arguments["handoff_from"])
            recent = transcript.activity(s["transcript_path"], limit=10) if s.get("transcript_path") else []
            lines = "\n".join(f"- {i['kind']}: {i['text']}" for i in recent)
            task += f"\n\nHanded off from session {s['id'][:8]} in {s.get('cwd')}. Its latest activity:\n{lines}"
        item = {"task": task, "cwd": arguments["folder"], "model": arguments.get("model"),
                "provider": arguments.get("provider"), "worktree": None, "extra": [], **launch.agent_environment()}
        response = client.request({"cmd": "queue-add", "item": item, "paused": True})
        if not response.get("ok"):
            raise ValueError(response.get("error"))
        try:
            subprocess.run(["notify-send", "--app-name=omaorchestra", "--urgency=normal",
                            "An agent suggested a task", launch.short(arguments["task"], 120)
                            + "\nIt is paused in the queue until you resume it."], capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass
        return {"queued": response["item"]["id"], "state": "paused",
                "note": "Added paused. It runs only if the user resumes it in omaorchestra."}
    raise KeyError(name)


def handle(message):
    """The response to one JSON-RPC message, or None for notifications."""
    method, request_id = message.get("method"), message.get("id")
    if request_id is None:
        return None
    try:
        if method == "initialize":
            asked = (message.get("params") or {}).get("protocolVersion")
            result = {"protocolVersion": asked if asked in SUPPORTED else SUPPORTED[0],
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "omaorchestra", "version": __version__}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = message.get("params") or {}
            try:
                value = call(params.get("name"), params.get("arguments") or {})
                result = {"content": [{"type": "text", "text": json.dumps(value, indent=2)}], "isError": False}
            except KeyError as e:
                return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": f"unknown tool {e}"}}
            except (ValueError, client.DaemonUnavailable) as e:
                result = {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True}
        else:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"unknown method {method}"}}
    except Exception as e:  # never let one bad request end the session
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": str(e)}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve(stdin=None, stdout=None):
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        response = handle(message) if isinstance(message, dict) else None
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
