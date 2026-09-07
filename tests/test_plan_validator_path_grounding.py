"""
Regression tests for Pearl's plan-time path-grounding checks.

These tests verify that validate_plan() catches hallucinated file extensions
before any tool call runs, so the executor never attempts non-existent paths.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.agent.plan_validator import PlanValidationError, validate_plan
from src.llm.parser import ToolCall


def _step(tool: str, **kwargs) -> ToolCall:
    return ToolCall(tool_name=tool, args=(), kwargs=kwargs)


# ---------------------------------------------------------------------------
# Helper: mock workspace extension scanner
# ---------------------------------------------------------------------------

def _with_exts(*extensions: str):
    """
    Return a context manager that makes _extensions_in_workspace() return
    exactly the given extensions (e.g. ".py", ".txt").
    """
    return patch(
        "src.agent.plan_validator._extensions_in_workspace",
        return_value=frozenset(extensions),
    )


# ---------------------------------------------------------------------------
# Extension hallucination detection
# ---------------------------------------------------------------------------

class TestExtensionHallucinationCheck:
    def test_java_extension_rejected_in_python_workspace(self):
        """src/Foo.java must be flagged in a .py-only workspace."""
        steps = [
            _step("read_file", path="src/utils.py"),
            _step("read_file", path="src/ParserRegistry.java"),
        ]
        with _with_exts(".py", ".txt", ".md"):
            with pytest.raises(PlanValidationError, match=r"\.java"):
                validate_plan(steps)

    def test_correct_extension_passes(self):
        """src/planner.py in a .py workspace must pass."""
        steps = [_step("read_file", path="src/agent/planner.py")]
        with _with_exts(".py", ".txt", ".md"):
            validate_plan(steps)  # must not raise

    def test_multiple_bad_extensions_reported(self):
        """All bad extensions should appear in one PlanValidationError."""
        steps = [
            _step("read_file", path="src/foo.py"),
            _step("read_file", path="src/Foo.java"),
            _step("write_file", path="src/Bar.rb", content="x"),
        ]
        with _with_exts(".py"):
            with pytest.raises(PlanValidationError) as exc_info:
                validate_plan(steps)
        msg = str(exc_info.value)
        assert ".java" in msg
        assert ".rb" in msg

    def test_fails_open_when_workspace_scan_returns_empty(self):
        """When _extensions_in_workspace returns empty (scan failed), no error."""
        steps = [_step("read_file", path="src/Anything.java")]
        with _with_exts():  # empty frozenset = scan failed / disabled
            validate_plan(steps)  # must not raise

    def test_files_without_extension_are_ignored(self):
        """Paths like 'Makefile' or 'LICENSE' have no extension — not checked."""
        steps = [_step("read_file", path="Makefile")]
        with _with_exts(".py"):
            validate_plan(steps)  # must not raise

    def test_create_file_not_checked(self):
        """create_file legitimately introduces new extensions — not checked."""
        steps = [
            _step("read_file", path="src/main.py"),
            _step("create_file", path="src/new_module.rs", content="fn main() {}"),
        ]
        with _with_exts(".py"):
            validate_plan(steps)  # must not raise

    def test_write_file_is_checked(self):
        """write_file is a destructive operation — extension IS checked."""
        steps = [
            _step("read_file", path="src/main.py"),
            _step("write_file", path="src/Main.java", content="public class Main {}"),
        ]
        with _with_exts(".py"):
            with pytest.raises(PlanValidationError, match=r"\.java"):
                validate_plan(steps)

    def test_replace_in_file_is_checked(self):
        steps = [
            _step("read_file", path="src/main.py"),
            _step(
                "replace_in_file",
                path="src/main.rb",
                old_string="x",
                new_string="y",
            ),
        ]
        with _with_exts(".py"):
            with pytest.raises(PlanValidationError, match=r"\.rb"):
                validate_plan(steps)

    def test_ts_extension_accepted_in_ts_workspace(self):
        steps = [_step("read_file", path="src/index.ts")]
        with _with_exts(".ts", ".js", ".json"):
            validate_plan(steps)  # must not raise


# ---------------------------------------------------------------------------
# Pre-existing checks must still work alongside the new one
# ---------------------------------------------------------------------------

class TestExistingChecksStillPass:
    def test_empty_plan_still_raises(self):
        with _with_exts(".py"):
            with pytest.raises(PlanValidationError, match="at least one step"):
                validate_plan([])

    def test_forbidden_path_still_detected(self):
        steps = [_step("read_file", path="/etc/passwd")]
        with _with_exts(".py"):
            with pytest.raises(PlanValidationError, match="forbidden"):
                validate_plan(steps)

    def test_read_before_write_still_enforced(self):
        steps = [_step("write_file", path="src/main.py", content="x")]
        with _with_exts(".py"):
            with pytest.raises(PlanValidationError, match="prior read_file"):
                validate_plan(steps)
