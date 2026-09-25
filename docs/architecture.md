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
- Approval requests are surfaced to the user, never auto-approved.
- The socket is user-only (0600) and never exposed over the network.
- Remote means outbound only: phone pushes are HTTPS POSTs to ntfy
  (`remote.py`); omaorchestra opens no port of its own. What a push says is
  set by `[remote] content`, and prompts leave the machine only at `full`.
  Pushes go out only while you are away (`away.py`): the mode is set by hand,
  or read from the lock screen and the compositor's idle notifications,
  both local.

## Protocol
Newline-delimited JSON over the socket, one response per request:

| Request | Response |
|---|---|
| `{"cmd": "ping"}` | `{"ok": true, "version": ...}` |
| `{"cmd": "list"}` | `{"ok": true, "sessions": [...]}` |
| `{"cmd": "update", "session_id", "agent", "status", "cwd"?, "message"?, "pid"?, "pid_start"?, "transcript_path"?, "model"?, "branch"?}` | `{"ok": true, "session": {...}}` |
| `{"cmd": "remove", "session_id", "reason"?}` | `{"ok": true, "removed": bool}` |
| `{"cmd": "subscribe"}` | `{"ok": true, "sessions": [...], "queue": {...}, "away": {...}}`, then a stream (below) |
| `{"cmd": "away", "mode"?}` | `{"ok": true, "away": {"mode", "away", "reason", "since", "push", "after", "locked", "idle"}}`; `mode` (auto, on, off) sets it |
| `{"cmd": "reload"}` | `{"ok": true}`, or the error that kept the old settings |
| `{"cmd": "queue-list"}` | `{"ok": true, "queue": {"held", "busy", "limit", "blocked", "tasks": [...]}}` |
| `{"cmd": "queue-add", "item", "paused"?}`, `queue-cancel`, `queue-move` (`id`, `position`), `queue-pause`, `queue-resume`, `queue-hold`, `queue-release` | `{"ok": true, "queue": {...}}` |
| `{"cmd": "queue-run", "id"}` | `{"ok": true, "session_id"}` |

Errors return `{"ok": false, "error": ...}` and keep the connection open.

### Subscriptions

After `subscribe` the connection carries one JSON line per change until
either side closes it:

    {"event": "session", "session": {...}}                  # new or updated
    {"event": "removed", "id": "...", "reason": "..."}      # session-end, dismissed, process-gone, did-not-start
    {"event": "queue", "queue": {...}}                      # the queue, or the busy count, changed
    {"event": "away", "away": {...}}                        # away mode, or whether you are away, changed

Every update is streamed, including ones that only move `updated`. The
snapshot and the subscription are taken together, so nothing is missed in
between. A subscriber more than 1000 events behind is disconnected; it can
reconnect for a fresh snapshot. `omaorchestra watch` (or `watch --json`) is a
reference client.
Statuses: `idle`, `working`, `needs-input`. The registry persists to
`$XDG_STATE_HOME/omaorchestra/sessions.json`, and away mode to `away.json`
beside it (the bar widget watches both).

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
