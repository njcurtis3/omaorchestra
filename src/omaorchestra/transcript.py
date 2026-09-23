"""Facts about a session read from its agent transcript (Claude Code JSONL)."""

import json
import os
import re

TAIL_BYTES = 256 * 1024


def info(path, tail_bytes=TAIL_BYTES):
    """{"model": ..., "branch": ...} from the end of a transcript; None where unknown.

    Only the last `tail_bytes` are read, so a long session costs the same as a
    short one. The newest entry that names a model or branch wins.
    """
    found = {"model": None, "branch": None}
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - tail_bytes))
            chunk = f.read()
    except (OSError, TypeError, ValueError):
        return found
    lines = chunk.split(b"\n")
    if size > tail_bytes:
        lines = lines[1:]  # the first line is probably cut off
    for line in reversed(lines):
        if found["model"] and found["branch"]:
            break
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        message = entry.get("message")
        if not found["model"] and isinstance(message, dict) and isinstance(message.get("model"), str):
            if message["model"] != "<synthetic>":
                found["model"] = message["model"]
        # "HEAD" is what is recorded outside a repository or on a detached
        # checkout: no branch worth showing.
        if not found["branch"] and entry.get("gitBranch") not in (None, "", "HEAD") and isinstance(entry["gitBranch"], str):
            found["branch"] = entry["gitBranch"]
    return found


def model_name(model):
    """A readable name for a model id: "claude-opus-5-5" -> "Opus 5.5"."""
    if not model:
        return ""
    name = re.sub(r"^claude-", "", model)
    name = re.sub(r"-\d{8}$", "", name)  # dated snapshot suffix
    parts = name.split("-")
    words = [p for p in parts if not p.isdigit()]
    version = ".".join(p for p in parts if p.isdigit())
    if not words:
        return model
    return " ".join(w.capitalize() for w in words) + (f" {version}" if version else "")
