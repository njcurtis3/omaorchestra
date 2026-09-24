import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import (__version__, catalog, claude_settings, client, config, control, daemon, hooks, keys, launch, procs,
               providers, service, windows, worktrees)


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
        sessions = next(stream)["sessions"]
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
            elif message["event"] == "queue":
                q = message["queue"]
                print(f"{time.strftime('%H:%M:%S')}  queue: {len(q['tasks'])} task(s), {q['busy']}/{q['limit']} busy"
                      + (", held" if q["held"] else ""), flush=True)
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


def cmd_run(args):
    result = launch.run(args.task, args.dir, permission_mode=args.permission_mode, model=args.model,
                        extra=args.agent_args, worktree=args.worktree)
    record = result["worktree"]
    where = record["workdir"] if record else launch.Path(args.dir).expanduser().resolve()
    print(f"started {result['id'][:8]} in {where}")
    if record:
        print(f"  on branch {record['branch']}, from {record.get('base_branch') or record['base'][:8]}")
    if result["note"]:
        print(f"  ({result['note']})")
    if not result["tracked"]:
        print("(omaorchestrad is not running, so this session is not tracked)")
    return 0


def queue_request(payload):
    try:
        response = client.request(payload, timeout=30)  # a start may create a worktree
    except client.DaemonUnavailable:
        raise launch.LaunchError("omaorchestrad is not running; the queue lives in the daemon") from None
    if not response.get("ok"):
        raise launch.LaunchError(response.get("error") or "the daemon refused")
    return response


def print_queue(q):
    state = "held" if q["held"] else "running"
    print(f"{q['busy']} of {q['limit']} agent slots busy; queue {state}")
    if q.get("blocked"):
        print(f"waiting: {q['blocked']['text']}")
    if not q["tasks"]:
        print("no queued tasks")
    for i, t in enumerate(q["tasks"], 1):
        mark = {"pending": " ", "paused": "‖", "failed": "!"}.get(t["state"], "?")
        print(f"{i:>2} {mark} {t['id'][:8]}  {launch.short(t['task'], 60)}")
        print(f"             {t['cwd']}" + (f"   (failed: {t['error']})" if t.get("error") else ""))


def cmd_queue_list(args):
    print_queue(queue_request({"cmd": "queue-list"})["queue"])
    return 0


def cmd_queue_add(args):
    cwd = launch.Path(args.dir).expanduser().resolve()
    item = {"task": args.task, "cwd": str(cwd), "model": args.model, "permission_mode": args.permission_mode,
            "worktree": args.worktree, "extra": args.agent_args, **launch.agent_environment()}
    response = queue_request({"cmd": "queue-add", "item": item, "paused": args.paused})
    print(f"queued {response['item']['id'][:8]}")
    print_queue(response["queue"])
    return 0


def cmd_queue_action(args):
    cmd = {"cancel": "queue-cancel", "pause": "queue-pause", "resume": "queue-resume", "run": "queue-run",
           "hold": "queue-hold", "release": "queue-release"}[args.queue_command]
    payload = {"cmd": cmd}
    if getattr(args, "id", None):
        payload["id"] = args.id
    response = queue_request(payload)
    if cmd == "queue-run":
        print(f"started {response['session_id'][:8]}")
        return 0
    print_queue(response["queue"])
    return 0


def cmd_queue_move(args):
    q = queue_request({"cmd": "queue-list"})["queue"]
    ids = [t["id"] for t in q["tasks"]]
    match = [i for i in ids if i.startswith(args.id)]
    if len(match) != 1:
        raise launch.LaunchError(f"no single queued task matching {args.id}")
    now = ids.index(match[0])
    position = {"up": now - 1, "down": now + 1}.get(args.queue_command)
    if position is None:
        position = args.position - 1  # people count from 1
    print_queue(queue_request({"cmd": "queue-move", "id": match[0], "position": position})["queue"])
    return 0


def cmd_provider_list(args):
    items = providers.load()
    if not items:
        print("no providers; add one with `omaorchestra provider add <kind>` "
              f"({', '.join(providers.KINDS)})")
        return 0
    for p in items:
        key = "no key needed" if not providers.needs_key(p) else ("key stored" if keys.lookup(p["id"]) else "no key yet")
        print(f"{p['id']:<14} {p['label']:<16} {p['base_url']:<34} {key}")
    return 0


def cmd_provider_add(args):
    item = providers.add(args.kind, args.id, args.base_url)
    print(f"added {item['id']} ({item['base_url']})")
    if providers.needs_key(item):
        print(f"next: omaorchestra provider key {item['id']}")
    return 0


def cmd_provider_key(args):
    provider = providers.get(args.id)
    if args.stdin:
        key = sys.stdin.read()
    else:
        import getpass
        key = getpass.getpass(f"API key for {provider['label']} (not shown): ")
    keys.store(provider["id"], key)
    print(f"stored the key for {provider['id']} in the system keyring")
    return 0


def cmd_provider_remove(args):
    providers.remove(args.id)
    print(f"removed {args.id} and its key")
    return 0


def cmd_provider_test(args):
    print(providers.test(providers.get(args.id)))
    return 0


def cmd_models(args):
    if args.refresh:
        cache = catalog.refresh([args.provider] if args.provider else None)
        for pid, entry in cache.items():
            if entry.get("error") and (not args.provider or pid == args.provider):
                print(f"{pid}: {entry['error']}", file=sys.stderr)
    models = [m for m in catalog.all_models() if not args.provider or m["provider"] == args.provider]
    if args.json:
        print(json.dumps(models, indent=2))
        return 0
    for m in models:
        price = (f"${m['input_price']:g}/${m['output_price']:g} per MTok"
                 if m["input_price"] is not None and m["output_price"] is not None else "")
        context = f"{m['context'] // 1000}K ctx" if m.get("context") else ""
        print(f"{m['provider']:<12} {m['id']:<44} {context:<10} {price}")
    if not any(m["provider"] != "claude-code" for m in models) and not args.refresh:
        print("(only Claude Code's aliases; add providers and run `omaorchestra models --refresh`)")
    return 0


def cmd_worktree_list(args):
    items = worktrees.records()
    if not items:
        print("no worktrees")
        return 0
    for r in items:
        info = worktrees.status(r)
        state = ("removed" if not info["exists"] else "merged" if info["merged"]
                 else f"{len(info['commits'])} commit(s)" + (", uncommitted changes" if info["dirty"] else ""))
        print(f"{r['session_id'][:8]}  {r['branch']:<48} {state}")
        print(f"          {r['path']}")
    return 0


def cmd_worktree_diff(args):
    result = worktrees.changes(worktrees.find(args.worktree))
    if result["error"]:
        raise worktrees.WorktreeError(result["error"])
    for commit in result["commits"]:
        print(f"commit {commit}")
    print(result["diff"] or "(no changes since the base)", end="" if result["diff"] else "\n")
    for name in result["untracked"]:
        print(f"untracked: {name}")
    return 0


def cmd_worktree_merge(args):
    print(worktrees.merge(worktrees.find(args.worktree)))
    return 0


def cmd_worktree_remove(args):
    print(worktrees.remove(worktrees.find(args.worktree), force=args.force))
    return 0


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
    if control.wait_until_gone(session):
        client.request({"cmd": "list"})  # prunes it now rather than at the next check
        print(f"stopped the agent for {label}")
    else:
        print(f"asked the agent for {label} to stop; it has not exited yet")
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


def cmd_config_set(args):
    section, _, key = args.setting.partition(".")
    value = config.parse_value(section, key, args.value)
    config.save({section: {key: value}})
    print(f"{args.setting} = {config.toml_value(value)}")
    return reload_daemon(quiet_if_down=True)


def reload_daemon(quiet_if_down=False):
    try:
        response = client.request({"cmd": "reload"})
    except client.DaemonUnavailable:
        if not quiet_if_down:
            print("omaorchestrad is not running", file=sys.stderr)
            return 1
        print("(omaorchestrad is not running; it will use this when it starts)")
        return 0
    if not response.get("ok"):
        print(f"omaorchestra: the daemon kept its old settings: {response.get('error')}", file=sys.stderr)
        return 1
    print("omaorchestrad reloaded its settings")
    return 0


def cmd_config_reload(args):
    return reload_daemon()


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
    run_p = sub.add_parser("run", help="start an agent on a task in a new terminal window")
    run_p.add_argument("task", help="what the agent should do")
    run_p.add_argument("--in", dest="dir", default=".", help="folder to work in (default: here)")
    run_p.add_argument("--model", help="model to use, passed to the agent")
    run_p.add_argument("--permission-mode", help="the agent's permission mode (default: its own setting)")
    run_p.add_argument("--worktree", dest="worktree", action="store_true", default=None,
                       help="work in a separate git worktree (default: tasks.isolate_with_worktrees)")
    run_p.add_argument("--no-worktree", dest="worktree", action="store_false", help="work in the folder itself")
    run_p.set_defaults(func=cmd_run, agent_args=[])
    run_p.epilog = "Anything after -- is passed to the agent as is."
    qp = sub.add_parser("queue", help="tasks waiting for a free agent slot")
    qp.set_defaults(func=cmd_queue_list)
    q_sub = qp.add_subparsers(dest="queue_command")
    q_sub.add_parser("list", help="the queue and how many slots are busy").set_defaults(func=cmd_queue_list)
    qa = q_sub.add_parser("add", help="queue a task (anything after -- goes to the agent)")
    qa.add_argument("task")
    qa.add_argument("--in", dest="dir", default=".")
    qa.add_argument("--model")
    qa.add_argument("--permission-mode")
    qa.add_argument("--worktree", dest="worktree", action="store_true", default=None)
    qa.add_argument("--no-worktree", dest="worktree", action="store_false")
    qa.add_argument("--paused", action="store_true", help="add it paused")
    qa.set_defaults(func=cmd_queue_add, agent_args=[])
    for name, text in (("cancel", "remove a task from the queue"), ("pause", "skip it until resumed"),
                       ("resume", "let it run again (also retries a failed task)"),
                       ("run", "start it now, whatever the limit")):
        p = q_sub.add_parser(name, help=text)
        p.add_argument("id", help="queued task id or prefix")
        p.set_defaults(func=cmd_queue_action)
    for name, text in (("hold", "start nothing new until released"), ("release", "let the queue run again")):
        q_sub.add_parser(name, help=text).set_defaults(func=cmd_queue_action)
    mv = q_sub.add_parser("move", help="put a task at a position (1 = next)")
    mv.add_argument("id")
    mv.add_argument("position", type=int)
    mv.set_defaults(func=cmd_queue_move)
    for name in ("up", "down"):
        p = q_sub.add_parser(name, help=f"move a task one place {name}")
        p.add_argument("id")
        p.set_defaults(func=cmd_queue_move)

    pp = sub.add_parser("provider", help="model providers (API keys live in the system keyring)")
    p_sub = pp.add_subparsers(dest="provider_command", required=True)
    p_sub.add_parser("list", help="configured providers").set_defaults(func=cmd_provider_list)
    pa = p_sub.add_parser("add", help="add a provider: " + ", ".join(providers.KINDS))
    pa.add_argument("kind", choices=list(providers.KINDS))
    pa.add_argument("--id", help="a name for it (default: the kind)")
    pa.add_argument("--base-url", help="a different endpoint (a proxy, a self-hosted Ollama, ...)")
    pa.set_defaults(func=cmd_provider_add)
    pk = p_sub.add_parser("key", help="store its API key in the system keyring")
    pk.add_argument("id")
    pk.add_argument("--stdin", action="store_true", help="read the key from standard input")
    pk.set_defaults(func=cmd_provider_key)
    for name, func, text in (("remove", cmd_provider_remove, "forget it and its key"),
                             ("test", cmd_provider_test, "check it answers and accepts the key")):
        p = p_sub.add_parser(name, help=text)
        p.add_argument("id")
        p.set_defaults(func=func)
    mp = sub.add_parser("models", help="models from Claude Code and your providers")
    mp.add_argument("--provider", help="only this provider")
    mp.add_argument("--refresh", action="store_true", help="fetch the lists again")
    mp.add_argument("--json", action="store_true")
    mp.set_defaults(func=cmd_models)

    wt = sub.add_parser("worktree", help="task worktrees: list, review, merge, remove")
    wt_sub = wt.add_subparsers(dest="worktree_command", required=True)
    wt_sub.add_parser("list", help="every task worktree and how it stands").set_defaults(func=cmd_worktree_list)
    for name, func, text in (("diff", cmd_worktree_diff, "everything done since the task started"),
                             ("merge", cmd_worktree_merge, "merge into the branch it started from"),
                             ("remove", cmd_worktree_remove, "delete it (refuses to lose work without --force)")):
        p = wt_sub.add_parser(name, help=text)
        p.add_argument("worktree", help="session id (or prefix), branch, or path")
        if name == "remove":
            p.add_argument("--force", action="store_true", help="discard uncommitted or unmerged work")
        p.set_defaults(func=func)
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
    set_p = config_sub.add_parser("set", help="change a setting, keeping the file's comments")
    set_p.add_argument("setting", help="section.key, e.g. notifications.finished_after")
    set_p.add_argument("value", help="true/false, a number, or a comma-separated list")
    set_p.set_defaults(func=cmd_config_set)
    config_sub.add_parser("reload", help="make the daemon re-read the config").set_defaults(func=cmd_config_reload)

    service_cmd = sub.add_parser("service", help="run the daemon as a systemd user service")
    service_sub = service_cmd.add_subparsers(dest="service_command", required=True)
    install_p = service_sub.add_parser("install", help="enable and start the service")
    install_p.add_argument("--dry-run", action="store_true", help="show what would be done")
    install_p.set_defaults(func=cmd_service_install)
    service_sub.add_parser("uninstall", help="stop and disable the service").set_defaults(func=cmd_service_uninstall)
    service_sub.add_parser("status", help="show whether the service is running").set_defaults(func=cmd_service_status)

    # `run ... -- <agent args>`: split them off first, since argparse would
    # otherwise mix them up with run's own options.
    argv = list(sys.argv[1:] if argv is None else argv)
    agent_args = []
    if (argv[:1] == ["run"] or argv[:2] == ["queue", "add"]) and "--" in argv:
        split = argv.index("--")
        argv, agent_args = argv[:split], argv[split + 1:]
    args = parser.parse_args(argv)
    if agent_args:
        args.agent_args = agent_args
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except (claude_settings.SettingsError, service.ServiceError, config.ConfigError, windows.WindowError,
            control.ControlError, launch.LaunchError, worktrees.WorktreeError, providers.ProviderError,
            keys.KeyError_) as e:
        print(f"omaorchestra: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
