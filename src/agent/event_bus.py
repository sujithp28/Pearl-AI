"""
Structured event bus for Pearl V2.

Replaces ad-hoc string-keyed dicts in the SSE pipeline with typed
events that carry enough context for the UI to render meaningful cards.

Usage (inside executor/session)::

    bus = EventBus()
    bus.emit(PlanStartEvent(prompt="fix the bug", step_count=3))

    # Subscriber (SSE handler)
    for event in bus.drain():
        yield _sse(event["type"], event)

The bus is not a singleton — each autonomous run creates its own instance
so events never bleed across concurrent sessions.

Every event is a plain dict after serialization so the SSE layer needs
no special handling.
"""

from __future__ import annotations

import queue
import time
from dataclasses import asdict, dataclass, field
from typing import Any

# ── Base ────────────────────────────────────────────────────────────────────


@dataclass
class PearlEvent:
    type: str
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


# ── Typed events ─────────────────────────────────────────────────────────────


@dataclass
class SessionStartEvent(PearlEvent):
    type: str = "session_start"
    session_id: str = ""
    workspace: str = ""


@dataclass
class ContextCondensedEvent(PearlEvent):
    type: str = "context_condensed"
    turns_before: int = 0
    turns_after: int = 0
    tokens_before: int = 0
    tokens_after: int = 0
    reason: str = ""


@dataclass
class PlanStartEvent(PearlEvent):
    type: str = "plan_start"
    prompt: str = ""
    step_count: int = 0


@dataclass
class PlanStepEvent(PearlEvent):
    type: str = "plan_step"
    step_index: int = 0
    tool: str = ""
    description: str = ""


@dataclass
class PlanCompleteEvent(PearlEvent):
    type: str = "plan_complete"
    step_count: int = 0


@dataclass
class ToolStartEvent(PearlEvent):
    type: str = "tool_start"
    step: int = 0
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolSuccessEvent(PearlEvent):
    type: str = "tool_success"
    step: int = 0
    tool: str = ""
    summary: str = ""


@dataclass
class ToolFailureEvent(PearlEvent):
    type: str = "tool_failure"
    step: int = 0
    tool: str = ""
    error: str = ""
    error_type: str = ""


@dataclass
class ApprovalRequiredEvent(PearlEvent):
    type: str = "approval_required"
    files: list[str] = field(default_factory=list)
    diff_preview: str = ""


@dataclass
class CheckpointCreatedEvent(PearlEvent):
    type: str = "checkpoint_created"
    checkpoint_id: str = ""
    label: str = ""


@dataclass
class VerificationStartEvent(PearlEvent):
    type: str = "verification_start"
    files: list[str] = field(default_factory=list)


@dataclass
class VerificationCompleteEvent(PearlEvent):
    type: str = "verification_complete"
    status: str = ""
    tests_run: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    confidence: float = 0.0
    unexpected_files: list[str] = field(default_factory=list)


@dataclass
class ReflectionStartEvent(PearlEvent):
    type: str = "reflection_start"
    iteration: int = 0


@dataclass
class ReflectionCompleteEvent(PearlEvent):
    type: str = "reflection_complete"
    status: str = ""  # "complete" | "retry" | "replan" | "blocked"
    confidence: float = 0.0
    reason: str = ""
    iteration: int = 0


@dataclass
class ReplanStartEvent(PearlEvent):
    type: str = "replan_start"
    reason: str = ""
    attempt: int = 0


@dataclass
class RunCompleteEvent(PearlEvent):
    type: str = "run_complete"
    stop_reason: str = ""
    steps: int = 0
    replans: int = 0


@dataclass
class RunFailedEvent(PearlEvent):
    type: str = "run_failed"
    error: str = ""
    stop_reason: str = ""


@dataclass
class CancellationEvent(PearlEvent):
    type: str = "cancelled"
    message: str = "Run cancelled by user."


# ── Bus ──────────────────────────────────────────────────────────────────────


class EventBus:
    """
    Thread-safe, per-run structured event queue.

    emit()  → put a typed event in the queue
    drain() → iterate events (blocks until closed)
    close() → signal end-of-stream

    The bus is intentionally simple: a thin wrapper around queue.Queue so
    the executor can emit from its thread and the SSE handler can drain
    from the asyncio thread.
    """

    def __init__(self) -> None:
        self._q: queue.Queue[PearlEvent | None] = queue.Queue()

    def emit(self, event: PearlEvent) -> None:
        self._q.put(event)

    def close(self) -> None:
        self._q.put(None)  # sentinel

    def drain(self):
        """Yield events until close() is called."""
        while True:
            ev = self._q.get()
            if ev is None:
                return
            yield ev

    def drain_dicts(self):
        """Yield serialized dicts until close() is called."""
        for ev in self.drain():
            yield ev.to_dict()
