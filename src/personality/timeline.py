"""
The plan-preview timeline stages, and the event each one describes.

This map lived as a private class attribute on `MCPServer`, back when
the VS Code extension was the only client with a plan preview. The web
plan preview needs the same stages, and a copy in the HTTP adapter would
be a second source of truth for wording that must match across clients:
two surfaces of one agent disagreeing about what Pearl is currently
doing.

Keys are the stage names a client renders. Values select the wording the
configured personality supplies for that stage.
"""

from __future__ import annotations

from src.personality.emoji import EventKind

TIMELINE_EVENT_KINDS: dict[str, EventKind] = {
    "planning": EventKind.PLANNING,
    "plan_ready": EventKind.PLAN_READY,
    "waiting_approval": EventKind.APPROVAL,
    "running_tool": EventKind.EXECUTING,
    "completed": EventKind.COMPLETED,
}
