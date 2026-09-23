# Architecture (draft)

## Components
- **omaorchestrad**: user service holding the session registry and task queue.
- **omaorchestra CLI**: talks to the daemon over a Unix socket (JSON messages).
- **Adapters**: per-agent contract: `spawn(task, cwd)`, `status()`,
  `attach()`, `stop()`.
- **Bar plugin**: passive QML front end over the daemon.

## Principles
- Status comes from agent hooks where available (for example Claude Code
  hooks calling `omaorchestra event`), not terminal scraping.
- Parallel tasks are isolated in separate git worktrees; merging is manual.
- Approval requests are surfaced to the user, never auto-approved.
- The socket is user-only (0600) and never exposed over the network.

## Protocol
Newline-delimited JSON over the socket, one response per request:

| Request | Response |
|---|---|
| `{"cmd": "ping"}` | `{"ok": true, "version": ...}` |
| `{"cmd": "list"}` | `{"ok": true, "sessions": [...]}` |
| `{"cmd": "update", "session_id", "agent", "status", "cwd"?, "message"?, "pid"?, "pid_start"?, "transcript_path"?, "model"?, "branch"?}` | `{"ok": true, "session": {...}}` |
| `{"cmd": "remove", "session_id", "reason"?}` | `{"ok": true, "removed": bool}` |
| `{"cmd": "subscribe"}` | `{"ok": true, "sessions": [...], "queue": {...}}`, then a stream (below) |
| `{"cmd": "reload"}` | `{"ok": true}`, or the error that kept the old settings |
| `{"cmd": "queue-list"}` | `{"ok": true, "queue": {"held", "busy", "limit", "tasks": [...]}}` |
| `{"cmd": "queue-add", "item", "paused"?}`, `queue-cancel`, `queue-move` (`id`, `position`), `queue-pause`, `queue-resume`, `queue-hold`, `queue-release` | `{"ok": true, "queue": {...}}` |
| `{"cmd": "queue-run", "id"}` | `{"ok": true, "session_id"}` |

Errors return `{"ok": false, "error": ...}` and keep the connection open.

### Subscriptions

After `subscribe` the connection carries one JSON line per change until
either side closes it:

    {"event": "session", "session": {...}}                  # new or updated
    {"event": "removed", "id": "...", "reason": "..."}      # session-end, dismissed, process-gone, did-not-start
    {"event": "queue", "queue": {...}}                      # the queue, or the busy count, changed

Every update is streamed, including ones that only move `updated`. The
snapshot and the subscription are taken together, so nothing is missed in
between. A subscriber more than 1000 events behind is disconnected; it can
reconnect for a fresh snapshot. `omaorchestra watch` (or `watch --json`) is a
reference client.
Statuses: `idle`, `working`, `needs-input`. The registry persists to
`$XDG_STATE_HOME/omaorchestra/sessions.json`.

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

## Open questions
- How each non-Claude agent reports state.
- tmux sessions vs. plain terminal windows for attach/detach.
