"""Daemon logging: journal priorities under systemd, timestamps in a terminal.

Messages are an event name followed by key=value fields, so they grep well:
    session changed id=... status=working cwd=/home/u/proj
"""

import json
import logging
import os
import sys

logger = logging.getLogger("omaorchestra")

# syslog priorities, which journald reads from a "<N>" line prefix
PRIORITY = {logging.DEBUG: 7, logging.INFO: 6, logging.WARNING: 4, logging.ERROR: 3, logging.CRITICAL: 2}


class JournalFormatter(logging.Formatter):
    def format(self, record):
        return f"<{PRIORITY.get(record.levelno, 6)}>{record.getMessage()}"


def under_journal(env=None):
    """True when stderr goes to the journal (systemd sets JOURNAL_STREAM)."""
    return bool((os.environ if env is None else env).get("JOURNAL_STREAM"))


def setup(verbose=False, stream=None, journal=None):
    handler = logging.StreamHandler(stream or sys.stderr)
    if under_journal() if journal is None else journal:
        handler.setFormatter(JournalFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
    logger.handlers[:] = [handler]
    logger.propagate = False
    set_verbose(verbose)
    return logger


def set_verbose(verbose):
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)


def value(v):
    text = str(v)
    if text == "" or any(c.isspace() or c in '"=' for c in text):
        return json.dumps(text)
    return text


def fields(**kv):
    return " ".join(f"{k}={value(v)}" for k, v in kv.items() if v is not None)


def event(level, name, **kv):
    logger.log(level, f"{name} {fields(**kv)}".rstrip())
