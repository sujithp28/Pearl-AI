"""
What risk tier did this run actually stage?

Before this, the CLI's `--yes` path asked `can_auto_approve("staged")` —
a hardcoded literal. So a run that staged a file deletion, which is a
dangerous tool, was auto-approved in headless mode under the staged
policy. Headless mode exists to remove prompts, not protections, and
that was a protection being removed by accident.

The executor now records the tier of any tool that adds to the pending
change set, and the batch is as risky as its riskiest member.

The correlation lives in the executor rather than in ChangeManager,
which deliberately knows nothing about tools and still deals only in
paths and text. The executor is the one component that knows both.
"""

from __future__ import annotations

import pytest

from src.agent.headless import ExecutionPolicy
from src.tools.edit_tools import get_active_patch_manager
from src.tools.metadata import tool
from src.tools.models import max_risk
from tests.test_executor import _plan_of, build_executor


@tool(description="Stages a change; classified dangerous.", risk_level="dangerous")
def risky_stage() -> str:
    manager = get_active_patch_manager()
    assert manager is not None, "test tool requires an active ChangeManager"
    manager.propose("danger.txt", None, "boom\n")
    return "staged a dangerous change"


@tool(description="Stages a change; classified staged.", risk_level="staged")
def ordinary_stage() -> str:
    manager = get_active_patch_manager()
    assert manager is not None, "test tool requires an active ChangeManager"
    manager.propose("ordinary.txt", None, "fine\n")
    return "staged an ordinary change"


@tool(description="Reads something; stages nothing.", risk_level="safe")
def just_looking() -> str:
    return "nothing staged"


@pytest.fixture
def executor_and_planner():
    executor, planner = build_executor()
    for fn in (risky_stage, ordinary_stage, just_looking):
        executor.dispatcher._registry.register(fn)
    return executor, planner


class TestMaxRisk:
    def test_nothing_staged_is_safe(self) -> None:
        """Not a fail-open default: no risky operation happened."""
        assert max_risk() == "safe"

    def test_the_riskiest_member_wins(self) -> None:
        assert max_risk("safe", "dangerous", "staged") == "dangerous"
        assert max_risk("safe", "staged") == "staged"

    def test_an_unknown_tier_is_treated_as_most_restrictive(self) -> None:
        """A tier added later without updating the ordering fails closed."""
        assert max_risk("safe", "staged", "unheard-of") == "unheard-of"


class TestStagedRiskLevel:
    def test_a_run_that_stages_nothing_reports_safe(
        self, executor_and_planner, monkeypatch
    ) -> None:
        executor, planner = executor_and_planner
        monkeypatch.setattr(
            planner.client, "generate_json", _plan_of({"tool": "just_looking"})
        )

        executor.run("look around")

        assert executor.staged_risk_level() == "safe"

    def test_an_ordinary_staged_edit_reports_staged(
        self, executor_and_planner, monkeypatch
    ) -> None:
        executor, planner = executor_and_planner
        monkeypatch.setattr(
            planner.client, "generate_json", _plan_of({"tool": "ordinary_stage"})
        )

        executor.run("write a file")

        assert executor.staged_risk_level() == "staged"

    def test_a_dangerous_tool_that_stages_reports_dangerous(
        self, executor_and_planner, monkeypatch
    ) -> None:
        """The case the hardcoded literal used to hide."""
        executor, planner = executor_and_planner
        monkeypatch.setattr(
            planner.client, "generate_json", _plan_of({"tool": "risky_stage"})
        )

        executor.run("remove a file")

        assert executor.staged_risk_level() == "dangerous"

    def test_a_mixed_batch_reports_its_riskiest_member(
        self, executor_and_planner, monkeypatch
    ) -> None:
        executor, planner = executor_and_planner
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of({"tool": "ordinary_stage"}, {"tool": "risky_stage"}),
        )

        executor.run("edit one file and remove another")

        assert executor.staged_risk_level() == "dangerous"

    def test_a_tool_that_stages_nothing_does_not_raise_the_tier(
        self, executor_and_planner, monkeypatch
    ) -> None:
        """
        Risk is recorded for staging, not for running. A dangerous tool
        that touches no files must not block approval of an unrelated
        edit, or every run using the shell would be unapprovable.
        """
        executor, planner = executor_and_planner

        @tool(description="Dangerous but stages nothing.", risk_level="dangerous")
        def dangerous_noop() -> str:
            return "ran, staged nothing"

        executor.dispatcher._registry.register(dangerous_noop)
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of({"tool": "dangerous_noop"}, {"tool": "ordinary_stage"}),
        )

        executor.run("run a command then edit a file")

        assert executor.staged_risk_level() == "staged"

    def test_the_tier_resets_between_runs(
        self, executor_and_planner, monkeypatch
    ) -> None:
        """
        Otherwise one dangerous run would poison every later run in the
        same session.
        """
        executor, planner = executor_and_planner
        monkeypatch.setattr(
            planner.client, "generate_json", _plan_of({"tool": "risky_stage"})
        )
        executor.run("remove a file")
        executor.reject()

        monkeypatch.setattr(
            planner.client, "generate_json", _plan_of({"tool": "just_looking"})
        )
        executor.run("look around")

        assert executor.staged_risk_level() == "safe"


class TestApprovalDecisionUsesIt:
    """
    The end the whole thing exists for: what `--yes` decides.
    """

    def test_headless_approve_still_refuses_a_dangerous_batch(self) -> None:
        policy = ExecutionPolicy(mode="headless", staged_policy="approve")

        assert policy.can_auto_approve("staged") is True
        assert policy.can_auto_approve("dangerous") is False

    def test_the_decision_changes_with_the_batch(
        self, executor_and_planner, monkeypatch
    ) -> None:
        executor, planner = executor_and_planner
        policy = ExecutionPolicy(mode="headless", staged_policy="approve")

        monkeypatch.setattr(
            planner.client, "generate_json", _plan_of({"tool": "ordinary_stage"})
        )
        executor.run("edit a file")
        ordinary = policy.can_auto_approve(executor.staged_risk_level())
        executor.reject()

        monkeypatch.setattr(
            planner.client, "generate_json", _plan_of({"tool": "risky_stage"})
        )
        executor.run("remove a file")
        risky = policy.can_auto_approve(executor.staged_risk_level())

        assert ordinary is True
        assert risky is False
