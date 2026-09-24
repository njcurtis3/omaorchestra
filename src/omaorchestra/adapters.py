"""One adapter per agent CLI: how to start it, how it reports its state, and
how its reporting is installed.

Every adapter maps the agent's hook events onto omaorchestra's statuses
(working, needs-input, idle, or ended), knows the agent's process names (to
find it from a hook, or after a launch), builds its command line, and
installs, removes and checks its hooks. Features some agents lack are
flags: a session id chosen up front, MCP profiles, routing through a
provider.
"""

import json
import os
import shlex
from pathlib import Path

from . import claude_settings, procs


class Adapter:
    name = ""
    label = ""
    process_names = ()
    events = {}                  # hook event name -> status, or None for "ended"
    needs_input_events = ()      # events whose message is shown while waiting
    supports_session_id = False  # can be told its session id at launch
    supports_mcp_profile = False
    supports_routing = False
    # Shown when a launched agent has started but not reported for this long.
    silent_message = "Not started yet: check its window."
    quiet_seconds = 30  # opencode took about 17 s to report on its first start

    def binary(self):
        """The command to run; OMAORCHESTRA_<NAME> overrides it (tests use a stand-in)."""
        return os.environ.get(f"OMAORCHESTRA_{self.name.upper()}") or self.name

    def command(self, task, session_id, workdir, model=None, permission_mode=None, extra=(), agent_bin=None):
        raise NotImplementedError

    def message(self, event):
        return event.get("message")

    def agent_process(self, event):
        """(pid, start_time) of the agent that sent `event`, or None."""
        return procs.agent_process(names=self.process_names)

    def request_for(self, event, agent_process=None):
        """A daemon request for one hook event, or None to ignore it."""
        name, session_id = event.get("hook_event_name"), event.get("session_id")
        if name not in self.events or not session_id:
            return None
        status = self.events[name]
        if status is None:
            return {"cmd": "remove", "session_id": session_id}
        request = {"cmd": "update", "session_id": session_id, "agent": self.name, "status": status,
                   "cwd": event.get("cwd"), "message": self.message(event) if name in self.needs_input_events else None,
                   "transcript_path": event.get("transcript_path"), "model": event.get("model") or None,
                   "launch_id": event.get("launch_id")}
        if agent_process and agent_process[1] is not None:
            request["pid"], request["pid_start"] = agent_process
        return request

    # hooks
    def install_hooks(self, command, target=None):
        raise NotImplementedError

    def uninstall_hooks(self, target=None):
        raise NotImplementedError

    def hooks_status(self, target=None):
        """(installed, detail)."""
        raise NotImplementedError


# ---------------------------------------------------------------- Claude Code

class Claude(Adapter):
    name, label = "claude", "Claude Code"
    process_names = ("claude",)
    events = {
        "SessionStart": "idle",
        "UserPromptSubmit": "working",
        # Fires after a tool runs, so it also clears needs-input once a
        # permission prompt has been answered.
        "PostToolUse": "working",
        "Notification": "needs-input",
        "Stop": "idle",
        "SessionEnd": None,
    }
    needs_input_events = ("Notification",)
    supports_session_id = supports_mcp_profile = supports_routing = True
    silent_message = "Not started yet: its window may be asking whether to trust this folder."
    quiet_seconds = 10

    def command(self, task, session_id, workdir, model=None, permission_mode=None, extra=(), agent_bin=None):
        command = [agent_bin or self.binary(), "--session-id", session_id]
        if permission_mode:
            command += ["--permission-mode", permission_mode]
        if model:
            command += ["--model", model]
        return command + list(extra) + ["--", task]

    def request_for(self, event, agent_process=None):
        request = super().request_for(event, agent_process)
        if request and request["cmd"] == "update":
            request.pop("model")  # Claude's model comes from its transcript (it can change mid-session)
        return request

    def _path(self, target):
        return Path(target) if target else claude_settings.default_path()

    def install_hooks(self, command, target=None):
        return claude_settings.change(self._path(target), lambda s: claude_settings.install(s, command, self.events))

    def uninstall_hooks(self, target=None):
        return claude_settings.change(self._path(target), claude_settings.remove)

    def hooks_status(self, target=None):
        found = claude_settings.installed_events(claude_settings.load(self._path(target)))
        missing = [e for e in self.events if e not in found]
        return bool(found) and not missing, (f"missing {', '.join(missing)}" if found and missing else "")


# ---------------------------------------------------------------- Codex

class Codex(Adapter):
    """Codex's hooks match Claude Code's in shape and payload
    (developers.openai.com/codex/hooks): ~/.codex/hooks.json, events such as
    SessionStart/UserPromptSubmit/PostToolUse/PermissionRequest/Stop/
    SessionEnd, stdin with session_id, cwd, transcript_path and model.
    Codex runs a hook only once the user has trusted it (/hooks in Codex),
    and does not tell hooks its PID, so the hook walks up to `codex`."""

    name, label = "codex", "Codex"
    process_names = ("codex",)
    events = {
        "SessionStart": "idle",
        "UserPromptSubmit": "working",
        "PostToolUse": "working",
        "PermissionRequest": "needs-input",
        "Stop": "idle",
        "SessionEnd": None,
    }
    needs_input_events = ("PermissionRequest",)
    silent_message = ("Not reporting: check its window. Codex runs omaorchestra's hooks only once you trust "
                      "them (/hooks in Codex).")

    def command(self, task, session_id, workdir, model=None, permission_mode=None, extra=(), agent_bin=None):
        command = [agent_bin or self.binary(), "-C", str(workdir)]
        if model:
            command += ["-m", model]
        if permission_mode:
            command += ["-a", permission_mode]
        return command + list(extra) + ["--", task]

    def message(self, event):
        if event.get("message"):
            return event["message"]
        tool, tool_input = event.get("tool_name"), event.get("tool_input") or {}
        detail = tool_input.get("command") if isinstance(tool_input, dict) else None
        if isinstance(detail, list):
            detail = shlex.join(detail)
        return f"Codex asks to use {tool}" + (f": {detail}" if detail else "") if tool else "Codex needs your approval"

    def _path(self, target):
        return Path(target) if target else Path.home() / ".codex" / "hooks.json"

    def install_hooks(self, command, target=None):
        return claude_settings.change(self._path(target), lambda s: claude_settings.install(s, command, self.events))

    def uninstall_hooks(self, target=None):
        return claude_settings.change(self._path(target), claude_settings.remove)

    def hooks_status(self, target=None):
        found = claude_settings.installed_events(claude_settings.load(self._path(target)))
        missing = [e for e in self.events if e not in found]
        installed = bool(found) and not missing
        return installed, ("trust them in Codex with /hooks, or they do not run" if installed else
                           (f"missing {', '.join(missing)}" if found else ""))


# ---------------------------------------------------------------- opencode

PLUGIN_MARKER = "// Written by `omaorchestra hooks install --agent opencode`; `hooks uninstall` removes it."

PLUGIN = PLUGIN_MARKER + """
// Reports this opencode session to omaorchestra: working, waiting for
// permission, idle, ended. Event names follow opencode's plugin docs.
export const Omaorchestra = async ({ directory }) => {
  const send = (name, sessionID, extra = {}) => {
    if (!sessionID) return
    const payload = JSON.stringify({
      hook_event_name: name, session_id: sessionID, cwd: directory, pid: process.pid,
      launch_id: process.env.OMAORCHESTRA_LAUNCH_ID || null, ...extra,
    })
    try {
      const proc = Bun.spawn(__COMMAND__, { stdin: "pipe", stdout: "ignore", stderr: "ignore" })
      proc.stdin.write(payload)
      proc.stdin.end()
    } catch (e) {}
  }
  const sid = (p) => (p && (p.sessionID || (p.info && p.info.id) || p.id)) || null
  return {
    event: async ({ event }) => {
      const p = event.properties || {}
      switch (event.type) {
        case "session.created": return send("SessionStart", sid(p))
        case "session.status":
          return send(p.status && p.status.type === "idle" ? "Stop" : "UserPromptSubmit", sid(p))
        case "session.idle": return send("Stop", sid(p))
        case "permission.asked": return send("PermissionRequest", sid(p), { message: p.title || "opencode needs your permission" })
        case "permission.replied": return send("PostToolUse", sid(p))
        case "session.deleted": return send("SessionEnd", sid(p))
      }
    },
  }
}
"""


class OpenCode(Adapter):
    name, label = "opencode", "opencode"
    process_names = ("opencode", ".opencode")
    events = {
        "SessionStart": "idle",
        "UserPromptSubmit": "working",
        "PostToolUse": "working",
        "PermissionRequest": "needs-input",
        "Stop": "idle",
        "SessionEnd": None,
    }
    needs_input_events = ("PermissionRequest",)

    def command(self, task, session_id, workdir, model=None, permission_mode=None, extra=(), agent_bin=None):
        command = [agent_bin or self.binary(), str(workdir), "--prompt", task]
        if model:
            command += ["-m", model]
        return command + list(extra)

    def agent_process(self, event):
        """The plugin runs inside opencode and sends its PID."""
        pid = event.get("pid")
        if isinstance(pid, int):
            started = procs.start_time(pid)
            if started is not None:
                return pid, started
        return super().agent_process(event)

    def _path(self, target):
        return Path(target) if target else Path.home() / ".config" / "opencode" / "plugins" / "omaorchestra.js"

    def install_hooks(self, command, target=None):
        path = self._path(target)
        if path.exists() and PLUGIN_MARKER not in path.read_text():
            raise claude_settings.SettingsError(f"{path} exists and was not written by omaorchestra")
        text = PLUGIN.replace("__COMMAND__", json.dumps(shlex.split(command)))
        if path.exists() and path.read_text() == text:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return str(path)

    def uninstall_hooks(self, target=None):
        path = self._path(target)
        if path.exists() and PLUGIN_MARKER in path.read_text():
            path.unlink()
            return str(path)
        return None

    def hooks_status(self, target=None):
        path = self._path(target)
        installed = path.exists() and PLUGIN_MARKER in path.read_text()
        return installed, str(path) if installed else ""


ADAPTERS = {a.name: a for a in (Claude(), Codex(), OpenCode())}


def get(name):
    if name not in ADAPTERS:
        raise KeyError(f"unknown agent {name} (known: {', '.join(ADAPTERS)})")
    return ADAPTERS[name]
