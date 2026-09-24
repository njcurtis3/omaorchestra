"""API keys in the system keyring (Secret Service, via secret-tool).

Keys never touch config files, logs, the queue, or this repository. Each is
stored with the attributes service=omaorchestra, provider=<provider id>.
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


def store(provider_id, key):
    if not key.strip():
        raise KeyError_("the key is empty")
    result = _run(["store", f"--label=omaorchestra: {provider_id} API key", "service", SERVICE,
                   "provider", provider_id], stdin=key.strip())
    if result.returncode != 0:
        raise KeyError_(f"could not store the key: {result.stderr.strip() or 'keyring refused'}")


def lookup(provider_id):
    """The key, or None when there is none (or no keyring)."""
    try:
        result = _run(["lookup", "service", SERVICE, "provider", provider_id])
    except KeyError_:
        return None
    return result.stdout if result.returncode == 0 and result.stdout else None


def clear(provider_id):
    _run(["clear", "service", SERVICE, "provider", provider_id])
