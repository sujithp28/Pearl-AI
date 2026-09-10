"""
Tests for duplicate-write detection in validate_plan() — Milestone 2, G8.

A plan that writes the same path more than once is structurally invalid:
the second write silently clobbers the first within the same plan.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.agent.plan_validator import PlanValidationError, validate_plan
from src.llm.parser import ToolCall


def _step(tool: str, path: str | None = None) -> ToolCall:
    tc = ToolCall.__new__(ToolCall)
    tc.tool_name = tool
    tc.kwargs = {"path": path} if path else {}
    tc.step_id = None
    tc.depends_on = []
    return tc


# Suppress workspace scan so extension checks don't fire.
@pytest.fixture(autouse=True)
def _no_scan():
    with patch(
        "src.agent.plan_validator._extensions_in_workspace", return_value=frozenset()
    ):
        yield


class TestDuplicateWriteDetected:
    def test_write_file_twice_same_path_raises(self):
        steps = [
            _step("read_file", "src/foo.py"),
            _step("write_file", "src/foo.py"),
            _step("write_file", "src/foo.py"),
        ]
        with pytest.raises(PlanValidationError, match="already written"):
            validate_plan(steps)

    def test_create_file_then_write_same_path_raises(self):
        steps = [
            _step("create_file", "src/new.py"),
            _step("write_file", "src/new.py"),
        ]
        with pytest.raises(PlanValidationError, match="already written"):
            validate_plan(steps)

    def test_replace_in_file_twice_same_path_raises(self):
        steps = [
            _step("read_file", "src/a.py"),
            _step("replace_in_file", "src/a.py"),
            _step("replace_in_file", "src/a.py"),
        ]
        with pytest.raises(PlanValidationError, match="already written"):
            validate_plan(steps)


class TestDuplicateWriteNotFired:
    def test_different_paths_no_error(self):
        steps = [
            _step("read_file", "src/a.py"),
            _step("write_file", "src/a.py"),
            _step("read_file", "src/b.py"),
            _step("write_file", "src/b.py"),
        ]
        # Should not raise
        validate_plan(steps)

    def test_single_write_no_error(self):
        steps = [
            _step("read_file", "src/foo.py"),
            _step("write_file", "src/foo.py"),
        ]
        validate_plan(steps)

    def test_write_tool_no_path_arg_no_error(self):
        """A write tool without a 'path' kwarg must not raise."""
        tc = _step("write_file")
        tc.kwargs = {}  # no path
        steps = [_step("read_file", "src/x.py"), tc]
        validate_plan(steps)


class TestDuplicateWriteErrorMessage:
    def test_error_names_both_steps(self):
        steps = [
            _step("read_file", "src/foo.py"),
            _step("write_file", "src/foo.py"),  # step 2
            _step("edit_lines", "src/foo.py"),  # step 3
        ]
        with pytest.raises(PlanValidationError) as exc_info:
            validate_plan(steps)
        msg = str(exc_info.value)
        assert "src/foo.py" in msg
        assert "step 2" in msg.lower() or "already written at step" in msg
