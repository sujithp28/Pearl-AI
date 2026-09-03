"""
Tests for src/agent/event_bus.py
"""
from __future__ import annotations

import threading

import pytest

from src.agent.event_bus import (
    ApprovalRequiredEvent,
    CancellationEvent,
    CheckpointCreatedEvent,
    ContextCondensedEvent,
    EventBus,
    PlanStartEvent,
    ReflectionCompleteEvent,
    ReflectionStartEvent,
    ReplanStartEvent,
    RunCompleteEvent,
    RunFailedEvent,
    SessionStartEvent,
    ToolFailureEvent,
    ToolStartEvent,
    ToolSuccessEvent,
    VerificationCompleteEvent,
    VerificationStartEvent,
)


def _drain(bus: EventBus) -> list:
    """Close then collect all events."""
    bus.close()
    return list(bus.drain())


# ---------------------------------------------------------------------------
# EventBus basics
# ---------------------------------------------------------------------------


class TestEventBusEmitAndDrain:
    def test_emit_single_event(self) -> None:
        bus = EventBus()
        bus.emit(PlanStartEvent(step_count=3))
        events = _drain(bus)
        assert len(events) == 1
        assert events[0].step_count == 3

    def test_drain_returns_empty_on_empty_bus(self) -> None:
        bus = EventBus()
        assert _drain(bus) == []

    def test_emit_multiple_events_preserves_order(self) -> None:
        bus = EventBus()
        bus.emit(PlanStartEvent(step_count=1))
        bus.emit(ToolStartEvent(step=0, tool="read_file"))
        bus.emit(ToolSuccessEvent(step=0, tool="read_file"))
        events = _drain(bus)
        assert len(events) == 3
        assert isinstance(events[0], PlanStartEvent)
        assert isinstance(events[1], ToolStartEvent)
        assert isinstance(events[2], ToolSuccessEvent)

    def test_drain_stops_at_close_sentinel(self) -> None:
        bus = EventBus()
        bus.emit(PlanStartEvent(step_count=2))
        bus.close()
        # After sentinel any new emits are queued but not seen by this drain
        bus.emit(PlanStartEvent(step_count=99))
        events = list(bus.drain())
        assert len(events) == 1
        assert events[0].step_count == 2

    def test_drain_dicts_returns_serializable_data(self) -> None:
        bus = EventBus()
        bus.emit(PlanStartEvent(step_count=5))
        bus.close()
        dicts = list(bus.drain_dicts())
        assert len(dicts) == 1
        d = dicts[0]
        assert isinstance(d, dict)
        assert d["step_count"] == 5
        assert d["type"] == "plan_start"

    def test_emit_is_thread_safe(self) -> None:
        bus = EventBus()
        errors: list[Exception] = []

        def _emit_many() -> None:
            try:
                for i in range(50):
                    bus.emit(ToolStartEvent(step=i, tool=f"tool_{i}"))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_emit_many) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        events = _drain(bus)
        assert len(events) == 200  # 4 threads * 50 events

    def test_to_dict_includes_type_and_ts(self) -> None:
        event = PlanStartEvent(step_count=3)
        d = event.to_dict()
        assert "type" in d
        assert "ts" in d
        assert d["type"] == "plan_start"


# ---------------------------------------------------------------------------
# Specific event types
# ---------------------------------------------------------------------------


class TestEventTypes:
    def test_tool_failure_event(self) -> None:
        bus = EventBus()
        bus.emit(ToolFailureEvent(step=1, tool="write_file", error="Permission denied"))
        events = _drain(bus)
        e = events[0]
        assert isinstance(e, ToolFailureEvent)
        assert e.error == "Permission denied"

    def test_approval_required_event(self) -> None:
        bus = EventBus()
        bus.emit(ApprovalRequiredEvent(files=["a.py", "b.py"], diff_preview="--- a\n+++ b"))
        events = _drain(bus)
        e = events[0]
        assert isinstance(e, ApprovalRequiredEvent)
        assert "a.py" in e.files

    def test_run_complete_event(self) -> None:
        bus = EventBus()
        bus.emit(RunCompleteEvent(stop_reason="completed", steps=3))
        events = _drain(bus)
        e = events[0]
        assert isinstance(e, RunCompleteEvent)
        assert e.stop_reason == "completed"

    def test_run_failed_event(self) -> None:
        bus = EventBus()
        bus.emit(RunFailedEvent(error="max iterations reached"))
        events = _drain(bus)
        e = events[0]
        assert isinstance(e, RunFailedEvent)
        assert "max" in e.error

    def test_context_condensed_event(self) -> None:
        bus = EventBus()
        bus.emit(ContextCondensedEvent(tokens_before=8000, tokens_after=4000))
        events = _drain(bus)
        e = events[0]
        assert isinstance(e, ContextCondensedEvent)
        assert e.tokens_before > e.tokens_after

    def test_reflection_events(self) -> None:
        bus = EventBus()
        bus.emit(ReflectionStartEvent(iteration=1))
        bus.emit(ReflectionCompleteEvent(status="complete", confidence=0.95))
        events = _drain(bus)
        assert isinstance(events[0], ReflectionStartEvent)
        assert isinstance(events[1], ReflectionCompleteEvent)
        assert events[1].confidence == pytest.approx(0.95)

    def test_cancellation_event(self) -> None:
        bus = EventBus()
        bus.emit(CancellationEvent(message="user pressed cancel"))
        events = _drain(bus)
        assert isinstance(events[0], CancellationEvent)

    def test_session_start_event(self) -> None:
        bus = EventBus()
        bus.emit(SessionStartEvent(session_id="abc-123"))
        events = _drain(bus)
        e = events[0]
        assert isinstance(e, SessionStartEvent)
        assert e.session_id == "abc-123"

    def test_verification_events(self) -> None:
        bus = EventBus()
        bus.emit(VerificationStartEvent())
        bus.emit(VerificationCompleteEvent(status="passed"))
        events = _drain(bus)
        assert isinstance(events[0], VerificationStartEvent)
        assert isinstance(events[1], VerificationCompleteEvent)

    def test_replan_start_event(self) -> None:
        bus = EventBus()
        bus.emit(ReplanStartEvent(attempt=2))
        events = _drain(bus)
        e = events[0]
        assert isinstance(e, ReplanStartEvent)
        assert e.attempt == 2

    def test_checkpoint_created_event(self) -> None:
        bus = EventBus()
        bus.emit(CheckpointCreatedEvent(checkpoint_id="cp-001", label="before refactor"))
        events = _drain(bus)
        e = events[0]
        assert isinstance(e, CheckpointCreatedEvent)
        assert e.label == "before refactor"
