"""API keys in the system keyring (Secret Service, via secret-tool).

Keys never touch config files, logs, the queue, or this repository. Each is
stored with the attributes service=omaorchestra, provider=<provider id>;
remote push secrets (the ntfy topic and token) with service=omaorchestra,
remote=<name>.
"""

import subprocess

SERVICE = "omaorchestra"


class KeyError_(Exception):
    pass


def _run(args, stdin=None):
    try:
        return subprocess.run(["secret-tool", *args], input=stdin, capture_output=True, text=True, timeout=15)
    except FileNotFoundError as e:
        raise KeyError_("secret-tool is not installed (package libsecret)") from e
    except subprocess.TimeoutExpired as e:
        raise KeyError_("the keyring did not answer; is it unlocked?") from e


def _store(attribute, name, secret, label):
    if not secret.strip():
        raise KeyError_(f"the {label.rsplit(' ', 1)[-1]} is empty")
    result = _run(["store", f"--label=omaorchestra: {label}", "service", SERVICE, attribute, name],
                  stdin=secret.strip())
    if result.returncode != 0:
        raise KeyError_(f"could not store it: {result.stderr.strip() or 'keyring refused'}")


def _lookup(attribute, name):
    try:
        result = _run(["lookup", "service", SERVICE, attribute, name])
    except KeyError_:
        return None
    return result.stdout if result.returncode == 0 and result.stdout else None


def store(provider_id, key):
    _store("provider", provider_id, key, f"{provider_id} API key")


def lookup(provider_id):
    """The key, or None when there is none (or no keyring)."""
    return _lookup("provider", provider_id)


def clear(provider_id):
    _run(["clear", "service", SERVICE, "provider", provider_id])


def store_remote(name, secret):
    """`name` is "topic" or "token"."""
    _store("remote", name, secret, f"push notification {name}")


def lookup_remote(name):
    return _lookup("remote", name)


def clear_remote(name):
    _run(["clear", "service", SERVICE, "remote", name])
