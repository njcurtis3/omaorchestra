# omaorchestra

Agent coordinator and harness interface for [Omarchy](https://omarchy.org/).

Run AI coding agents side by side, see which are working, waiting for you, or
done, and jump into any of them, from the terminal or the Omarchy bar.

> **Status:** early prototype. The daemon tracks Claude Code sessions through
> hooks; there is no bar widget or task queue yet.
> omaorchestra is an independent, third-party project. It is not part of, or
> endorsed by, Omarchy.

## Planned design

```
omaorchestrad (user systemd service)
   ├─ session registry   who is running, where, what state
   ├─ task queue         pending / running / done / failed
   ├─ adapters           one small module per agent CLI
   └─ unix socket API    $XDG_RUNTIME_DIR/omaorchestra.sock (mode 0600)
          ▲                          ▲
   omaorchestra CLI           Omarchy bar widget + panel (QML)
```

See [docs/architecture.md](docs/architecture.md).

## Layout

| Path | Purpose |
|---|---|
| `src/omaorchestra/` | daemon and CLI (Python, standard library only) |
| `bin/` | entry-point scripts |
| `plugin/` | Omarchy shell bar widget and panel (QML) |
| `packaging/` | PKGBUILD and systemd user unit |
| `config.example.toml` | documented configuration |

## Try it

```bash
bin/omaorchestra daemon &          # start the coordinator
bin/omaorchestra hooks-snippet     # hooks block for ~/.claude/settings.json
bin/omaorchestra ls                # sessions and their state
```

Merge the printed hooks block into `~/.claude/settings.json` (the `command`
must resolve to this checkout's `bin/omaorchestra`, or an installed copy).
Each Claude Code session then reports itself:

| Hook | Status |
|---|---|
| `SessionStart`, `Stop` | `idle` |
| `UserPromptSubmit`, `PostToolUse` | `working` |
| `Notification` | `needs-input` (permission prompt or waiting) |
| `SessionEnd` | removed |

The hook command never fails or blocks the agent: if the daemon is down, events
are dropped.

## Development

```bash
python -m unittest discover tests
```

Set `OMAORCHESTRA_SOCKET` and `OMAORCHESTRA_STATE_DIR` to run a throwaway
daemon alongside a real one.

## Configuration

Copy `config.example.toml` to `~/.config/omaorchestra/config.toml`. Personal
settings live there, never in the source tree.

## Requirements

Omarchy on Arch Linux, Python 3.11+. Plugin APIs in Omarchy are still changing,
so a supported Omarchy version will be pinned here once there is a release.

## License

[MIT](LICENSE)
