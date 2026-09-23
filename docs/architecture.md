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
| `{"cmd": "update", "session_id", "agent", "status", "cwd"?, "message"?}` | `{"ok": true, "session": {...}}` |
| `{"cmd": "remove", "session_id"}` | `{"ok": true, "removed": bool}` |

Errors return `{"ok": false, "error": ...}` and keep the connection open.
Statuses: `idle`, `working`, `needs-input`. The registry persists to
`$XDG_STATE_HOME/omaorchestra/sessions.json`.

A second daemon refuses to start while one is answering on the socket; a stale
socket file is replaced.

## Open questions
- Sessions that exit without `SessionEnd` (crash, kill) linger in the
  registry. Needs a liveness check, e.g. a PID reported by the hook.
- How each non-Claude agent reports state.
- tmux sessions vs. plain terminal windows for attach/detach.
