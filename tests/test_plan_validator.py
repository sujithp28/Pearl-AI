"""
Tests for the deterministic plan validator.

Unit tests cover validate_plan() directly with ToolCall fixtures.
Integration tests exercise the Planner so the full plan() / replan()
path is verified.
"""

from __future__ import annotations

import pytest

from src.agent.dispatcher import ToolDispatcher
from src.agent.plan_validator import (
    DESTRUCTIVE_WRITE_TOOLS,
    FORBIDDEN_PATH_PREFIXES,
    MAX_STEPS,
    PlanValidationError,
    validate_plan,
)
from src.agent.planner import Planner
from src.llm.parser import ToolCall
from src.tools.metadata import tool
from src.tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _call(tool_name: str, **kwargs) -> ToolCall:
    return ToolCall(tool_name=tool_name, args=(), kwargs=kwargs)


def _calls(*tool_names: str) -> list[ToolCall]:
    return [_call(name) for name in tool_names]


# ---------------------------------------------------------------------------
# validate_plan() — unit tests
# ---------------------------------------------------------------------------


class TestStepCountValidation:
    def test_empty_plan_raises(self):
        with pytest.raises(PlanValidationError, match="at least one step"):
            validate_plan([])

    def test_plan_at_max_steps_is_valid(self):
        validate_plan(_calls(*["add"] * MAX_STEPS))  # must not raise

    def test_plan_one_over_max_raises(self):
        with pytest.raises(PlanValidationError, match=str(MAX_STEPS)):
            validate_plan(_calls(*["add"] * (MAX_STEPS + 1)))

    def test_error_message_includes_actual_count(self):
        count = MAX_STEPS + 5
        with pytest.raises(PlanValidationError, match=str(count)):
            validate_plan(_calls(*["add"] * count))

    def test_single_step_plan_is_valid(self):
        validate_plan([_call("add", a=1, b=2)])  # must not raise

    def test_none_step_plan_is_valid(self):
        validate_plan([_call("none")])  # must not raise


class TestForbiddenPathValidation:
    @pytest.mark.parametrize("prefix", FORBIDDEN_PATH_PREFIXES)
    def test_forbidden_prefix_in_string_arg_raises(self, prefix: str):
        step = _call("read_file", path=f"{prefix}sensitive")
        with pytest.raises(PlanValidationError, match="forbidden"):
            validate_plan([step])

    def test_error_names_the_offending_step_number(self):
        steps = [_call("read_file", path="safe.py"), _call("read_file", path="/etc/passwd")]
        with pytest.raises(PlanValidationError, match="Step 2"):
            validate_plan(steps)

    def test_error_names_the_offending_tool(self):
        steps = [_call("read_file", path="/etc/passwd")]
        with pytest.raises(PlanValidationError, match="read_file"):
            validate_plan(steps)

    def test_error_names_the_offending_argument(self):
        steps = [_call("read_file", path="/etc/passwd")]
        with pytest.raises(PlanValidationError, match="path"):
            validate_plan(steps)

    def test_relative_path_is_allowed(self):
        validate_plan([_call("read_file", path="src/main.py")])  # must not raise

    def test_absolute_path_inside_home_is_allowed(self):
        validate_plan([_call("read_file", path="/home/sujith/project/file.py")])

    def test_non_string_arg_is_not_path_checked(self):
        validate_plan([_call("add", a=1, b=2)])  # must not raise

    def test_none_step_skips_path_check(self):
        # "none" steps are sentinel steps with no real arguments;
        # they are explicitly skipped to avoid false positives.
        validate_plan([_call("none")])  # must not raise

    def test_multiple_forbidden_paths_collected_together(self):
        steps = [
            _call("read_file", path="/etc/passwd"),
            _call("read_file", path="/proc/self/mem"),
        ]
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(steps)
        msg = str(exc_info.value)
        assert "/etc/passwd" in msg
        assert "/proc/self/mem" in msg

    def test_plan_exceeding_max_and_forbidden_path_reports_both(self):
        steps = _calls(*["add"] * (MAX_STEPS + 1))
        steps[0] = _call("read_file", path="/etc/passwd")
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(steps)
        msg = str(exc_info.value)
        assert str(MAX_STEPS) in msg
        assert "/etc/passwd" in msg


# ---------------------------------------------------------------------------
# Integration: Planner.plan() and Planner.replan() call validate_plan()
# ---------------------------------------------------------------------------


@tool(description="Add two numbers.", parameters={"a": "int", "b": "int"}, returns="int")
def add(a: int, b: int) -> int:
    return a + b


def _build_planner() -> Planner:
    registry = ToolRegistry()
    registry.register(add)
    dispatcher = ToolDispatcher(registry)
    return Planner(registry, dispatcher)


class TestPlannerIntegration:
    def test_plan_raises_on_oversized_plan(self, monkeypatch):
        planner = _build_planner()
        oversized = [{"tool": "add", "arguments": {"a": i, "b": i}} for i in range(MAX_STEPS + 1)]
        monkeypatch.setattr(
            planner.client, "generate_json", lambda prompt, **kw: {"steps": oversized}
        )
        with pytest.raises((PlanValidationError, ValueError)):
            planner.plan("do a lot of things")

    def test_plan_raises_on_forbidden_path(self, monkeypatch):
        planner = _build_planner()
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            lambda prompt, **kw: {
                "steps": [{"tool": "add", "arguments": {"a": "/etc/passwd", "b": 2}}]
            },
        )
        with pytest.raises((PlanValidationError, ValueError)):
            planner.plan("do something dangerous")

    def test_plan_accepts_valid_plan(self, monkeypatch):
        planner = _build_planner()
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            lambda prompt, **kw: {
                "steps": [{"tool": "add", "arguments": {"a": 1, "b": 2}}]
            },
        )
        steps = planner.plan("add 1+2")
        assert len(steps) == 1

    def test_replan_raises_on_oversized_plan(self, monkeypatch):
        planner = _build_planner()
        oversized = [{"tool": "add", "arguments": {"a": i, "b": i}} for i in range(MAX_STEPS + 1)]
        monkeypatch.setattr(
            planner.client, "generate_json", lambda prompt, **kw: {"steps": oversized}
        )
        with pytest.raises((PlanValidationError, ValueError)):
            planner.replan("do a lot", completed=[], failed={"tool": "add", "arguments": {}, "error": "x"})

    def test_replan_raises_on_forbidden_path(self, monkeypatch):
        planner = _build_planner()
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            lambda prompt, **kw: {
                "steps": [{"tool": "add", "arguments": {"a": "/sys/kernel", "b": 0}}]
            },
        )
        with pytest.raises((PlanValidationError, ValueError)):
            planner.replan("do something", completed=[], failed={"tool": "x", "arguments": {}, "error": "y"})


# ---------------------------------------------------------------------------
# validate_plan() — read-before-write enforcement
# ---------------------------------------------------------------------------


class TestReadBeforeWrite:
    def test_write_file_without_prior_read_raises(self):
        steps = [_call("write_file", path="x.py", content="hi")]
        with pytest.raises(PlanValidationError, match="without a prior read_file"):
            validate_plan(steps)

    def test_replace_in_file_without_prior_read_raises(self):
        steps = [_call("replace_in_file", path="x.py", old="a", new="b")]
        with pytest.raises(PlanValidationError, match="without a prior read_file"):
            validate_plan(steps)

    def test_edit_lines_without_prior_read_raises(self):
        steps = [_call("edit_lines", path="x.py", start=1, end=1, new_content="x")]
        with pytest.raises(PlanValidationError, match="without a prior read_file"):
            validate_plan(steps)

    def test_patch_file_without_prior_read_raises(self):
        steps = [_call("patch_file", path="x.py", diff="@@ @@")]
        with pytest.raises(PlanValidationError, match="without a prior read_file"):
            validate_plan(steps)

    def test_read_then_write_is_valid(self):
        steps = [
            _call("read_file", path="x.py"),
            _call("write_file", path="x.py", content="new"),
        ]
        validate_plan(steps)  # must not raise

    def test_read_anywhere_before_write_satisfies_constraint(self):
        # read_file doesn't have to immediately precede the write
        steps = [
            _call("read_file", path="x.py"),
            _call("add", a=1, b=2),
            _call("write_file", path="x.py", content="new"),
        ]
        validate_plan(steps)  # must not raise

    def test_create_file_without_read_is_allowed(self):
        # create_file is intentionally excluded from DESTRUCTIVE_WRITE_TOOLS
        steps = [_call("create_file", path="new.py", content="# new")]
        validate_plan(steps)  # must not raise

    def test_error_message_names_step_number_and_tool(self):
        steps = [
            _call("add", a=1, b=2),
            _call("write_file", path="x.py", content="hi"),
        ]
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(steps)
        msg = str(exc_info.value)
        assert "Step 2" in msg
        assert "write_file" in msg

    def test_multiple_writes_without_read_all_reported(self):
        steps = [
            _call("write_file", path="a.py", content="a"),
            _call("replace_in_file", path="b.py", old="x", new="y"),
        ]
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(steps)
        msg = str(exc_info.value)
        assert "write_file" in msg
        assert "replace_in_file" in msg

    def test_destructive_write_tools_constant_covers_expected_set(self):
        assert DESTRUCTIVE_WRITE_TOOLS == frozenset(
            {"write_file", "replace_in_file", "edit_lines", "patch_file"}
        )

    def test_create_file_is_not_in_destructive_write_tools(self):
        assert "create_file" not in DESTRUCTIVE_WRITE_TOOLS
