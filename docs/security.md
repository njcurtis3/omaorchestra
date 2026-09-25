# Security

What omaorchestra exposes, what leaves the machine, and what happens if
something goes wrong: a lost phone, a leaked push topic, other people on
your tailnet, or an agent turned against you.

## In short

- **No network listener.** The daemon talks over a Unix socket that only
  your user can open (mode 0600, in your private runtime folder). The app's
  single-instance socket is the same kind, in the same place. Tests check
  both the code and the running processes for open ports
  (`tests/test_no_ports.py`).
- **Outbound only, and only what you set up.** omaorchestra connects out for
  phone pushes (ntfy), your API providers (model lists, balance checks), MCP
  servers you ask it to check, and nothing else.
- **Secrets in the keyring.** Provider API keys, MCP secrets, and the ntfy
  topic and token live in the system keyring, never in config files or
  logs. A secret is never sent over plain http, except to this machine
  (localhost).
- **Private local state.** `~/.local/state/omaorchestra` holds your
  sessions (with what agents asked), the queue (task text), and the
  approvals record (commands agents asked to run). The daemon keeps that
  folder at mode 0700.
- **Agents stay in charge of themselves.** omaorchestra never approves
  anything on its own. It relays your answer, one request at a time, and
  only while you are away.

## Who it trusts

omaorchestra runs as you and trusts what runs as you. Any process of your
user can use the socket, as `omaorchestra` itself does: list sessions, queue
and start tasks, stop agents, answer permission prompts. That is no more
than such a process could do with your shell, so omaorchestra adds no
privilege, but it does not take any away either.

Agents run as you too. Two guards keep them from using omaorchestra in
passing:

- Its MCP server (`omaorchestra mcp serve`) only reads, apart from
  `queue_task`, which adds a task **paused** and notifies you: an agent can
  suggest work, never start it. Its `get_session` tool does show an agent
  the recent activity of your other sessions; that is what it is for, but
  it means one project's agent can read another's conversation.
- An answer to a permission prompt sent from inside an agent (a process
  under `claude`, `codex` or `opencode`, or with Claude Code's `CLAUDECODE`
  marker) is refused. The daemon identifies the sender by the socket's
  peer credentials, which a client cannot fake.

Neither is a wall. An agent that can run commands can do what you can,
including starting a detached process that is no longer under it. What
limits an agent is its own permission mode; keep that as tight as the work
allows.

## What leaves the machine

### Pushes

Only with `remote.push` on (it is off by default). Each push is one HTTPS
request to your ntfy server with a title and a message; what they say
depends on `remote.content`:

| Push | `minimal` (default) | `summary` adds | `full` adds |
|---|---|---|---|
| needs you | "website needs you" / "Waiting for your input" | the task's title | the agent's question (can quote commands and code) |
| finished | "website finished" / "Worked for 12m" | the task's title | |
| failed | "website: task failed to start" | the task's title | the error |
| usage limit | "Claude Code reached its usage limit" / which limit, and how full | | |
| queue blocked | "Queue is waiting" / the limit, or today's provider spend against your budget | | |

So even `minimal` names your project folders and, for a blocked queue,
your daily provider spend. Prompts and code leave the machine only at
`full`.

Around the text:

- ntfy has no end-to-end encryption. Whoever runs the server (ntfy.sh, or
  you) can read every push.
- ntfy.sh keeps messages for up to 12 hours, so a phone that was offline
  can catch up. Anyone who knows the topic can read them in that time.
- The server sees your machine's public address and when pushes happen.
- The optional access token goes in an `Authorization` header, over https
  only.

### Everything else

| Connection | When | Carries |
|---|---|---|
| Your API providers (Anthropic, OpenRouter, ...) | `models --refresh`, `provider test`, `spend`, and the agents you route through them | your API key; for routed agents, their whole conversation |
| MCP servers | `mcp check`, and the agents that use them | the handshake and a tool list; secrets from the keyring as configured |
| SSH (not omaorchestra's) | when you connect from your phone | see [From your phone](remote.md) |

## Threats

### A lost or stolen phone

What the phone can reach decides what someone holding it can do:

- **The ntfy app** shows pushes: at `minimal`, project names and what
  happened. Nothing in ntfy can act on your machine.
- **An SSH app with Tailscale SSH** opens a shell as you. That is
  everything.
- **An SSH app with the restricted key** opens only `omaorchestra top`. That
  is narrower than a shell, but not harmless: `top` can queue a task in any
  folder, and while you are away it can approve agents' permission prompts.
  Together those can run code on your machine.

Before it happens:

- Lock the phone, and turn on the SSH app's own passcode or biometric lock
  if it has one.
- Keep Tailscale's key expiry on for the phone, and "check" mode for
  Tailscale SSH, so access lapses on its own.
- If remote answers are more than you need, turn them off:
  `omaorchestra config set remote.answer_prompts false`.

When it happens, from any computer:

1. Remove the phone from your tailnet in the Tailscale admin console
   (Machines). It can reach nothing from then on.
2. With the restricted key, delete its line from `~/.ssh/authorized_keys`.
3. `omaorchestra away off`, so nothing waits for a remote answer.
4. `omaorchestra remote topic --new`, and subscribe to the new topic on
   your new phone.

### A leaked or guessed ntfy topic

The topic is the address, and on ntfy.sh the only lock. `remote topic --new`
makes a random one (about 100 bits), and it is kept in the keyring, never in
the config file or the log.

- **Reading**: someone with the topic sees what your pushes say, live and
  for up to 12 hours back. Keep `content` at `minimal` if that matters.
- **Writing**: someone with the topic can post to your phone, for example a
  fake "needs you" push. They cannot reach your machine this way:
  omaorchestra only ever sends to the topic, never reads from it. Its pushes
  never ask you to run anything; treat one that does as not from it.
- **Fixing it**: `omaorchestra remote topic --new`, then subscribe to the
  new topic. A self-hosted ntfy server can protect topics with accounts;
  store its token with `omaorchestra remote token`.

### Other people on your tailnet

omaorchestra listens on nothing, so the tailnet reaches it only through SSH.

- **Tailscale SSH**: who may connect, and as whom, is your tailnet policy.
  The default lets only your own devices in (`autogroup:self`), as your
  non-root users. If you share the tailnet, check the policy's `ssh` rules
  do not give others this machine.
- **sshd**: the firewall rule in [From your phone](remote.md) opens port 22
  to every device on the tailnet, not just yours. With keys only, that is a
  locked door rather than an open one; to narrow it further, allow port 22
  to this machine only from your own devices in the tailnet policy.
- Other services you open on `tailscale0` (Sunshine, for one) are reachable
  by the whole tailnet too.

### Other users on this machine

The daemon's socket is mode 0600 inside your runtime folder (0700), the
app's socket is in the same folder, and the state folder is 0700. The
config folder holds no secrets. What is left is your home folder's own
mode, which is 0700 on Arch.

### An agent turned against you

A prompt injection (in a web page, an issue, a file) can make an agent act
for someone else. omaorchestra cannot contain an agent; its permission mode
and sandbox do. What omaorchestra adds:

- no way to approve permission prompts in passing (refused from inside an
  agent), and no way to start work through its MCP server
- every remote answer recorded in `approvals.jsonl`, with where it came
  from (`omaorchestra permissions`)

## Known limits

- The refusal of answers from inside an agent is a guard, not a boundary
  (see above).
- Anything running as your user can use the socket.
- Pushes are readable by the ntfy server; there is no end-to-end
  encryption.
- The daemon's log (`journalctl --user -u omaorchestrad`) records session
  changes and, for each remote permission request, what was asked (for
  example `Bash: npm test`).
- Worktrees hold your code like any checkout; they are yours to protect
  like the repository they came from.

## Reporting a problem

Please report security problems privately, through **Report a
vulnerability** on the repository's Security tab, not in a public issue.
