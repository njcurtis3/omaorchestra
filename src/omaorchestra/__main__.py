import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import __version__, claude_settings, client, config, control, daemon, hooks, procs, service, windows


def cmd_daemon(args):
    return daemon.run(verbose=args.verbose)


def cmd_ping(args):
    try:
        print(f"omaorchestrad {client.request({'cmd': 'ping'})['version']} is running")
    except client.DaemonUnavailable:
        print("omaorchestrad is not running", file=sys.stderr)
        return 1
    return 0


def cmd_ls(args):
    try:
        sessions = client.request({"cmd": "list"})["sessions"]
    except client.DaemonUnavailable:
        print("omaorchestrad is not running", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(sessions, indent=2))
        return 0
    if not sessions:
        print("no sessions")
        return 0
    now = time.time()
    for s in sessions:
        ago = int(now - s["updated"])
        print(f"{s['id'][:8]}  {s['agent']:<7} {s['status']:<12} {ago:>5}s ago  {s.get('cwd', '')}")
    return 0


# Same order as the bar panel: the session that needs you first.
STATUS_ORDER = {"needs-input": 0, "working": 1, "idle": 2}


def pick_session(sessions, wanted):
    """The session whose id starts with `wanted`, or the first by panel order."""
    if not wanted:
        if not sessions:
            raise windows.WindowError("no agent sessions")
        return sorted(sessions, key=lambda s: (STATUS_ORDER.get(s["status"], 3), -s["updated"]))[0]
    matches = [s for s in sessions if s["id"].startswith(wanted)]
    if not matches:
        raise windows.WindowError(f"no session matching {wanted}")
    if len(matches) > 1:
        raise windows.WindowError(f"{wanted} matches {len(matches)} sessions; give more of the id")
    return matches[0]


def tell(message, notify):
    """Report a problem on stderr, and as a notification when run from a keybinding."""
    print(f"omaorchestra: {message}", file=sys.stderr)
    if notify:
        try:
            subprocess.run(["notify-send", "--app-name=omaorchestra", "--urgency=low", "omaorchestra", message],
                           capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass


def cmd_focus(args):
    try:
        sessions = client.request({"cmd": "list"})["sessions"]
        window, exact = windows.focus_session(pick_session(sessions, args.session))
    except client.DaemonUnavailable:
        tell("omaorchestrad is not running", args.notify)
        return 1
    except windows.WindowError as e:
        tell(str(e), args.notify)
        return 1
    note = "" if exact else " (best guess: that terminal owns several windows)"
    print(f"focused {window.get('class', '')} \"{window.get('title', '')}\"{note}")
    return 0


def cmd_dismiss(args):
    try:
        sessions = client.request({"cmd": "list"})["sessions"]
        session = pick_session(sessions, args.session)
        client.request({"cmd": "remove", "session_id": session["id"], "reason": "dismissed"})
    except client.DaemonUnavailable:
        print("omaorchestrad is not running", file=sys.stderr)
        return 1
    print(f"dismissed {session['id'][:8]} ({session.get('cwd', '')})")
    return 0


def cmd_watch(args):
    """Print session changes as they happen."""
    try:
        stream = client.subscribe()
        sessions = next(stream)
    except (client.DaemonUnavailable, StopIteration):
        print("omaorchestrad is not running", file=sys.stderr)
        return 1
    try:
        if args.json:
            print(json.dumps({"sessions": sessions}), flush=True)
        else:
            print(f"{len(sessions)} session(s); watching for changes (ctrl+c to stop)", flush=True)
        for message in stream:
            if args.json:
                print(json.dumps(message), flush=True)
            elif message["event"] == "session":
                s = message["session"]
                print(f"{time.strftime('%H:%M:%S')}  {s['id'][:8]}  {s['status']:<12} {s.get('cwd', '')}", flush=True)
            else:
                print(f"{time.strftime('%H:%M:%S')}  {message['id'][:8]}  ended ({message.get('reason')})", flush=True)
    except KeyboardInterrupt:
        pass
    return 0


def cmd_app(args):
    from .app import main as app_main
    return app_main.run(check=args.check, session=args.session or "")


def cmd_stop(args):
    try:
        session = pick_session(client.request({"cmd": "list"})["sessions"], args.session)
    except client.DaemonUnavailable:
        print("omaorchestrad is not running", file=sys.stderr)
        return 1
    label = f"{session['id'][:8]} ({session.get('cwd', '')})"
    if not args.yes:
        if not sys.stdin.isatty():
            print("omaorchestra: refusing to stop an agent without confirmation; pass --yes", file=sys.stderr)
            return 1
        if input(f"Stop the agent for {label}? It ends that agent process. [y/N] ").strip().lower() not in ("y", "yes"):
            print("left it running")
            return 1
    control.stop(session)
    print(f"asked the agent for {label} to stop")
    return 0


def cmd_hook(args):
    # Called by agent hooks: never block or fail the agent, whatever happens.
    try:
        if args.agent not in config.load_or_defaults()["agents"]["enabled"]:
            return 0
        request = hooks.request_for(json.load(sys.stdin), procs.agent_process())
        if request:
            client.request(request, timeout=0.5)
    except Exception:
        pass
    return 0


def own_binary():
    """Absolute path of the omaorchestra launcher running now, if known."""
    launcher = os.environ.get("OMAORCHESTRA_BIN")
    if launcher and os.path.isfile(launcher):
        return launcher
    found = shutil.which("omaorchestra")
    return os.path.realpath(found) if found else None


def hook_command(args):
    if args.hook_command:
        return args.hook_command
    binary = own_binary()
    if not binary:
        raise claude_settings.SettingsError("cannot find the omaorchestra binary; pass --command")
    return claude_settings.hook_command(binary)


def cmd_config_path(args):
    print(config.path())
    return 0


def cmd_config_show(args):
    print(config.to_toml(config.load()), end="")
    return 0


def cmd_config_check(args):
    config.load()
    where = config.path()
    print(f"{where}: ok" if where.exists() else f"{where}: not present, using defaults")
    return 0


def cmd_hooks_snippet(args):
    print(json.dumps(claude_settings.install({}, hook_command(args)), indent=2))
    return 0


def change_settings(args, change):
    path = Path(args.settings) if args.settings else claude_settings.default_path()
    before = claude_settings.load(path)
    after = change(before)
    if after == before:
        print(f"{path}: already up to date")
        return 0
    if args.dry_run:
        print(json.dumps(after, indent=2))
        return 0
    backup = claude_settings.save(path, after)
    print(f"updated {path}" + (f" (backup: {backup})" if backup else ""))
    return 0


def cmd_service_install(args):
    binary = own_binary()
    if not binary:
        raise service.ServiceError("cannot find the omaorchestra binary")
    if args.dry_run:
        if service.uses_packaged_unit(binary):
            print(f"would enable packaged {service.PACKAGED_UNIT}")
        else:
            print(f"would write {service.user_unit_dir() / service.UNIT_NAME}:\n")
            print(service.render_unit(binary), end="")
        return 0
    for line in service.install(binary):
        print(line)
    return 0


def cmd_service_uninstall(args):
    for line in service.uninstall() or ["nothing to do"]:
        print(line)
    return 0


def cmd_service_status(args):
    enabled, active = service.status()
    print(f"{service.UNIT_NAME}: {enabled}, {active}")
    return 0 if active == "active" else 1


def cmd_hooks_install(args):
    command = hook_command(args)
    return change_settings(args, lambda s: claude_settings.install(s, command))


def cmd_hooks_uninstall(args):
    return change_settings(args, claude_settings.remove)


def cmd_hooks_status(args):
    path = Path(args.settings) if args.settings else claude_settings.default_path()
    found = claude_settings.installed_events(claude_settings.load(path))
    missing = [e for e in hooks.CLAUDE_EVENTS if e not in found]
    if not found:
        print(f"not installed in {path}")
        return 1
    commands = sorted(set(found.values()))
    print(f"installed in {path}")
    print("command: " + ", ".join(commands))
    if missing:
        print("missing events: " + ", ".join(missing))
    return 1 if missing else 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="omaorchestra", description="Agent coordinator for Omarchy")
    parser.add_argument("--version", action="version", version=f"omaorchestra {__version__}")
    sub = parser.add_subparsers(dest="subcommand")

    daemon_p = sub.add_parser("daemon", help="run the coordinator daemon")
    daemon_p.add_argument("-v", "--verbose", action="store_true", help="log every request")
    daemon_p.set_defaults(func=cmd_daemon)
    sub.add_parser("ping", help="check whether the daemon is running").set_defaults(func=cmd_ping)
    ls = sub.add_parser("ls", help="list agent sessions")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=cmd_ls)
    focus_p = sub.add_parser("focus", help="focus a session's terminal window")
    focus_p.add_argument("session", nargs="?", help="session id or prefix (default: the one that needs you)")
    focus_p.add_argument("--notify", action="store_true", help="report failures as a notification (for keybindings)")
    focus_p.set_defaults(func=cmd_focus)
    app_p = sub.add_parser("app", help="open the omaorchestra app window")
    app_p.add_argument("--check", action="store_true",
                       help="load the app offscreen, report whether it reaches the daemon, and exit")
    app_p.add_argument("--session", help="open on this session's details (id or prefix)")
    app_p.set_defaults(func=cmd_app)
    watch_p = sub.add_parser("watch", help="print session changes as they happen")
    watch_p.add_argument("--json", action="store_true", help="raw protocol messages, one per line")
    watch_p.set_defaults(func=cmd_watch)
    stop_p = sub.add_parser("stop", help="stop a session's agent process (asks first)")
    stop_p.add_argument("session", help="session id or prefix")
    stop_p.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    stop_p.set_defaults(func=cmd_stop)
    dismiss_p = sub.add_parser("dismiss", help="remove a session from the list (it returns if the agent reports again)")
    dismiss_p.add_argument("session", help="session id or prefix")
    dismiss_p.set_defaults(func=cmd_dismiss)
    hook = sub.add_parser("hook", help="receive an agent hook event on stdin")
    hook.add_argument("agent", choices=["claude"])
    hook.set_defaults(func=cmd_hook)

    hooks_cmd = sub.add_parser("hooks", help="manage omaorchestra's Claude Code hooks")
    hooks_sub = hooks_cmd.add_subparsers(dest="hooks_command", required=True)
    for name, func, text in (
        ("install", cmd_hooks_install, "add the hooks to Claude Code's settings.json"),
        ("uninstall", cmd_hooks_uninstall, "remove the hooks, leaving other settings alone"),
        ("status", cmd_hooks_status, "show whether the hooks are installed"),
        ("snippet", cmd_hooks_snippet, "print the hooks block without writing anything"),
    ):
        p = hooks_sub.add_parser(name, help=text)
        p.set_defaults(func=func)
        if name != "snippet":
            p.add_argument("--settings", help="settings.json to edit (default: ~/.claude/settings.json)")
        if name in ("install", "uninstall"):
            p.add_argument("--dry-run", action="store_true", help="print the result instead of writing it")
        if name in ("install", "snippet"):
            p.add_argument("--command", dest="hook_command", help="hook command to use (default: this omaorchestra, by absolute path)")

    config_cmd = sub.add_parser("config", help="inspect the configuration")
    config_sub = config_cmd.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("path", help="print the config file path").set_defaults(func=cmd_config_path)
    config_sub.add_parser("show", help="print the effective config, defaults included").set_defaults(func=cmd_config_show)
    config_sub.add_parser("check", help="validate the config file").set_defaults(func=cmd_config_check)

    service_cmd = sub.add_parser("service", help="run the daemon as a systemd user service")
    service_sub = service_cmd.add_subparsers(dest="service_command", required=True)
    install_p = service_sub.add_parser("install", help="enable and start the service")
    install_p.add_argument("--dry-run", action="store_true", help="show what would be done")
    install_p.set_defaults(func=cmd_service_install)
    service_sub.add_parser("uninstall", help="stop and disable the service").set_defaults(func=cmd_service_uninstall)
    service_sub.add_parser("status", help="show whether the service is running").set_defaults(func=cmd_service_status)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except (claude_settings.SettingsError, service.ServiceError, config.ConfigError, windows.WindowError,
            control.ControlError) as e:
        print(f"omaorchestra: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
