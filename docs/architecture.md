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

## Open questions
- How each non-Claude agent reports state.
- tmux sessions vs. plain terminal windows for attach/detach.
