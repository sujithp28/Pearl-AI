"""
Tests for deterministic confidence scoring.

Every test uses concrete inputs and checks concrete outputs.
No LLM calls, no mocking of the scoring function itself.
"""

from __future__ import annotations

import pytest

from src.agent.confidence import (
    MAX_REPLAN_PENALTY,
    POSITION_PENALTY,
    REPLAN_PENALTY,
    SHELL_COMPLEX_PENALTY,
    SHELL_PENALTY,
    SHELL_TOOL_NAMES,
    WRITE_PENALTY,
    WRITE_TOOL_NAMES,
    ConfidenceFactor,
    PlanConfidence,
    StepConfidence,
    _clamp,
    _score_step,
    score_plan,
)
from src.llm.parser import ToolCall


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _call(tool_name: str, **kwargs) -> ToolCall:
    return ToolCall(tool_name=tool_name, args=(), kwargs=kwargs)


def _calls(*names: str) -> list[ToolCall]:
    return [_call(name) for name in names]


# ---------------------------------------------------------------------------
# _clamp()
# ---------------------------------------------------------------------------


class TestClamp:
    def test_value_in_range_unchanged(self):
        assert _clamp(0.75) == 0.75

    def test_value_above_one_clamped(self):
        assert _clamp(1.5) == 1.0

    def test_value_below_zero_clamped(self):
        assert _clamp(-0.5) == 0.0

    def test_exactly_zero(self):
        assert _clamp(0.0) == 0.0

    def test_exactly_one(self):
        assert _clamp(1.0) == 1.0

    def test_result_is_rounded_to_4dp(self):
        result = _clamp(0.12345678)
        assert result == round(0.12345678, 4)


# ---------------------------------------------------------------------------
# _score_step() — position factor
# ---------------------------------------------------------------------------


class TestPositionFactor:
    def test_step_0_has_no_position_deduction(self):
        result = _score_step(_call("read_file", path="f.py"), 0)
        assert not any(f.name == "position" for f in result.factors)

    def test_step_1_has_position_deduction(self):
        result = _score_step(_call("read_file", path="f.py"), 1)
        position = next(f for f in result.factors if f.name == "position")
        assert position.deduction == POSITION_PENALTY * 1

    def test_step_4_has_correct_deduction(self):
        result = _score_step(_call("read_file", path="f.py"), 4)
        position = next(f for f in result.factors if f.name == "position")
        assert position.deduction == pytest.approx(POSITION_PENALTY * 4)

    def test_position_deduction_reduces_score(self):
        result = _score_step(_call("read_file", path="f.py"), 5)
        assert result.score == pytest.approx(1.0 - POSITION_PENALTY * 5)

    def test_none_step_always_scores_one_regardless_of_position(self):
        for idx in (0, 1, 10, 24):
            result = _score_step(_call("none"), idx)
            assert result.score == 1.0
            assert not result.factors


# ---------------------------------------------------------------------------
# _score_step() — write operation factor
# ---------------------------------------------------------------------------


class TestWriteFactor:
    @pytest.mark.parametrize("tool_name", sorted(WRITE_TOOL_NAMES))
    def test_known_write_tool_has_write_factor(self, tool_name: str):
        result = _score_step(_call(tool_name), 0)
        assert any(f.name == "write_operation" for f in result.factors)

    @pytest.mark.parametrize("tool_name", sorted(WRITE_TOOL_NAMES))
    def test_write_tool_deducts_write_penalty(self, tool_name: str):
        result = _score_step(_call(tool_name), 0)
        assert result.score == pytest.approx(1.0 - WRITE_PENALTY)

    def test_read_only_tool_has_no_write_factor(self):
        result = _score_step(_call("read_file", path="f.py"), 0)
        assert not any(f.name == "write_operation" for f in result.factors)

    def test_write_and_position_penalties_stack(self):
        result = _score_step(_call("create_file", path="x.py", content=""), 2)
        assert result.score == pytest.approx(1.0 - WRITE_PENALTY - POSITION_PENALTY * 2)


# ---------------------------------------------------------------------------
# _score_step() — shell command factor
# ---------------------------------------------------------------------------


class TestShellFactor:
    @pytest.mark.parametrize("tool_name", sorted(SHELL_TOOL_NAMES))
    def test_known_shell_tool_has_shell_factor(self, tool_name: str):
        result = _score_step(_call(tool_name, command="echo hi"), 0)
        assert any(f.name == "shell_command" for f in result.factors)

    def test_execute_shell_deducts_shell_penalty(self):
        result = _score_step(_call("execute_shell", command="echo hi"), 0)
        assert result.score == pytest.approx(1.0 - SHELL_PENALTY)

    def test_run_python_deducts_shell_penalty(self):
        result = _score_step(_call("run_python", script="print('hi')"), 0)
        assert result.score == pytest.approx(1.0 - SHELL_PENALTY)

    def test_shell_position_and_write_do_not_stack_for_non_shell(self):
        result = _score_step(_call("read_file", path="f.py"), 0)
        assert result.score == 1.0


# ---------------------------------------------------------------------------
# _score_step() — complex shell factor
# ---------------------------------------------------------------------------


class TestComplexShellFactor:
    @pytest.mark.parametrize(
        "command",
        [
            "ls | grep py",
            "echo hi && echo bye",
            "echo hi || exit 1",
            "echo a; echo b",
            "cat f > out.txt",
            "cat out.txt >> log.txt",
            "sort < input.txt",
            "echo `date`",
            "echo $(pwd)",
        ],
    )
    def test_complex_execute_shell_gets_extra_deduction(self, command: str):
        result = _score_step(_call("execute_shell", command=command), 0)
        assert any(f.name == "complex_shell" for f in result.factors)
        assert result.score == pytest.approx(1.0 - SHELL_PENALTY - SHELL_COMPLEX_PENALTY)

    def test_simple_execute_shell_no_complex_factor(self):
        result = _score_step(_call("execute_shell", command="echo hello"), 0)
        assert not any(f.name == "complex_shell" for f in result.factors)
        assert result.score == pytest.approx(1.0 - SHELL_PENALTY)

    def test_run_python_with_pipes_does_not_get_complex_penalty(self):
        # run_python's 'script' is Python code; complexity is not assessed
        # by the same shell-syntax scan.
        result = _score_step(_call("run_python", script="x = a | b"), 0)
        assert not any(f.name == "complex_shell" for f in result.factors)
        assert result.score == pytest.approx(1.0 - SHELL_PENALTY)

    def test_execute_shell_missing_command_arg_no_complex_factor(self):
        # If 'command' kwarg is absent we default to "" which has no
        # complex indicators.
        result = _score_step(_call("execute_shell"), 0)
        assert not any(f.name == "complex_shell" for f in result.factors)


# ---------------------------------------------------------------------------
# _score_step() — score is always in [0.0, 1.0]
# ---------------------------------------------------------------------------


class TestStepScoreBounds:
    def test_score_never_below_zero(self):
        # Many simultaneous penalties on the same step cannot produce a
        # negative score.
        result = _score_step(_call("execute_shell", command="cmd && cmd2"), 24)
        assert result.score >= 0.0

    def test_score_never_above_one(self):
        result = _score_step(_call("none"), 0)
        assert result.score <= 1.0

    def test_returns_step_confidence_instance(self):
        assert isinstance(_score_step(_call("read_file", path="f.py"), 0), StepConfidence)


# ---------------------------------------------------------------------------
# score_plan() — overall score computation
# ---------------------------------------------------------------------------


class TestScorePlan:
    def test_returns_plan_confidence_instance(self):
        assert isinstance(score_plan(_calls("read_file")), PlanConfidence)

    def test_empty_plan_returns_one(self):
        result = score_plan([])
        assert result.score == 1.0
        assert result.step_scores == []
        assert result.plan_factors == []

    def test_single_read_step_scores_one(self):
        result = score_plan([_call("read_file", path="f.py")])
        assert result.score == 1.0

    def test_score_is_mean_of_step_scores(self):
        steps = _calls("read_file", "read_file", "read_file")
        result = score_plan(steps)
        expected = sum(sc.score for sc in result.step_scores) / len(result.step_scores)
        assert result.score == pytest.approx(expected)

    def test_longer_plan_has_lower_score_than_shorter(self):
        short = score_plan(_calls("read_file"))
        long_ = score_plan(_calls(*["read_file"] * 10))
        assert long_.score < short.score

    def test_write_step_lowers_plan_score(self):
        read_only = score_plan([_call("read_file", path="f.py")])
        with_write = score_plan(
            [_call("read_file", path="f.py"), _call("create_file", path="f.py", content="")]
        )
        assert with_write.score < read_only.score

    def test_shell_step_lowers_score_more_than_write(self):
        with_write = score_plan([_call("create_file", path="f.py", content="")])
        with_shell = score_plan([_call("execute_shell", command="echo hi")])
        assert with_shell.score < with_write.score

    def test_step_scores_list_length_matches_steps(self):
        steps = _calls("read_file", "create_file", "execute_shell")
        result = score_plan(steps)
        assert len(result.step_scores) == 3

    def test_step_scores_preserve_order(self):
        steps = _calls("read_file", "create_file", "execute_shell")
        result = score_plan(steps)
        assert [sc.tool_name for sc in result.step_scores] == [
            "read_file",
            "create_file",
            "execute_shell",
        ]

    def test_step_scores_preserve_indices(self):
        steps = _calls("read_file", "create_file", "execute_shell")
        result = score_plan(steps)
        assert [sc.step_index for sc in result.step_scores] == [0, 1, 2]

    def test_score_always_in_unit_interval(self):
        # Maximum-risk plan: 25 complex shell steps.
        steps = [_call("execute_shell", command="cmd | cmd2") for _ in range(25)]
        result = score_plan(steps)
        assert 0.0 <= result.score <= 1.0

    def test_all_none_steps_score_one(self):
        result = score_plan(_calls("none", "none"))
        # none steps score 1.0 each; mean is 1.0; no plan factors.
        assert result.score == 1.0


# ---------------------------------------------------------------------------
# score_plan() — replan history factor
# ---------------------------------------------------------------------------


class TestReplanFactor:
    def test_no_replans_no_plan_factor(self):
        result = score_plan(_calls("read_file"), replans_used=0)
        assert result.plan_factors == []

    def test_one_replan_adds_plan_factor(self):
        result = score_plan(_calls("read_file"), replans_used=1)
        assert any(f.name == "prior_replans" for f in result.plan_factors)

    def test_replan_reduces_overall_score(self):
        no_replan = score_plan(_calls("read_file"), replans_used=0)
        with_replan = score_plan(_calls("read_file"), replans_used=1)
        assert with_replan.score < no_replan.score

    def test_one_replan_deduction_equals_replan_penalty(self):
        no_replan = score_plan(_calls("read_file"), replans_used=0)
        with_replan = score_plan(_calls("read_file"), replans_used=1)
        assert no_replan.score - with_replan.score == pytest.approx(REPLAN_PENALTY)

    def test_replan_penalty_is_capped_at_max(self):
        # 10 replans should not deduct more than MAX_REPLAN_PENALTY.
        no_replan = score_plan(_calls("read_file"), replans_used=0)
        many_replans = score_plan(_calls("read_file"), replans_used=10)
        assert no_replan.score - many_replans.score == pytest.approx(MAX_REPLAN_PENALTY)

    def test_two_replans_deducts_twice_replan_penalty(self):
        no_replan = score_plan(_calls("read_file"), replans_used=0)
        two_replans = score_plan(_calls("read_file"), replans_used=2)
        assert no_replan.score - two_replans.score == pytest.approx(REPLAN_PENALTY * 2)

    def test_replan_factor_description_mentions_count(self):
        result = score_plan(_calls("read_file"), replans_used=3)
        factor = next(f for f in result.plan_factors if f.name == "prior_replans")
        assert "3" in factor.description


# ---------------------------------------------------------------------------
# score_plan() — score is always in [0.0, 1.0]
# ---------------------------------------------------------------------------


class TestPlanScoreBounds:
    def test_plan_score_never_negative(self):
        # Worst case: 25 complex shell steps + max replans.
        steps = [_call("execute_shell", command="cmd | cmd2") for _ in range(25)]
        result = score_plan(steps, replans_used=10)
        assert result.score >= 0.0

    def test_plan_score_never_above_one(self):
        result = score_plan(_calls("none"))
        assert result.score <= 1.0


# ---------------------------------------------------------------------------
# Integration: score_plan() consistency with _score_step()
# ---------------------------------------------------------------------------


class TestScoringConsistency:
    def test_overall_score_equals_mean_of_step_scores_when_no_plan_factors(self):
        steps = [_call("read_file", path="f.py"), _call("create_file", path="g.py", content="")]
        result = score_plan(steps, replans_used=0)
        expected_mean = sum(sc.score for sc in result.step_scores) / len(result.step_scores)
        assert result.score == pytest.approx(expected_mean)

    def test_overall_score_equals_mean_minus_replan_deduction(self):
        steps = [_call("read_file", path="f.py"), _call("read_file", path="g.py")]
        result = score_plan(steps, replans_used=1)
        mean = sum(sc.score for sc in result.step_scores) / len(result.step_scores)
        replan_deduction = next(
            f.deduction for f in result.plan_factors if f.name == "prior_replans"
        )
        assert result.score == pytest.approx(_clamp(mean - replan_deduction))

    def test_confidence_factor_is_dataclass(self):
        result = score_plan([_call("create_file", path="f.py", content="")], replans_used=1)
        step_factor = result.step_scores[0].factors[0]
        assert isinstance(step_factor, ConfidenceFactor)
        plan_factor = result.plan_factors[0]
        assert isinstance(plan_factor, ConfidenceFactor)


# ---------------------------------------------------------------------------
# Task 37: Regression suite — pin exact computed scores for known inputs
#
# These tests exist to catch silent constant drift. If POSITION_PENALTY,
# WRITE_PENALTY, SHELL_PENALTY, SHELL_COMPLEX_PENALTY, REPLAN_PENALTY,
# or MAX_REPLAN_PENALTY change, the tests below fail immediately.
# Adjust the expected values here whenever a constant is intentionally changed.
# ---------------------------------------------------------------------------


class TestConfidenceRegressionValues:
    """Pin exact computed scores so constant changes cause a visible failure."""

    # ---- Single-step plans ----

    def test_single_read_step_scores_exactly_1(self):
        result = score_plan([_call("read_file", path="f.py")])
        assert result.score == 1.0

    def test_single_write_step_scores_exactly_0_9(self):
        # Step 0 has no position penalty.  write penalty = 0.10.
        # 1.0 - 0.10 = 0.90
        result = score_plan([_call("create_file", path="f.py", content="")])
        assert result.score == 0.9

    def test_single_shell_step_scores_exactly_0_8(self):
        # shell penalty = 0.20
        result = score_plan([_call("execute_shell", command="echo hi")])
        assert result.score == 0.8

    def test_single_complex_shell_step_scores_exactly_0_75(self):
        # shell penalty 0.20 + complex penalty 0.05 = 0.25 → 0.75
        result = score_plan([_call("execute_shell", command="echo hi | cat")])
        assert result.score == 0.75

    # ---- Position penalties ----

    def test_second_read_step_gets_position_deduction(self):
        # Step 1: 1.0 - 0.02*1 = 0.98
        result = score_plan([_call("read_file"), _call("read_file")])
        assert result.step_scores[1].score == 0.98

    def test_fifth_read_step_gets_four_position_deductions(self):
        # Step 4: 1.0 - 0.02*4 = 0.92
        steps = _calls("read_file", "read_file", "read_file", "read_file", "read_file")
        result = score_plan(steps)
        assert result.step_scores[4].score == 0.92

    # ---- Two-step plan with read + write ----

    def test_read_then_write_mean_score(self):
        # Step 0 (read):  1.0
        # Step 1 (write): 1.0 - 0.02 (position) - 0.10 (write) = 0.88
        # mean = (1.0 + 0.88) / 2 = 0.94
        result = score_plan([_call("read_file"), _call("create_file", path="f.py", content="")])
        assert result.score == 0.94

    # ---- Replan penalties ----

    def test_one_replan_deducts_0_15_from_plan_score(self):
        # read step score = 1.0; replan deduction = 0.15 → 0.85
        result = score_plan([_call("read_file")], replans_used=1)
        assert result.score == 0.85

    def test_two_replans_deducts_0_30(self):
        # 1.0 - 0.15*2 = 0.70
        result = score_plan([_call("read_file")], replans_used=2)
        assert result.score == 0.70

    def test_three_replans_capped_at_max_replan_penalty(self):
        # 0.15*3=0.45 > MAX_REPLAN_PENALTY=0.30 → deduction is 0.30
        result = score_plan([_call("read_file")], replans_used=3)
        assert result.score == 0.70  # 1.0 - 0.30

    # ---- Constant stability (fail if constants change) ----

    def test_position_penalty_constant_is_0_02(self):
        assert POSITION_PENALTY == 0.02

    def test_write_penalty_constant_is_0_10(self):
        assert WRITE_PENALTY == 0.10

    def test_shell_penalty_constant_is_0_20(self):
        assert SHELL_PENALTY == 0.20

    def test_shell_complex_penalty_constant_is_0_05(self):
        assert SHELL_COMPLEX_PENALTY == 0.05

    def test_replan_penalty_constant_is_0_15(self):
        assert REPLAN_PENALTY == 0.15

    def test_max_replan_penalty_constant_is_0_30(self):
        assert MAX_REPLAN_PENALTY == 0.30
