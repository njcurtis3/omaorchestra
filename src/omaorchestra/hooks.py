"""Translate Claude Code hook events into session status updates."""

# Claude Code hook event -> omaorchestra status. None means the session ended.
CLAUDE_EVENTS = {
    "SessionStart": "idle",
    "UserPromptSubmit": "working",
    # Fires after a tool runs, so it also clears needs-input once a permission
    # prompt has been answered.
    "PostToolUse": "working",
    "Notification": "needs-input",
    "Stop": "idle",
    "SessionEnd": None,
}


def request_for(event, agent_process=None):
    """Build a daemon request from a Claude Code hook payload, or None to ignore it.

    `agent_process` is the agent's (pid, start_time), recorded so the daemon
    can drop the session if the agent dies without a SessionEnd.
    """
    name = event.get("hook_event_name")
    session_id = event.get("session_id")
    if name not in CLAUDE_EVENTS or not session_id:
        return None
    status = CLAUDE_EVENTS[name]
    if status is None:
        return {"cmd": "remove", "session_id": session_id}
    request = {
        "cmd": "update",
        "session_id": session_id,
        "agent": "claude",
        "status": status,
        "cwd": event.get("cwd"),
        "message": event.get("message") if name == "Notification" else None,
        "transcript_path": event.get("transcript_path"),
    }
    if agent_process and agent_process[1] is not None:
        request["pid"], request["pid_start"] = agent_process
    return request

