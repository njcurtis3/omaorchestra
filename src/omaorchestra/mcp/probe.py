"""Check that an MCP server starts and answers: the MCP handshake, then the
list of its tools.

stdio servers are started as an agent would start them, but in an empty
temporary folder, with a minimal environment (PATH, HOME, LANG plus the
server's own variables) and a time limit, and are stopped afterwards.
HTTP servers are spoken to over MCP's streamable HTTP transport.
"""

import json
import os
import select
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

from .. import __version__

PROTOCOL = "2025-06-18"
TIMEOUT = 20


def _initialize(request_id=1):
    return {"jsonrpc": "2.0", "id": request_id, "method": "initialize",
            "params": {"protocolVersion": PROTOCOL, "capabilities": {},
                       "clientInfo": {"name": "omaorchestra", "version": __version__}}}


def _result(ok, started, **kw):
    return {"ok": ok, "seconds": round(time.monotonic() - started, 2), "tools": [], "server": None,
            "version": None, "protocol": None, "error": None, **kw}


def _summarise(init, tools):
    info = (init or {}).get("serverInfo") or {}
    return {"server": info.get("name"), "version": info.get("version"),
            "protocol": (init or {}).get("protocolVersion"),
            "tools": [{"name": t.get("name"), "description": (t.get("description") or "").strip()[:200]}
                      for t in tools if isinstance(t, dict)]}


# ---------------------------------------------------------------- stdio

class _Stdio:
    def __init__(self, proc, deadline):
        self.proc, self.deadline, self.buffer = proc, deadline, b""

    def send(self, message):
        self.proc.stdin.write(json.dumps(message).encode() + b"\n")
        self.proc.stdin.flush()

    def receive(self, request_id):
        """The response to `request_id`, skipping notifications and logs."""
        while True:
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                try:
                    message = json.loads(line)
                except ValueError:
                    continue  # stray output; servers should not print to stdout, but some do
                if isinstance(message, dict) and message.get("id") == request_id:
                    if "error" in message:
                        raise RuntimeError(message["error"].get("message") or "error")
                    return message.get("result") or {}
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"no answer within {TIMEOUT} seconds")
            ready, _, _ = select.select([self.proc.stdout], [], [], remaining)
            if not ready:
                continue
            chunk = os.read(self.proc.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError("the server exited")
            self.buffer += chunk


def stdio(command, args=(), env=None, timeout=TIMEOUT):
    started = time.monotonic()
    if not shutil.which(command):
        return _result(False, started, error=f"{command} is not installed or not on PATH")
    base_env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS")
                if k in os.environ}
    with tempfile.TemporaryDirectory(prefix="omaorchestra-mcp-probe-") as workdir:
        with tempfile.TemporaryFile() as errors:
            try:
                proc = subprocess.Popen([command, *args], cwd=workdir, env={**base_env, **(env or {})},
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
                                        start_new_session=True)
            except OSError as e:
                return _result(False, started, error=f"cannot start {command}: {e.strerror}")
            try:
                session = _Stdio(proc, started + timeout)
                session.send(_initialize())
                init = session.receive(1)
                session.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
                tools, cursor, request_id = [], None, 2
                while True:
                    params = {"cursor": cursor} if cursor else {}
                    session.send({"jsonrpc": "2.0", "id": request_id, "method": "tools/list", "params": params})
                    page = session.receive(request_id)
                    tools += page.get("tools") or []
                    cursor, request_id = page.get("nextCursor"), request_id + 1
                    if not cursor:
                        break
                return _result(True, started, **_summarise(init, tools))
            except (RuntimeError, TimeoutError, OSError, ValueError) as e:
                errors.seek(0)
                tail = errors.read().decode(errors="replace").strip().splitlines()[-3:]
                return _result(False, started, error=str(e) + (": " + " | ".join(tail) if tail else ""))
            finally:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    proc.wait(timeout=3)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass


# ---------------------------------------------------------------- HTTP

def _post(url, message, headers, session_id, timeout):
    request_headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                       "MCP-Protocol-Version": PROTOCOL, "User-Agent": "omaorchestra", **headers}
    if session_id:
        request_headers["Mcp-Session-Id"] = session_id
    request = urllib.request.Request(url, data=json.dumps(message).encode(), headers=request_headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode(errors="replace")
        new_session = response.headers.get("Mcp-Session-Id") or session_id
        if "id" not in message:
            return None, new_session
        if response.headers.get_content_type() == "text/event-stream":
            for line in body.splitlines():
                if line.startswith("data:"):
                    candidate = json.loads(line[5:].strip())
                    if candidate.get("id") == message["id"]:
                        body = json.dumps(candidate)
                        break
        reply = json.loads(body)
        if "error" in reply:
            raise RuntimeError(reply["error"].get("message") or "error")
        return reply.get("result") or {}, new_session


def http(url, headers=None, timeout=TIMEOUT):
    started = time.monotonic()
    headers = headers or {}
    try:
        init, session_id = _post(url, _initialize(), headers, None, timeout)
        _post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, headers, session_id, timeout)
        tools, cursor, request_id = [], None, 2
        while True:
            params = {"cursor": cursor} if cursor else {}
            page, session_id = _post(url, {"jsonrpc": "2.0", "id": request_id, "method": "tools/list",
                                           "params": params}, headers, session_id, timeout)
            tools += page.get("tools") or []
            cursor, request_id = page.get("nextCursor"), request_id + 1
            if not cursor:
                break
        return _result(True, started, **_summarise(init, tools))
    except urllib.error.HTTPError as e:
        e.close()
        hint = " (needs authentication)" if e.code in (401, 403) else ""
        return _result(False, started, error=f"{url} answered {e.code} {e.reason}{hint}")
    except (urllib.error.URLError, OSError) as e:
        return _result(False, started, error=f"cannot reach {url} ({getattr(e, 'reason', e)})")
    except (ValueError, RuntimeError) as e:
        return _result(False, started, error=str(e))
