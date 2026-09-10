"""
`AutonomousExecutor.run` accepting a plan the caller already has.

Why this exists: the executor plans inside `run()`, so a plan preview
that calls `Planner.plan` to show the user some steps and then calls
`run()` would plan a second time. The user would approve one plan and
Pearl would execute another. They usually match, and usually is not a
guarantee. A preview that can silently diverge from what runs
manufactures confidence rather than informing it.

Two properties matter and are tested here.

The default must be untouched, because every existing caller and every
existing executor test goes through it.

A supplied plan must still be validated. Planning and validation are one
call today (`Planner.plan` runs `validate_plan` itself), so skipping the
planning call would skip the validator with it, and a plan reaching
execution unvalidated is exactly what the plan validator exists to
prevent.

Plans are never accepted from a client over HTTP. The server stores what
its own planner produced and the caller passes back an id. See
`tests/test_api_parity.py`.
"""

from __future__ import annotations

import pytest

from src.agent.plan_validator import PlanValidationError
from src.llm.parser import ToolCall
from tests.test_executor import _plan_of, build_executor


@pytest.fixture
def executor_and_planner():
    return build_executor()


class TestDefaultIsUnchanged:
    def test_no_supplied_plan_still_asks_the_planner(
        self, executor_and_planner, monkeypatch
    ):
        executor, planner = executor_and_planner
        calls = {"n": 0}

        def _counting(prompt, cancel_check=None):
            calls["n"] += 1
            return {"steps": [{"tool": "add", "arguments": {"a": 1, "b": 2}}]}

        monkeypatch.setattr(planner.client, "generate_json", _counting)

        report = executor.run("add one and two")

        assert calls["n"] == 1
        assert report.succeeded
        assert [s.tool_name for s in report.steps] == ["add"]

    def test_explicit_none_behaves_exactly_like_omitting_it(
        self, executor_and_planner, monkeypatch
    ):
        executor, planner = executor_and_planner
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of({"tool": "add", "arguments": {"a": 2, "b": 3}}),
        )

        report = executor.run("add two and three", initial_plan=None)

        assert report.succeeded
        assert report.steps[0].result == 5


class TestSuppliedPlanIsExecuted:
    def test_the_planner_is_not_called_at_all(self, executor_and_planner, monkeypatch):
        executor, planner = executor_and_planner

        def _explode(prompt, cancel_check=None):
            raise AssertionError("planner was called despite a supplied plan")

        monkeypatch.setattr(planner.client, "generate_json", _explode)

        report = executor.run(
            "add four and five",
            initial_plan=[ToolCall(tool_name="add", args=(), kwargs={"a": 4, "b": 5})],
        )

        assert report.succeeded
        assert report.steps[0].result == 9

    def test_the_supplied_steps_are_the_ones_that_run(
        self, executor_and_planner, monkeypatch
    ):
        """
        The point of the whole change. If the executor re-planned, this
        would run the planner's plan instead of the approved one.
        """
        executor, planner = executor_and_planner
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of({"tool": "add", "arguments": {"a": 100, "b": 100}}),
        )

        report = executor.run(
            "add one and one",
            initial_plan=[ToolCall(tool_name="add", args=(), kwargs={"a": 1, "b": 1})],
        )

        assert report.steps[0].result == 2


class TestSuppliedPlanIsStillValidated:
    def test_an_empty_supplied_plan_is_rejected(self, executor_and_planner):
        """
        An empty list is a supplied plan, not an absent one, so it must
        reach the validator rather than fall back to planning.
        """
        executor, _ = executor_and_planner

        report = executor.run("do nothing", initial_plan=[])

        assert not report.succeeded
        assert report.stop_reason != "completed"

    def test_a_forbidden_system_path_is_rejected(self, executor_and_planner):
        executor, _ = executor_and_planner

        report = executor.run(
            "read the password file",
            initial_plan=[
                ToolCall(tool_name="read_file", args=(), kwargs={"path": "/etc/passwd"})
            ],
        )

        assert not report.succeeded
        assert not any(s.tool_name == "read_file" for s in report.steps)

    def test_the_validator_really_rejects_that_plan(self):
        """
        Guards the test above. If `validate_plan` stopped rejecting this
        plan, that test would pass for the wrong reason.
        """
        from src.agent.plan_validator import validate_plan

        with pytest.raises(PlanValidationError):
            validate_plan(
                [
                    ToolCall(
                        tool_name="read_file", args=(), kwargs={"path": "/etc/passwd"}
                    )
                ]
            )
