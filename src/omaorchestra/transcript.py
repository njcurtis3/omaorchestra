"""Facts about a session read from its agent transcript (Claude Code JSONL)."""

import json
import os
import re

TAIL_BYTES = 256 * 1024


def info(path, tail_bytes=TAIL_BYTES):
    """{"model", "branch", "title"} from the end of a transcript; None where unknown.

    Only the last `tail_bytes` are read, so a long session costs the same as a
    short one. The newest entry that names a model or branch wins.
    """
    found = {"model": None, "branch": None, "title": None}
    for entry in reversed(_tail_entries(path, tail_bytes)):
        if all(found.values()):
            break
        if not found["title"] and entry.get("type") == "ai-title" and isinstance(entry.get("aiTitle"), str):
            found["title"] = entry["aiTitle"].strip() or None
        message = entry.get("message")
        if not found["model"] and isinstance(message, dict) and isinstance(message.get("model"), str):
            if message["model"] != "<synthetic>":
                found["model"] = message["model"]
        # "HEAD" is what is recorded outside a repository or on a detached
        # checkout: no branch worth showing.
        if not found["branch"] and entry.get("gitBranch") not in (None, "", "HEAD") and isinstance(entry["gitBranch"], str):
            found["branch"] = entry["gitBranch"]
    return found


def _tail_entries(path, tail_bytes):
    """The JSON objects in the last `tail_bytes` of a JSONL file, oldest first."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - tail_bytes))
            chunk = f.read()
    except (OSError, TypeError, ValueError):
        return []
    lines = chunk.split(b"\n")
    if size > tail_bytes:
        lines = lines[1:]  # the first line is probably cut off
    entries = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _short(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# The input field that best says what a tool call did.
TOOL_SUMMARY_KEYS = ("command", "file_path", "path", "pattern", "url", "query", "description", "prompt", "skill")


def tool_summary(name, tool_input):
    if isinstance(tool_input, dict):
        for key in TOOL_SUMMARY_KEYS:
            if isinstance(tool_input.get(key), str) and tool_input[key].strip():
                return f"{name}: {_short(tool_input[key], 160)}"
    return name


def activity(path, limit=40, tail_bytes=512 * 1024):
    """Recent prompts, replies and tool calls, oldest first.

    Each item is {"at": ISO timestamp or "", "kind": "prompt"|"reply"|"tool",
    "text": ...}. Tool results, thinking and subagent (sidechain) entries are
    left out: this is a glance at what the agent is doing, not a transcript.
    """
    items = []
    for entry in _tail_entries(path, tail_bytes):
        if entry.get("isSidechain") or entry.get("isMeta"):
            continue
        message = entry.get("message")
        if not isinstance(message, dict):
            continue
        at = entry.get("timestamp") or ""
        content = message.get("content")
        if entry.get("type") == "user":
            if isinstance(content, str) and content.strip() and not content.lstrip().startswith("<"):
                items.append({"at": at, "kind": "prompt", "text": _short(content, 300)})
            elif isinstance(content, list):
                text = " ".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
                if text.strip() and not text.lstrip().startswith("<"):
                    items.append({"at": at, "kind": "prompt", "text": _short(text, 300)})
        elif entry.get("type") == "assistant" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text", "").strip():
                    items.append({"at": at, "kind": "reply", "text": _short(block["text"], 300)})
                elif block.get("type") == "tool_use":
                    items.append({"at": at, "kind": "tool", "text": tool_summary(block.get("name", "tool"), block.get("input"))})
    return items[-limit:]


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
