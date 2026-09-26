# Architecture

## Components
- **omaorchestrad**: user service holding the session registry and task queue.
- **omaorchestra CLI**: talks to the daemon over a Unix socket (JSON messages).
- **Adapters**: one per agent CLI (Claude Code, Codex, opencode): how to
  start it, how its hook events map to statuses, and how its hooks are
  installed.
- **Bar plugin**: passive QML front end; it reads the registry file the
  daemon writes and never talks to the socket.
- **App**: PySide6 + QML, a client of the socket like the CLI.

## Principles
- Status comes from agent hooks (`omaorchestra hook <agent>`), not terminal
  scraping.
- Parallel tasks are isolated in separate git worktrees; merging is manual.
- Approval requests are surfaced to the user, never auto-approved. While you
  are away, a Claude Code permission prompt can also be answered remotely
  (`approvals.py`): its PermissionRequest hook waits for your answer beside
  the terminal prompt, which stays up. This is the one hook that waits; one
  answer covers one request and never adds a rule. The daemon refuses
  answers whose sender (by the socket's peer credentials) runs under an
  agent: a guard against agents answering in passing, not a boundary, since
  agents run as the user.
- The socket is user-only (0600) and never exposed over the network; nothing
  omaorchestra runs listens on a port (`tests/test_no_ports.py`). The threat
  model is [security.md](security.md).
- Remote means outbound only: phone pushes are HTTPS POSTs to ntfy
  (`remote.py`); omaorchestra opens no port of its own. What a push says is
  set by `[remote] content`, and prompts leave the machine only at `full`.
  Pushes go out only while you are away (`away.py`): the mode is set by
  hand, or read from the lock screen and the compositor's idle
  notifications, both local.
- Reaching the machine from a phone is the system's SSH over Tailscale
  ([remote.md](remote.md)), ideally with a key whose forced command is
  `omaorchestra top`. `setup --only remote` (`remote_access.py`) only reads
  that setup; it never changes sshd, Tailscale or the firewall.

## Protocol
Newline-delimited JSON over the socket, one response per request:

| Request | Response |
|---|---|
| `{"cmd": "ping"}` | `{"ok": true, "version": ...}` |
| `{"cmd": "list"}` | `{"ok": true, "sessions": [...]}` |
| `{"cmd": "update", "session_id", "agent", "status", "cwd"?, "message"?, "pid"?, "pid_start"?, "transcript_path"?, "model"?, "branch"?}` | `{"ok": true, "session": {...}}` |
| `{"cmd": "remove", "session_id", "reason"?}` | `{"ok": true, "removed": bool}` |
| `{"cmd": "subscribe"}` | `{"ok": true, "sessions": [...], "queue": {...}, "away": {...}, "approvals": [...]}`, then a stream (below) |
| `{"cmd": "away", "mode"?}` | `{"ok": true, "away": {"mode", "away", "reason", "since", "push", "after", "locked", "idle"}}`; `mode` (auto, on, off) sets it |
| `{"cmd": "approval-ask", "session_id", "tool", "summary", "cwd"}` (the hook) | held open until answered, then `{"ok": true, "decision": {"behavior", "message"} or null, "reason"?}` |
| `{"cmd": "approvals"}` | `{"ok": true, "approvals": [{"id", "session_id", "tool", "summary", "cwd", "asked"}]}` |
| `{"cmd": "approval-answer", "id", "behavior": "allow"\|"deny", "message"?, "source"?}` | `{"ok": true, "approval": {...}}` |
| `{"cmd": "reload"}` | `{"ok": true}`, or the error that kept the old settings |
| `{"cmd": "queue-list"}` | `{"ok": true, "queue": {"held", "busy", "limit", "blocked", "tasks": [...]}}` |
| `{"cmd": "queue-add", "item", "paused"?}`, `queue-cancel`, `queue-move` (`id`, `position`), `queue-pause`, `queue-resume`, `queue-hold`, `queue-release` | `{"ok": true, "queue": {...}}` |
| `{"cmd": "queue-run", "id"}` | `{"ok": true, "session_id"}` |
| `{"cmd": "queue-add", "item": {..., "after"?, "same_worktree"?, "brief"?, "review"?}}` | a chained task (`chain.py`): it waits (state `waiting`) until the task or session it follows finishes, then is released, or `held` with the reason |
| `{"cmd": "stopping", "session_id"}` | `{"ok": true, "known": bool}`: it is being stopped on purpose (history says "stopped", not "crashed") |

Errors return `{"ok": false, "error": ...}` and keep the connection open.

### Subscriptions

After `subscribe` the connection carries one JSON line per change until
either side closes it:

    {"event": "session", "session": {...}}                  # new or updated
    {"event": "removed", "id": "...", "reason": "..."}      # session-end, dismissed, process-gone, did-not-start
    {"event": "queue", "queue": {...}}                      # the queue, or the busy count, changed
    {"event": "away", "away": {...}}                        # away mode, or whether you are away, changed
    {"event": "approvals", "approvals": [...]}              # permission prompts waiting for a remote answer

Every update is streamed, including ones that only move `updated`. The
snapshot and the subscription are taken together, so nothing is missed in
between. A subscriber more than 1000 events behind is disconnected; it can
reconnect for a fresh snapshot. `omaorchestra watch` (or `watch --json`) is a
reference client.
Statuses: `idle`, `working`, `needs-input`. The registry persists to
`$XDG_STATE_HOME/omaorchestra/sessions.json`, and away mode to `away.json`
beside it (the bar widget watches both). When a session ends the daemon
appends a record to `history.jsonl` (`history.py`), off the event loop: what
it did, its time per status, cost, the commits between its start (the
folder's HEAD when it first reported) and its end, and its outcome.

A second daemon refuses to start while one is answering on the socket; a stale
socket file is replaced.

## Liveness
Hooks report the agent's process as `pid` plus `pid_start` (start time in
clock ticks, field 22 of `/proc/<pid>/stat`). PIDs are reused, so the pair is
the identity. The hook takes the PID from `CLAUDE_PID`, which Claude Code
exports to its children, and otherwise walks up the process tree to the
nearest process named `claude`.

The daemon drops a session when that process is gone or the PID now belongs
to a different process: on startup, on every `list`, and every 30 seconds.
Sessions without a recorded process are kept.
