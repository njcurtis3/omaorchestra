import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import (__version__, adapters, catalog, claude_settings, client, config, control, daemon, hooks, keys, launch,
               modeldefaults, procs, providers, remote, remote_access, service, setup, windows, worktrees)
from .mcp import registry as mcp_registry


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


def cmd_top(args):
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("omaorchestra: top needs a terminal", file=sys.stderr)
        return 1
    from . import top
    return top.run()


def cmd_app(args):
    from .app import main as app_main
    return app_main.run(check=args.check, session=args.session or "")


def cmd_run(args):
    result = launch.run(args.task, args.dir, permission_mode=args.permission_mode, model=args.model,
                        extra=args.agent_args, worktree=args.worktree, provider=args.provider,
                        mcp_profile=args.mcp_profile, agent=args.agent)
    record = result["worktree"]
    where = record["workdir"] if record else launch.Path(args.dir).expanduser().resolve()
    print(f"started {result['id'][:8]} in {where}" + (f", through {args.provider}" if args.provider else ""))
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
    if args.provider:  # fail now, not when the task's turn comes
        try:
            launch.routing.claude_code_env(providers.get(args.provider), args.model)
        except launch.routing.RoutingError as e:
            raise launch.LaunchError(str(e)) from e
    if args.mcp_profile and args.mcp_profile != mcp_registry.NONE_PROFILE \
            and args.mcp_profile not in mcp_registry.profiles():
        raise mcp_registry.McpError(f"no profile {args.mcp_profile}")
    item = {"task": args.task, "cwd": str(cwd), "model": args.model, "permission_mode": args.permission_mode,
            "worktree": args.worktree, "extra": args.agent_args, "provider": args.provider,
            "mcp_profile": args.mcp_profile, "agent": args.agent, **launch.agent_environment(args.agent)}
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


def cmd_spend(args):
    from . import spend
    r = spend.report(include_balances=not args.offline)
    print(json.dumps(r, indent=2) if args.json else spend.format_report(r))
    return 0


def known_projects():
    """Folders worth checking for a .mcp.json: sessions' and recent ones."""
    from . import recent
    folders = set(recent.load())
    try:
        folders |= {s.get("cwd") for s in client.request({"cmd": "list"})["sessions"] if s.get("cwd")}
    except client.DaemonUnavailable:
        pass
    return sorted(folders)


def cmd_mcp_list(args):
    from .mcp import inventory
    servers = inventory.everything(known_projects())
    if args.json:
        print(json.dumps(servers, indent=2))
        return 0
    if not servers:
        print("no MCP servers configured for Claude Code, Codex or opencode")
        return 0
    for s in servers:
        where = s["scope"] + (f" ({s['project']})" if s["project"] else "")
        what = s["url"] or " ".join([s["command"] or "?", *s["args"]])
        flags = [f for f, on in (("disabled", not s["enabled"]), ("omaorchestra", s["managed"])) if on]
        print(f"{s['agent']:<9} {s['name']:<20} {s['transport']:<6} {where:<30} {what}"
              + (f"  [{', '.join(flags)}]" if flags else ""))
        secrets = s["env_keys"] + s["header_keys"]
        if secrets:
            print(f"          uses: {', '.join(secrets)} (values not shown)")
    return 0


def cmd_mcp_add(args):
    import getpass
    from .mcp import registry
    env, headers, secret_values = {}, {}, {}
    for pair in args.env:
        key, sep, value = pair.partition("=")
        if not sep:
            raise registry.McpError(f"--env takes KEY=VALUE, not {pair}")
        env[key] = value
    for pair in args.header:
        key, sep, value = pair.partition(":")
        if not sep:
            raise registry.McpError(f"--header takes 'Name: value', not {pair}")
        headers[key.strip()] = value.strip()
    for key in args.secret_env + args.secret_header:
        secret_values[key] = sys.stdin.readline().strip() if args.secrets_stdin else \
            getpass.getpass(f"{key} for {args.name} (not shown; goes to the keyring): ")
    if args.url:
        spec = {"transport": args.transport or "http", "url": args.url, "headers": headers,
                "secret_headers": args.secret_header}
    else:
        if not args.server_command:
            raise registry.McpError("give the server's command after --, or --url")
        spec = {"transport": "stdio", "command": args.server_command[0], "args": args.server_command[1:],
                "env": env, "secret_env": args.secret_env}
    targets = [] if args.no_install else [registry.parse_target(t) for t in (args.agent or ["claude"])]
    registry.add(args.name, spec, targets, secret_values)
    print(f"added {args.name} to " + (", ".join(registry.target_label(t) for t in targets) or
                                      "omaorchestra only (for profiles)"))
    return 0


def cmd_mcp_managed(args):
    from .mcp import registry
    servers = registry.load()
    if not servers:
        print("omaorchestra manages no MCP servers (add one with `omaorchestra mcp add`)")
    for name, spec in servers.items():
        what = spec.get("url") or " ".join([spec["command"], *spec.get("args", [])])
        state = "enabled" if spec.get("enabled") else "disabled"
        secret = spec.get("secret_env", []) + spec.get("secret_headers", [])
        print(f"{name:<20} {spec['transport']:<6} {state:<9} {what}")
        print(f"                     in: {', '.join(registry.target_label(t) for t in spec['targets'])}"
              + (f"; secrets in keyring: {', '.join(secret)}" if secret else ""))
    return 0


def cmd_mcp_change(args):
    from .mcp import registry
    if args.mcp_command == "remove":
        registry.remove(args.name)
        print(f"removed {args.name} from its agents, and its secrets from the keyring")
    else:
        registry.set_enabled(args.name, args.mcp_command == "enable")
        print(f"{args.mcp_command}d {args.name}")
    return 0


def cmd_mcp_check(args):
    from .mcp import health, inventory
    entries = [e for e in inventory.everything(known_projects())
               if (not args.name or e["name"] == args.name) and (not args.agent or e["agent"] == args.agent)]
    if not entries:
        print("no matching MCP servers")
        return 1
    results = health.check_all(entries)
    failed = 0
    for e in entries:
        r = results[inventory.key(e)]
        where = f"{e['agent']}:{e['scope']}"
        if r["ok"]:
            tools = ", ".join(t["name"] for t in r["tools"][:8]) + (" …" if len(r["tools"]) > 8 else "")
            print(f"ok    {e['name']:<20} {where:<14} {r['server'] or ''} {r['version'] or ''}  "
                  f"{len(r['tools'])} tools ({r['seconds']}s): {tools}")
        else:
            failed += 1
            print(f"FAIL  {e['name']:<20} {where:<14} {r['error']}")
    return 1 if failed else 0


def cmd_mcp_profile(args):
    command = args.profile_command or "list"
    if command == "set":
        mcp_registry.set_profile(args.name, args.servers)
        print(f"profile {args.name}: {', '.join(args.servers) or '(no servers)'}")
    elif command == "remove":
        mcp_registry.remove_profile(args.name)
        print(f"removed profile {args.name}")
    else:
        print(f"{mcp_registry.NONE_PROFILE:<16} (built in: no MCP servers)")
        for name, members in mcp_registry.profiles().items():
            print(f"{name:<16} {', '.join(members) or '(no servers)'}")
    return 0


def cmd_handoff(args):
    sessions = client.request({"cmd": "list"})["sessions"]
    session = pick_session(sessions, args.session)
    if args.queue:
        from . import handoff as handing
        item = {"task": handing.brief(session), "cwd": handing.workdir(session), "model": args.model,
                "provider": args.provider, "worktree": False, "agent": args.agent or "claude", "extra": [],
                **launch.agent_environment(args.agent or "claude")}
        response = queue_request({"cmd": "queue-add", "item": item})
        print(f"queued a hand-off of {session['id'][:8]} as {response['item']['id'][:8]}")
        return 0
    response = client.request({"cmd": "handoff", "session_id": session["id"], "agent": args.agent,
                               "model": args.model, "provider": args.provider, "stop": args.stop,
                               "path": os.environ.get("PATH")}, timeout=30)
    if not response.get("ok"):
        raise launch.LaunchError(response.get("error"))
    print(f"handed {session['id'][:8]} to {response['agent']} as {response['session_id'][:8]}"
          + ("; stopped the old one" if response["stopped"] else ""))
    return 0


def cmd_permissions(args):
    from . import permissions
    data = permissions.everything(known_projects())
    if args.json:
        print(json.dumps(data, indent=2))
        return 0
    print("Claude Code")
    if not data["claude"]:
        print("  no settings files with rules")
    for src in data["claude"]:
        print(f"  {src['source']}  ({src['path']})")
        if src["default_mode"]:
            print(f"    default mode: {src['default_mode']}")
        for kind in ("allow", "ask", "deny"):
            plain = [r for r in src["rules"][kind] if not permissions.mcp_server_of(r)]
            if plain:
                print(f"    {kind}: {', '.join(plain)}")
        for server, rules in src["mcp_rules"].items():
            parts = [f"{k} {', '.join(r)}" for k, r in rules.items() if r]
            print(f"    MCP {server}: {'; '.join(parts)}")
        for key, value in src["mcp_lists"].items():
            print(f"    {key}: {', '.join(map(str, value))}")
        if not any(src["rules"].values()) and not src["mcp_lists"] and not src["default_mode"]:
            print("    (no permission rules)")
    for c in data["codex"]:
        print(f"Codex: approval policy {c['approval_policy'] or 'default'}, sandbox {c['sandbox_mode'] or 'default'}")
    for o in data["opencode"]:
        print(f"opencode: permission {json.dumps(o['permission'])}")
    print("Recent requests for your approval")
    if not data["record"]:
        print("  none recorded yet")
    for e in data["record"][:args.limit]:
        when = time.strftime("%m-%d %H:%M", time.localtime(e["at"]))
        waited = f" after {e['waited']}s" if e["waited"] is not None else ""
        print(f"  {when}  {launch.Path(e.get('project') or '').name:<18} {e['outcome']}{waited}: {e.get('message') or ''}")
    return 0


def cmd_mcp_serve(args):
    from .mcp import serve
    serve.serve()
    return 0


def cmd_mcp_exec(args):
    from .mcp import registry
    registry.exec_server(args.name)  # does not return
    return 1


def cmd_mcp_headers(args):
    from .mcp import registry
    print(registry.headers_json(args.name))
    return 0


def cmd_models_default(args):
    where = launch.Path(args.dir).expanduser().resolve() if args.dir else None
    if args.clear or args.model:
        if where:
            modeldefaults.set_for(where, "" if args.clear else args.model)
        else:
            config.save({"tasks": {"default_model": "" if args.clear else args.model}})
            reload_daemon(quiet_if_down=True)
    model, source = modeldefaults.for_folder(where or launch.Path.cwd())
    label = {"folder": "this folder's default", "global": "the global default"}.get(source, "the agent's own default")
    print(f"{model or '(none)'}  ({label})")
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
    # Called by agent hooks: never fail the agent, whatever happens, and never
    # block it; the one wait (a permission prompt while you are away) runs
    # beside the prompt, which stays answerable at the terminal.
    try:
        settings = config.load_or_defaults()
        if args.agent not in settings["agents"]["enabled"]:
            return 0
        adapter = adapters.get(args.agent)
        event = json.load(sys.stdin)
        # A task omaorchestra launched carries its placeholder id in the
        # environment, which hooks inherit: it ties the agent's own session
        # id to that placeholder.
        if isinstance(event, dict) and not event.get("launch_id"):
            event["launch_id"] = os.environ.get("OMAORCHESTRA_LAUNCH_ID") or None
        request = adapter.request_for(event, adapter.agent_process(event))
        if request and request["cmd"] == "update":
            request["path"] = os.environ.get("PATH")  # the daemon needs it to start other agents
        if request:
            client.request(request, timeout=0.5)
        if (adapter.answers_permissions and isinstance(event, dict)
                and event.get("hook_event_name") == "PermissionRequest"):
            from . import approvals
            answer = approvals.ask(event, settings["remote"])
            if answer:
                print(json.dumps(answer), flush=True)
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
    return claude_settings.hook_command(binary, getattr(args, "agent", "claude") or "claude")


AWAY_MODES = {"auto": "away once the screen locks, or after {after} without input",
              "on": "away until you switch back", "off": "at the desk until you switch back"}
AWAY_REASONS = {"on": "away", "locked": "away (the screen is locked)", "idle": "away (no input for {after})"}


def minutes(n):
    return f"{n} minute" if n == 1 else f"{n} minutes"


def away_now(state):
    """"away (the screen is locked) since 14:02", or "at the desk"."""
    if not state["away"]:
        return "at the desk"
    since = time.strftime("%H:%M", time.localtime(state["since"]))
    return AWAY_REASONS[state["reason"]].format(after=minutes(state["after"])) + f" since {since}"


def cmd_away(args):
    request = {"cmd": "away"}
    if args.mode:
        request["mode"] = args.mode
    try:
        response = client.request(request)
    except client.DaemonUnavailable:
        print("omaorchestrad is not running; away mode lives in the daemon", file=sys.stderr)
        return 1
    if not response.get("ok"):
        print(f"omaorchestra: {response.get('error')}", file=sys.stderr)
        return 1
    state = response["away"]
    if args.json:
        print(json.dumps(state))
        return 0
    print(f"mode  {state['mode']}: {AWAY_MODES[state['mode']].format(after=minutes(state['after']))}")
    print(f"now   {away_now(state)}")
    active = state.get("active", state["push"])
    if state["mode"] == "auto" and active:
        unknown = [what for what, key in (("the lock screen", "locked"), ("idle time", "idle"))
                   if state[key] is None]
        if unknown:
            print(f"cannot read {' or '.join(unknown)} yet; see `journalctl --user -u omaorchestrad`")
    if not active:
        print("push and remote answers are both off, so being away changes nothing: "
              "omaorchestra config set remote.push true")
    elif not state["push"]:
        print("push is off: while away, prompts can be answered remotely, but nothing is pushed "
              "(omaorchestra config set remote.push true)")
    return 0


def cmd_approvals(args):
    try:
        response = client.request({"cmd": "approvals"})
    except client.DaemonUnavailable:
        print("omaorchestrad is not running", file=sys.stderr)
        return 1
    items = response.get("approvals") or []
    if args.json:
        print(json.dumps(items, indent=2))
        return 0
    if not items:
        print("no permission prompts waiting for a remote answer")
        print("(prompts are answerable remotely only while you are away: omaorchestra away)")
        return 0
    now = time.time()
    for item in items:
        print(f"{item['id']}  {present_project(item['cwd']):<18} {int(now - item['asked'])}s  {item['summary']}")
    print("\nomaorchestra approve <id> allows that one request; omaorchestra deny <id> refuses it")
    return 0


def present_project(cwd):
    from .app import present
    return present.project(cwd)


def cmd_answer(args):
    from . import approvals
    behavior = "allow" if args.answer == "approve" else "deny"
    source = " ".join(filter(None, ["the command line", approvals.where_from()]))
    try:
        response = client.request({"cmd": "approval-answer", "id": args.id, "behavior": behavior,
                                   "message": getattr(args, "message", None), "source": source})
    except client.DaemonUnavailable:
        print("omaorchestrad is not running", file=sys.stderr)
        return 1
    if not response.get("ok"):
        print(f"omaorchestra: {response.get('error')}", file=sys.stderr)
        return 1
    item = response["approval"]
    print(f"{'allowed' if behavior == 'allow' else 'denied'}: {item['summary']} ({present_project(item['cwd'])})")
    return 0


def cmd_remote_status(args):
    settings = config.load()["remote"]
    topic, token = keys.lookup_remote("topic"), keys.lookup_remote("token")
    try:
        state = client.request({"cmd": "away"}).get("away")
    except client.DaemonUnavailable:
        state = None
    print(f"push     {'on' if settings['push'] else 'off'}")
    print(f"server   {settings['server']}")
    print(f"topic    {'stored in the keyring' if topic else 'none yet (omaorchestra remote topic --new)'}")
    print(f"token    {'stored in the keyring' if token else 'none'}")
    print(f"content  {settings['content']}")
    print(f"events   {', '.join(settings['events']) or 'none'}")
    away = f"{state['mode']}, {away_now(state)}" if state else "unknown (is omaorchestrad running?)"
    print(f"away     {away}")
    if topic and not settings["push"]:
        print("turn it on with: omaorchestra config set remote.push true")
    return 0


def cmd_remote_topic(args):
    if args.clear:
        keys.clear_remote("topic")
        print("forgot the topic")
        return 0
    if args.show:
        topic = keys.lookup_remote("topic")
        if not topic:
            print("no topic yet; make one with `omaorchestra remote topic --new`", file=sys.stderr)
            return 1
        print(topic.strip())
        return 0
    if args.new:
        topic = remote.new_topic()
    elif args.stdin:
        topic = sys.stdin.read()
    else:
        import getpass
        topic = getpass.getpass("ntfy topic (not shown): ")
    topic = topic.strip()
    if not topic or any(c.isspace() or c == "/" for c in topic) or len(topic) > 64:
        raise remote.RemoteError("a topic is up to 64 characters, with no spaces or slashes")
    keys.store_remote("topic", topic)
    print("stored the topic in the system keyring")
    if args.new:
        server = config.load()["remote"]["server"]
        print(f"\nsubscribe to it in the ntfy app on your phone:\n  server {server}\n  topic  {topic}\n"
              "\nanyone who knows the topic can read what is sent to it; keep it private.")
    return 0


def cmd_remote_token(args):
    if args.clear:
        keys.clear_remote("token")
        print("forgot the token")
        return 0
    if args.stdin:
        token = sys.stdin.read()
    else:
        import getpass
        token = getpass.getpass("ntfy access token (not shown): ")
    keys.store_remote("token", token)
    print("stored the token in the system keyring")
    return 0


def cmd_remote_test(args):
    settings = config.load()["remote"]
    remote.send(settings, "needs-you", "omaorchestra test", "Push notifications work.")
    print(f"sent a test notification through {settings['server']}")
    if not settings["push"]:
        print("push is off, so agents will not send any yet: omaorchestra config set remote.push true")
    return 0


def cmd_remote_ssh_key(args):
    binary = own_binary()
    if not binary:
        raise remote_access.RemoteAccessError("cannot find the omaorchestra binary to put in the key's command")
    if args.key:
        try:
            text = Path(args.key).expanduser().read_text()
        except OSError as e:
            raise remote_access.RemoteAccessError(f"cannot read {args.key}: {e.strerror}") from None
    else:
        if sys.stdin.isatty():
            print("paste the phone's public key (one line, starting ssh-ed25519 or similar), then Enter:",
                  file=sys.stderr)
            text = sys.stdin.readline()
        else:
            text = sys.stdin.read()
    line = remote_access.key_line(binary, text, args.comment)
    if not args.add:
        print(line)
        print("\nadd that line to ~/.ssh/authorized_keys (or run this again with --add); the key can then only "
              "run `omaorchestra top`", file=sys.stderr)
        return 0
    if remote_access.add_key(line):
        print(f"added to {remote_access.authorized_keys_path()}; this key can only run `omaorchestra top`")
    else:
        print(f"that key is already in {remote_access.authorized_keys_path()}; left it as it is")
    return 0


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
    adapter = adapters.get(args.agent)
    if adapter.name == "opencode":
        print(adapters.PLUGIN.replace("__COMMAND__", json.dumps(shlex.split(hook_command(args)))))
    else:
        print(json.dumps(claude_settings.install({}, hook_command(args), adapter.events, adapter.hook_timeouts), indent=2))
    return 0


def change_settings(args, change):
    path = Path(args.settings) if args.settings else adapters.get(args.agent)._path(None)
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


def cmd_setup(args):
    for line in setup.setup(own_binary(), only=args.only or (), skip=args.skip or (), dry_run=args.dry_run):
        print(line)
    if not args.dry_run and list(args.only or ()) != ["remote"]:
        print("done: `omaorchestra app` opens the app, and `omaorchestra teardown` undoes this")
    return 0


def cmd_teardown(args):
    for line in setup.teardown(only=args.only or (), skip=args.skip or (), dry_run=args.dry_run) or ["nothing to do"]:
        print(line)
    for line in setup.leftovers():
        print(line)
    return 0


def cmd_hooks_install(args):
    adapter = adapters.get(args.agent)
    command = hook_command(args)
    if adapter.name == "opencode":
        if args.dry_run:
            return cmd_hooks_snippet(args)
        written = adapter.install_hooks(command, args.settings)
        print(f"wrote {written}" if written else "already up to date")
        return 0
    result = change_settings(args, lambda s: claude_settings.install(s, command, adapter.events, adapter.hook_timeouts))
    if adapter.name == "codex" and not args.dry_run:
        print("Codex runs these hooks only once you trust them: start codex and use /hooks.")
    return result


def cmd_hooks_uninstall(args):
    adapter = adapters.get(args.agent)
    if adapter.name == "opencode":
        removed = adapter.uninstall_hooks(args.settings)
        print(f"removed {removed}" if removed else "not installed")
        return 0
    return change_settings(args, claude_settings.remove)


def cmd_hooks_status(args):
    adapter = adapters.get(args.agent)
    installed, detail = adapter.hooks_status(args.settings)
    path = args.settings or adapter._path(None)
    print(("installed in " if installed else "not installed in ") + str(path) + (f" ({detail})" if detail else ""))
    return 0 if installed else 1


def build_parser():
    """Every command and option (also read by scripts/commands for docs/commands.md)."""
    parser = argparse.ArgumentParser(prog="omaorchestra", description="Agent coordinator for Omarchy")
    parser.add_argument("--version", action="version", version=f"omaorchestra {__version__}")
    sub = parser.add_subparsers(dest="subcommand")

    daemon_p = sub.add_parser("daemon", help="run the coordinator daemon")
    daemon_p.add_argument("-v", "--verbose", action="store_true", help="log every request")
    daemon_p.set_defaults(func=cmd_daemon)
    sub.add_parser("ping", help="check whether the daemon is running").set_defaults(func=cmd_ping)
    ls = sub.add_parser("ls", help="list agent sessions")
    ls.add_argument("--json", action="store_true", help="machine-readable output")
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
    sub.add_parser("top", help="sessions and the queue in the terminal, sized for a phone over SSH; "
                               "keys or taps").set_defaults(func=cmd_top)
    run_p = sub.add_parser("run", help="start an agent on a task in a new terminal window")
    run_p.add_argument("task", help="what the agent should do")
    run_p.add_argument("--in", dest="dir", default=".", help="folder to work in (default: here)")
    run_p.add_argument("--model", help="model to use, passed to the agent")
    run_p.add_argument("--permission-mode", help="the agent's permission mode (default: its own setting)")
    run_p.add_argument("--worktree", dest="worktree", action="store_true", default=None,
                       help="work in a separate git worktree (default: tasks.isolate_with_worktrees)")
    run_p.add_argument("--no-worktree", dest="worktree", action="store_false", help="work in the folder itself")
    run_p.add_argument("--provider", help="run through this API provider instead of the subscription")
    run_p.add_argument("--mcp-profile", help="only this profile's MCP servers ('none' for none)")
    run_p.add_argument("--agent", default="claude", choices=list(adapters.ADAPTERS), help="which agent (default claude)")
    run_p.set_defaults(func=cmd_run, agent_args=[])
    run_p.epilog = "Anything after -- is passed to the agent as is."
    qp = sub.add_parser("queue", help="tasks waiting for a free agent slot")
    qp.set_defaults(func=cmd_queue_list)
    q_sub = qp.add_subparsers(dest="queue_command")
    q_sub.add_parser("list", help="the queue and how many slots are busy").set_defaults(func=cmd_queue_list)
    qa = q_sub.add_parser("add", help="queue a task (anything after -- goes to the agent)")
    qa.add_argument("task", help="what the agent should do")
    qa.add_argument("--in", dest="dir", default=".", help="folder to work in (default: here)")
    qa.add_argument("--model", help="model to use, passed to the agent")
    qa.add_argument("--permission-mode", help="the agent's permission mode (default: its own setting)")
    qa.add_argument("--worktree", dest="worktree", action="store_true", default=None,
                    help="work in a separate git worktree (default: tasks.isolate_with_worktrees)")
    qa.add_argument("--no-worktree", dest="worktree", action="store_false", help="work in the folder itself")
    qa.add_argument("--paused", action="store_true", help="add it paused")
    qa.add_argument("--provider", help="run through this API provider instead of the subscription")
    qa.add_argument("--mcp-profile", help="only this profile's MCP servers ('none' for none)")
    qa.add_argument("--agent", default="claude", choices=list(adapters.ADAPTERS), help="which agent (default claude)")
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
    mv.add_argument("id", help="queued task id or prefix")
    mv.add_argument("position", type=int, help="its new place; 1 runs next")
    mv.set_defaults(func=cmd_queue_move)
    for name in ("up", "down"):
        p = q_sub.add_parser(name, help=f"move a task one place {name}")
        p.add_argument("id", help="queued task id or prefix")
        p.set_defaults(func=cmd_queue_move)

    ho = sub.add_parser("handoff", help="start another agent (or model) on a session's work, with a brief")
    ho.add_argument("session", help="session id or prefix")
    ho.add_argument("--agent", choices=list(adapters.ADAPTERS), help="default: tasks.fallback_agent, else claude")
    ho.add_argument("--model", help="model for the new agent (default: its own)")
    ho.add_argument("--provider", help="run the new agent through this API provider")
    ho.add_argument("--queue", action="store_true", help="queue it instead of starting it now")
    ho.add_argument("--stop", action="store_true", help="stop the old session once the new one is started")
    ho.set_defaults(func=cmd_handoff)
    pm = sub.add_parser("permissions", help="what agents may do without asking, and what they asked")
    pm.add_argument("--json", action="store_true", help="machine-readable output")
    pm.add_argument("--limit", type=int, default=20, help="how many recent requests to show")
    pm.set_defaults(func=cmd_permissions)
    sp = sub.add_parser("spend", help="provider spend today, subscription limits, and what sessions cost")
    sp.add_argument("--json", action="store_true", help="machine-readable output")
    sp.add_argument("--offline", action="store_true", help="do not ask providers for their balances")
    sp.set_defaults(func=cmd_spend)
    mcp_p = sub.add_parser("mcp", help="MCP servers across Claude Code, Codex and opencode")
    mcp_sub = mcp_p.add_subparsers(dest="mcp_command", required=True)
    ml = mcp_sub.add_parser("list", help="every configured MCP server, per agent and scope")
    ml.add_argument("--json", action="store_true", help="machine-readable output")
    ml.set_defaults(func=cmd_mcp_list)
    ma = mcp_sub.add_parser("add", help="add a server to agents, its secrets to the keyring "
                                        "(stdio: the command after --; HTTP: --url)")
    ma.add_argument("name", help="a name for the server")
    ma.add_argument("--agent", action="append",
                    help="claude[:user|:local:/project], codex, opencode (repeatable; default claude)")
    ma.add_argument("--url", help="an HTTP server's URL")
    ma.add_argument("--transport", choices=["http", "sse"], help="for --url (default http)")
    ma.add_argument("--env", action="append", default=[], help="KEY=VALUE for a stdio server (not secret)")
    ma.add_argument("--secret-env", action="append", default=[], help="KEY whose value is asked for and kept in the keyring")
    ma.add_argument("--header", action="append", default=[], help="'Name: value' for an HTTP server (not secret)")
    ma.add_argument("--secret-header", action="append", default=[], help="header whose value is asked for and kept in the keyring")
    ma.add_argument("--secrets-stdin", action="store_true", help="read secret values from stdin, one per line")
    ma.add_argument("--no-install", action="store_true", help="keep it for profiles only; install in no agent")
    ma.set_defaults(func=cmd_mcp_add, server_command=[])
    mcp_sub.add_parser("managed", help="servers omaorchestra manages").set_defaults(func=cmd_mcp_managed)
    mc = mcp_sub.add_parser("check", help="start servers, do the MCP handshake, list their tools")
    mc.add_argument("name", nargs="?", help="only this server")
    mc.add_argument("--agent", choices=["claude", "codex", "opencode"], help="only this agent's servers")
    mc.set_defaults(func=cmd_mcp_check)
    mcp_sub.add_parser("serve", help="omaorchestra's own MCP server, over stdio (for agents)").set_defaults(func=cmd_mcp_serve)
    mpr = mcp_sub.add_parser("profile", help="named sets of managed servers to start tasks with")
    mpr.set_defaults(func=cmd_mcp_profile, profile_command=None)
    mpr_sub = mpr.add_subparsers(dest="profile_command")
    mpr_sub.add_parser("list", help="every profile and its servers").set_defaults(func=cmd_mcp_profile)
    ps = mpr_sub.add_parser("set", help="create or replace a profile")
    ps.add_argument("name", help="profile name")
    ps.add_argument("servers", nargs="*", help="managed servers in it (none: an empty profile)")
    ps.set_defaults(func=cmd_mcp_profile)
    pr = mpr_sub.add_parser("remove", help="delete a profile (its servers stay)")
    pr.add_argument("name", help="profile name")
    pr.set_defaults(func=cmd_mcp_profile)
    for name, text in (("remove", "remove from its agents and forget it, secrets too"),
                       ("enable", "install it in its agents again"), ("disable", "take it out of its agents, keep it here")):
        p = mcp_sub.add_parser(name, help=text)
        p.add_argument("name", help="a managed server's name")
        p.set_defaults(func=cmd_mcp_change)
    for name, func, text in (("exec", cmd_mcp_exec, "start a managed stdio server with its secrets (agents run this)"),
                             ("headers", cmd_mcp_headers, "print a managed HTTP server's headers (Claude's headersHelper)")):
        p = mcp_sub.add_parser(name, help=text)
        p.add_argument("name", help="a managed server's name")
        p.set_defaults(func=func)
    pp = sub.add_parser("provider", help="model providers (API keys live in the system keyring)")
    p_sub = pp.add_subparsers(dest="provider_command", required=True)
    p_sub.add_parser("list", help="configured providers").set_defaults(func=cmd_provider_list)
    pa = p_sub.add_parser("add", help="add a provider: " + ", ".join(providers.KINDS))
    pa.add_argument("kind", choices=list(providers.KINDS), help="which service")
    pa.add_argument("--id", help="a name for it (default: the kind)")
    pa.add_argument("--base-url", help="a different endpoint (a proxy, a self-hosted Ollama, ...)")
    pa.set_defaults(func=cmd_provider_add)
    pk = p_sub.add_parser("key", help="store its API key in the system keyring")
    pk.add_argument("id", help="the provider's id (see `provider list`)")
    pk.add_argument("--stdin", action="store_true", help="read the key from standard input")
    pk.set_defaults(func=cmd_provider_key)
    for name, func, text in (("remove", cmd_provider_remove, "forget it and its key"),
                             ("test", cmd_provider_test, "check it answers and accepts the key")):
        p = p_sub.add_parser(name, help=text)
        p.add_argument("id", help="the provider's id (see `provider list`)")
        p.set_defaults(func=func)
    mp = sub.add_parser("models", help="models from Claude Code and your providers")
    mp.add_argument("--provider", help="only this provider")
    mp.add_argument("--refresh", action="store_true", help="fetch the lists again")
    mp.add_argument("--json", action="store_true", help="machine-readable output")
    mp.set_defaults(func=cmd_models)
    m_sub = mp.add_subparsers(dest="models_command")
    md = m_sub.add_parser("default", help="show or set the model tasks use when they name none")
    md.add_argument("model", nargs="?", help="a model or alias (omit to show the current default)")
    md.add_argument("--for", dest="dir", help="set it for this folder (and the folders inside it)")
    md.add_argument("--clear", action="store_true", help="remove the default")
    md.set_defaults(func=cmd_models_default)

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
    hook.add_argument("agent", choices=list(adapters.ADAPTERS), help="the agent sending the event")
    hook.set_defaults(func=cmd_hook)

    hooks_cmd = sub.add_parser("hooks", help="manage the hooks agents report to omaorchestra with")
    hooks_sub = hooks_cmd.add_subparsers(dest="hooks_command", required=True)
    for name, func, text in (
        ("install", cmd_hooks_install, "add the hooks to Claude Code's settings.json"),
        ("uninstall", cmd_hooks_uninstall, "remove the hooks, leaving other settings alone"),
        ("status", cmd_hooks_status, "show whether the hooks are installed"),
        ("snippet", cmd_hooks_snippet, "print the hooks block without writing anything"),
    ):
        p = hooks_sub.add_parser(name, help=text)
        p.set_defaults(func=func)
        p.add_argument("--agent", default="claude", choices=list(adapters.ADAPTERS),
                       help="which agent (default claude)")
        if name != "snippet":
            p.add_argument("--settings", help="settings.json to edit (default: ~/.claude/settings.json)")
        if name in ("install", "uninstall"):
            p.add_argument("--dry-run", action="store_true", help="print the result instead of writing it")
        if name in ("install", "snippet"):
            p.add_argument("--command", dest="hook_command", help="hook command to use (default: this omaorchestra, by absolute path)")

    remote_cmd = sub.add_parser("remote", help="push notifications to your phone (ntfy)")
    remote_sub = remote_cmd.add_subparsers(dest="remote_command")
    remote_cmd.set_defaults(func=cmd_remote_status)
    remote_sub.add_parser("status", help="how pushes are set up").set_defaults(func=cmd_remote_status)
    rt = remote_sub.add_parser("topic", help="set the ntfy topic (kept in the system keyring)")
    how = rt.add_mutually_exclusive_group()
    how.add_argument("--new", action="store_true", help="make up a hard-to-guess topic and print it")
    how.add_argument("--stdin", action="store_true", help="read the topic from standard input")
    how.add_argument("--show", action="store_true", help="print the stored topic")
    how.add_argument("--clear", action="store_true", help="forget the topic")
    rt.set_defaults(func=cmd_remote_topic)
    rk = remote_sub.add_parser("token", help="set an ntfy access token, for protected topics")
    how = rk.add_mutually_exclusive_group()
    how.add_argument("--stdin", action="store_true", help="read the token from standard input")
    how.add_argument("--clear", action="store_true", help="forget the token")
    rk.set_defaults(func=cmd_remote_token)
    remote_sub.add_parser("test", help="send one test notification now").set_defaults(func=cmd_remote_test)
    rs = remote_sub.add_parser("ssh-key", help="an authorized_keys line that lets a key run only `omaorchestra "
                                               "top` (for a phone; see docs/remote.md)")
    rs.add_argument("key", nargs="?", help="the public key file (default: read it from standard input)")
    rs.add_argument("--add", action="store_true", help="append it to ~/.ssh/authorized_keys (backed up first)")
    rs.add_argument("--comment", help="a name for the key in authorized_keys (default: the key's own comment)")
    rs.set_defaults(func=cmd_remote_ssh_key)
    ap = sub.add_parser("approvals", help="permission prompts waiting for a remote answer (while you are away)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.set_defaults(func=cmd_approvals)
    for name, text in (("approve", "allow one waiting permission prompt (just this request)"),
                       ("deny", "refuse one waiting permission prompt")):
        p = sub.add_parser(name, help=text)
        p.add_argument("id", help="the request's id from `omaorchestra approvals` (or a prefix)")
        if name == "deny":
            p.add_argument("--message", help="what to tell the agent (default: that you denied it remotely)")
        p.set_defaults(func=cmd_answer, answer=name)
    away_p = sub.add_parser("away", help="whether you are away (pushes and remote answers happen only then): "
                                        "show or set the mode")
    away_p.add_argument("mode", nargs="?", choices=["auto", "on", "off"],
                        help="auto: away when locked or idle (default); on: away; off: at the desk")
    away_p.add_argument("--json", action="store_true", help="machine-readable output")
    away_p.set_defaults(func=cmd_away)

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

    for name, helper, func in (("setup", "wire omaorchestra into this desktop: service, hooks, bar widget, "
                                         "keybindings, menu", cmd_setup),
                               ("teardown", "undo setup (keeps settings, state, worktrees and keys)", cmd_teardown)):
        step_p = sub.add_parser(name, help=helper)
        step_p.add_argument("--only", action="append", choices=setup.STEPS, metavar="STEP",
                            help=f"only this step (repeatable): {', '.join(setup.STEPS)}")
        step_p.add_argument("--skip", action="append", choices=setup.STEPS, metavar="STEP", help="skip this step (repeatable)")
        step_p.add_argument("--dry-run", action="store_true", help="show what would be done")
        step_p.set_defaults(func=func)

    return parser


def main(argv=None):
    parser = build_parser()
    # `run ... -- <agent args>`: split them off first, since argparse would
    # otherwise mix them up with run's own options.
    argv = list(sys.argv[1:] if argv is None else argv)
    agent_args = []
    if argv[:2] == ["mcp", "add"] and "--" in argv:
        split = argv.index("--")
        argv, server_command = argv[:split], argv[split + 1:]
        args = parser.parse_args(argv)
        args.server_command = server_command
        return run_command(args)
    if (argv[:1] == ["run"] or argv[:2] == ["queue", "add"]) and "--" in argv:
        split = argv.index("--")
        argv, agent_args = argv[:split], argv[split + 1:]
    args = parser.parse_args(argv)
    if agent_args:
        args.agent_args = agent_args
    return run_command(args, parser)


def run_command(args, parser=None):
    if not getattr(args, "func", None):
        if parser:
            parser.print_help()
        return 0
    try:
        return args.func(args)
    except (claude_settings.SettingsError, service.ServiceError, config.ConfigError, windows.WindowError,
            control.ControlError, launch.LaunchError, worktrees.WorktreeError, providers.ProviderError,
            keys.KeyError_, mcp_registry.McpError, setup.SetupError, remote.RemoteError,
            remote_access.RemoteAccessError) as e:
        print(f"omaorchestra: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
