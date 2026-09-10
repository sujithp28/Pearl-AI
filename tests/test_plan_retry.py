"""
Tests for AutonomousExecutor._plan_with_retry() — Milestone 2, G1.

Verifies that the first-attempt-plus-one-retry strategy works correctly
and that LLMCancelled is never swallowed by the retry logic.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.agent.executor import AutonomousExecutor
from src.llm.client import LLMCancelled
from src.llm.parser import ToolCall


def _make_executor() -> AutonomousExecutor:
    planner = MagicMock()
    dispatcher = MagicMock()
    return AutonomousExecutor(planner, dispatcher)


def _tool_call(name: str = "read_file") -> ToolCall:
    tc = MagicMock(spec=ToolCall)
    tc.tool_name = name
    tc.kwargs = {"path": "src/main.py"}
    tc.step_id = None
    tc.depends_on = []
    return tc


class TestPlanWithRetrySuccess:
    def test_returns_plan_on_first_success(self):
        """When planner.plan() succeeds on first attempt, return its result."""
        executor = _make_executor()
        tc = _tool_call()
        executor.planner.plan.return_value = [tc]

        result = executor._plan_with_retry("explain code", "context", [], [])
        assert result == [tc]
        executor.planner.plan.assert_called_once()
        executor.planner.replan.assert_not_called()

    def test_retries_on_first_failure(self):
        """When plan() fails, replan() is called with error feedback."""
        executor = _make_executor()
        tc = _tool_call()
        executor.planner.plan.side_effect = ValueError("invalid JSON")
        executor.planner.replan.return_value = [tc]

        result = executor._plan_with_retry("explain code", "context", [], [])
        assert result == [tc]
        executor.planner.plan.assert_called_once()
        executor.planner.replan.assert_called_once()

    def test_replan_called_with_error_info(self):
        """The replan call must include error type and suggestion."""
        executor = _make_executor()
        executor.planner.plan.side_effect = ValueError("bad plan")
        executor.planner.replan.return_value = [_tool_call()]

        executor._plan_with_retry("fix bug", "ctx", [], [])

        call_kwargs = executor.planner.replan.call_args
        failed_arg = call_kwargs[1].get("failed") or call_kwargs[0][2]
        assert "error_type" in failed_arg or "error" in failed_arg


class TestPlanWithRetryCancellation:
    def test_llm_cancelled_not_retried(self):
        """LLMCancelled must propagate immediately — never silently retried."""
        executor = _make_executor()
        executor.planner.plan.side_effect = LLMCancelled("user cancelled")

        with pytest.raises(LLMCancelled):
            executor._plan_with_retry("prompt", "ctx", [], [])

        executor.planner.replan.assert_not_called()

    def test_llm_cancelled_in_replan_propagates(self):
        """LLMCancelled raised by replan() must also propagate."""
        executor = _make_executor()
        executor.planner.plan.side_effect = ValueError("bad JSON")
        executor.planner.replan.side_effect = LLMCancelled("cancelled mid-replan")

        with pytest.raises(LLMCancelled):
            executor._plan_with_retry("prompt", "ctx", [], [])


class TestPlanWithRetryReplanFailure:
    def test_replan_failure_raises(self):
        """When both plan() and replan() fail, the replan exception propagates."""
        executor = _make_executor()
        executor.planner.plan.side_effect = ValueError("bad plan")
        executor.planner.replan.side_effect = RuntimeError("replan also bad")

        with pytest.raises(RuntimeError, match="replan also bad"):
            executor._plan_with_retry("prompt", "ctx", [], [])
