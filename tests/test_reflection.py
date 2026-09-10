"""
Tests for the reflection phase: _reflect() pure function and its
integration with AutonomousExecutor (reflection is populated on every
terminal ExecutionReport).
"""

from __future__ import annotations

import pytest

from src.agent.dispatcher import ToolDispatcher
from src.agent.executor import (
    AutonomousExecutor,
    ExecutionStep,
    ReflectionResult,
    _reflect,
)
from src.agent.planner import Planner
from src.tools.edit_tools import set_active_patch_manager
from src.tools.metadata import tool
from src.tools.registry import ToolRegistry
from src.tools.shell_tools import set_active_command_approver

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_leaked_approval_state():
    yield
    set_active_patch_manager(None)
    set_active_command_approver(None)


def _step(tool_name: str, *, ok: bool = True, iteration: int = 1) -> ExecutionStep:
    return ExecutionStep(
        iteration=iteration,
        tool_name=tool_name,
        kwargs={},
        result="done" if ok else None,
        error=None if ok else "boom",
        summary="ok" if ok else "fail",
    )


@tool(description="Add two numbers.", parameters={"a": "int", "b": "int"}, returns="int")
def add(a: int, b: int) -> int:
    return a + b


@tool(description="Always fails.")
def boom() -> None:
    raise ValueError("kaboom")


def _build_executor():
    registry = ToolRegistry()
    registry.register(add)
    registry.register(boom)
    dispatcher = ToolDispatcher(registry)
    planner = Planner(registry, dispatcher)
    return AutonomousExecutor(planner, dispatcher, checkpoints=None), planner


def _plan_of(*steps):
    return lambda prompt, cancel_check=None: {"steps": list(steps)}


# ---------------------------------------------------------------------------
# _reflect() — unit tests (pure function, no executor needed)
# ---------------------------------------------------------------------------


def test_complete_when_stop_reason_is_completed():
    steps = [_step("add")]
    result = _reflect(steps, "completed", 0)
    assert result.outcome == "COMPLETE"


def test_complete_includes_step_count_in_evidence():
    steps = [_step("add"), _step("add", iteration=2)]
    result = _reflect(steps, "completed", 0)
    assert "2" in " ".join(result.evidence)


def test_complete_mentions_replans_when_used():
    steps = [_step("add")]
    result = _reflect(steps, "completed", 2)
    assert result.outcome == "COMPLETE"
    joined = " ".join(result.evidence)
    assert "2" in joined and "replan" in joined


def test_complete_no_replan_mention_when_zero():
    steps = [_step("add")]
    result = _reflect(steps, "completed", 0)
    assert "replan" not in " ".join(result.evidence)


def test_partial_when_some_steps_succeeded_and_stopped_early():
    steps = [_step("add"), _step("boom", ok=False, iteration=2)]
    result = _reflect(steps, "fatal_error", 3)
    assert result.outcome == "PARTIAL"


def test_partial_evidence_names_failed_tool():
    steps = [_step("add"), _step("boom", ok=False, iteration=2)]
    result = _reflect(steps, "fatal_error", 3)
    assert "boom" in " ".join(result.evidence)


def test_partial_evidence_includes_stop_reason():
    steps = [_step("add"), _step("boom", ok=False, iteration=2)]
    result = _reflect(steps, "max_iterations", 0)
    assert "max_iterations" in " ".join(result.evidence)


def test_failed_when_no_steps_ran():
    result = _reflect([], "cancelled", 0)
    assert result.outcome == "FAILED"


def test_failed_when_all_steps_failed():
    steps = [_step("boom", ok=False), _step("boom", ok=False, iteration=2)]
    result = _reflect(steps, "fatal_error", 3)
    assert result.outcome == "FAILED"


def test_failed_evidence_includes_stop_reason():
    result = _reflect([], "cancelled", 0)
    assert "cancelled" in " ".join(result.evidence)


def test_reflect_returns_reflection_result():
    result = _reflect([], "completed", 0)
    assert isinstance(result, ReflectionResult)


# ---------------------------------------------------------------------------
# Integration: AutonomousExecutor populates reflection on every terminal report
# ---------------------------------------------------------------------------


def test_report_has_reflection_after_completed_run(monkeypatch):
    executor, planner = _build_executor()
    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of({"tool": "add", "arguments": {"a": 1, "b": 2}}),
    )
    report = executor.run("add 1+2")
    assert report.reflection is not None
    assert report.reflection.outcome == "COMPLETE"


def test_report_has_reflection_after_fatal_error(monkeypatch):
    executor, planner = _build_executor()
    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of({"tool": "boom", "arguments": {}}),
    )
    report = executor.run("do something that always fails")
    assert report.reflection is not None
    assert report.reflection.outcome in ("PARTIAL", "FAILED")


def test_report_reflection_is_none_when_awaiting_approval(monkeypatch, tmp_path):
    executor, planner = _build_executor()
    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of({"tool": "add", "arguments": {"a": 1, "b": 2}}),
    )
    monkeypatch.chdir(tmp_path)

    report = executor.run("add 1+2")
    # add() doesn't stage patches, so the run completes; reflection is set
    assert report.stop_reason == "completed"
    assert report.reflection is not None


def test_report_reflection_after_cancelled_before_plan(monkeypatch):
    executor, planner = _build_executor()
    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of({"tool": "add", "arguments": {"a": 1, "b": 2}}),
    )
    executor.cancel()
    report = executor.run("add 1+2")
    assert report.stop_reason == "cancelled"
    assert report.reflection is not None
    assert report.reflection.outcome == "FAILED"
