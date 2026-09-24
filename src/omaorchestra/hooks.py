"""Hook events to session updates. The rules live in the agent adapters;
these names are kept for Claude Code, the first adapter."""

from .adapters import Claude

CLAUDE_EVENTS = Claude.events


def request_for(event, agent_process=None):
    return Claude().request_for(event, agent_process)
