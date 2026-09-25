# From your phone

omaorchestra has two ways to reach you while you are away from the desk:

- **Pushes** tell you when an agent needs you or finishes (see
  [On your phone](guide.md#on-your-phone) and [away mode](guide.md#away-mode)).
- **`omaorchestra top` over SSH** lets you look and act: which agents are
  waiting and what they ask, answer a permission prompt (allow or deny that
  one request), dismiss or stop one, hand its work to another agent, and add
  to, pause or hold the queue.

This page is about the second: getting a terminal on this machine from a
phone, safely. omaorchestra opens no port itself and never will; SSH is the
system's, and the network is [Tailscale](https://tailscale.com), so nothing
is exposed to the internet.

```bash
omaorchestra setup --only remote    # checks all of the below, and changes nothing
```

## Tailscale first

Tailscale puts this machine and your phone on a private network (a
"tailnet") that only your devices can join, wherever they are.

1. On this machine: `omarchy-install-service-tailscale` (it installs
   Tailscale and runs `tailscale up`; log in when asked).
2. On the phone: install the Tailscale app and log in to the same account.
3. The phone can now reach the machine by its tailnet name (for example
   `desk`, or `desk.tail1234.ts.net`), shown by `omaorchestra setup --only
   remote`.

Then pick one of two ways in.

| | Tailscale SSH | sshd with a restricted key |
|---|---|---|
| Setting it up | one command | a few: config, firewall, key |
| Keys | none: your tailnet login is the key | one key made on the phone |
| What the phone can do | open a shell, then run `omaorchestra top` | run `omaorchestra top`, nothing else |
| A lost phone | remove it from the tailnet | remove it from the tailnet, and delete its key line |

## Option 1: Tailscale SSH

The quickest. Tailscale answers SSH on the tailnet address itself, so sshd
does not have to run and no firewall rule is needed.

```bash
sudo tailscale set --ssh
```

(Without `sudo` if your user is Tailscale's operator, as
`omarchy-install-service-tailscale` sets up.)

Who may log in is set by your tailnet's access rules. The default rules let
your own devices log in as your non-root users, and ask you to confirm in a
browser every so often ("check" mode). In your SSH app on the phone, add a
host with the machine's tailnet name and your user name, connect, and run
`omaorchestra top`.

The catch: whoever connects gets a full shell. If that is more than you want
a phone to have, use option 2.

## Option 2: sshd with a key that can only run top

The phone gets a key of its own that runs `omaorchestra top` and nothing
else: no shell, no other commands, no file copying, no port or agent
forwarding. Even someone holding the unlocked phone gets only what `top`
offers.

**1. Keys only.** Turn off password logins, so the only way in is a key:

```bash
echo 'PasswordAuthentication no' | sudo tee /etc/ssh/sshd_config.d/50-keys-only.conf
```

**2. Tailnet only.** Omarchy's firewall (ufw) drops incoming connections by
default. Let SSH in on the tailnet interface alone:

```bash
sudo ufw allow in on tailscale0 to any port 22 proto tcp
```

(Binding sshd to the tailnet address with `ListenAddress` works too, but
then sshd fails to start whenever it comes up before Tailscale. The firewall
rule has no such race.)

**3. Start sshd.**

```bash
sudo systemctl enable --now sshd
```

**4. The phone's key.** In your SSH app, create a new ed25519 key and copy
its *public* key (one line starting `ssh-ed25519`). On this machine:

```bash
omaorchestra remote ssh-key --add
```

and paste it. That appends one line to `~/.ssh/authorized_keys` (backed up
first):

```
command="/usr/bin/omaorchestra top",restrict,pty ssh-ed25519 AAAA... phone
```

`command` runs `top` whatever the phone asks for; `restrict` turns off
forwarding and the rest; `pty` gives the screen back. Without `--add` it only
prints the line, to add yourself.

**5. Connect.** In the SSH app, add a host with the machine's tailnet name,
your user name, and the new key. Connecting opens `top`; quitting it (**q**)
ends the session.

## Using top on a phone

`top` fits about 40 columns, so portrait works; see the
[guide](guide.md#in-a-terminal-and-on-your-phone) for its keys. Every key is
also a button along the bottom. Apps that pass taps through as mouse clicks
let you tap rows, tabs and buttons; in others, the keys work the same.
Typing is only needed for a new task.

Pair it with pushes in [away mode](guide.md#away-mode): the push says an
agent needs you, `top` shows what it asks, and **y** or **x** answers it
([how that works](guide.md#answering-permission-prompts-remotely)).

## If the phone is lost

1. Remove the phone from your tailnet in the Tailscale admin console
   (Machines). It can then reach nothing, whichever option you use.
2. With option 2, also delete its line from `~/.ssh/authorized_keys` (the
   one ending with the key's name).
3. Make a new ntfy topic, so the old one stops receiving pushes:
   `omaorchestra remote topic --new`, then subscribe to it on the new phone.

## Checking

`omaorchestra setup --only remote` reads the state of all of this and says
what, if anything, to change. It needs no root and changes nothing: not
sshd, not Tailscale, not the firewall. It reports:

- whether Tailscale is running, this machine's tailnet name, and whether
  Tailscale SSH is on
- whether sshd is running, where it listens, and whether it takes passwords
- whether the firewall lets SSH in, and on which networks
- how many keys can only run `top`, and how many can open a shell
