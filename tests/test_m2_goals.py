"""
M2 Goals — focused regression tests.

G1  Plan failure recovery: specific error types, retry limit, malformed JSON.
G3  Workspace isolation: _build_index no longer calls os.chdir().
G5  create_file safety: workspace boundary enforced at tool level.
G7  Patch atomicity: covered in test_patch_manager.py.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.agent.executor import AutonomousExecutor
from src.agent.plan_validator import PlanValidationError
from src.llm.client import LLMCancelled
from src.llm.parser import ToolCall

# ─────────────────────────────────────────────────────────────────────────────
# G1 — Plan failure recovery
# ─────────────────────────────────────────────────────────────────────────────


def _executor() -> AutonomousExecutor:
    planner = MagicMock()
    dispatcher = MagicMock()
    return AutonomousExecutor(planner, dispatcher)


def _tc(name="read_file") -> ToolCall:
    tc = MagicMock(spec=ToolCall)
    tc.tool_name = name
    tc.kwargs = {"path": "src/main.py"}
    tc.step_id = None
    tc.depends_on = []
    return tc


class TestPlanRetryMalformedJSON:
    """G1: malformed JSON from the model triggers one retry."""

    def test_malformed_json_triggers_retry(self):
        executor = _executor()
        tc = _tc()
        executor.planner.plan.side_effect = json.JSONDecodeError("No JSON", "", 0)
        executor.planner.replan.return_value = [tc]

        result = executor._plan_with_retry("fix bug", "", [], [])
        assert result == [tc]
        executor.planner.replan.assert_called_once()

    def test_replan_receives_error_message(self):
        executor = _executor()
        executor.planner.plan.side_effect = json.JSONDecodeError("Unexpected token", "", 5)
        executor.planner.replan.return_value = [_tc()]

        executor._plan_with_retry("find bug", "", [], [])

        call_kw = executor.planner.replan.call_args
        failed = call_kw[1].get("failed") or call_kw[0][2]
        # The actual error text must reach the replanner.
        assert "error" in failed and failed["error"]

    def test_both_malformed_json_attempts_fail_propagates(self):
        executor = _executor()
        executor.planner.plan.side_effect = json.JSONDecodeError("Bad", "", 0)
        executor.planner.replan.side_effect = json.JSONDecodeError("Still bad", "", 0)

        with pytest.raises(json.JSONDecodeError):
            executor._plan_with_retry("fix", "", [], [])


class TestPlanRetryValidationError:
    """G1: plan validation failures (PlanValidationError, ValueError) trigger retry."""

    def test_plan_validation_error_retried(self):
        executor = _executor()
        tc = _tc()
        executor.planner.plan.side_effect = PlanValidationError("step exceeds max")
        executor.planner.replan.return_value = [tc]

        result = executor._plan_with_retry("task", "", [], [])
        assert result == [tc]

    def test_unknown_tool_error_retried(self):
        executor = _executor()
        tc = _tc()
        executor.planner.plan.side_effect = ValueError("Unknown tool: fly_to_moon")
        executor.planner.replan.return_value = [tc]

        result = executor._plan_with_retry("task", "", [], [])
        assert result == [tc]

    def test_missing_required_field_error_retried(self):
        executor = _executor()
        tc = _tc()
        executor.planner.plan.side_effect = ValueError("missing required argument: path")
        executor.planner.replan.return_value = [tc]

        result = executor._plan_with_retry("task", "", [], [])
        assert result == [tc]


class TestPlanRetryLimitEnforced:
    """G1: retry is bounded — max 1 retry (plan + 1 replan = 2 attempts)."""

    def test_retry_limit_is_one_replan(self):
        """Only one replan attempt is made before giving up."""
        executor = _executor()
        executor.planner.plan.side_effect = ValueError("bad plan 1")
        executor.planner.replan.side_effect = ValueError("bad plan 2")

        with pytest.raises(ValueError, match="bad plan 2"):
            executor._plan_with_retry("task", "", [], [])

        executor.planner.plan.assert_called_once()
        executor.planner.replan.assert_called_once()

    def test_second_failure_propagates_replan_error_not_first(self):
        """The replanning exception (not the original) propagates to the caller."""
        executor = _executor()
        executor.planner.plan.side_effect = ValueError("first error")
        executor.planner.replan.side_effect = RuntimeError("replan error")

        with pytest.raises(RuntimeError, match="replan error"):
            executor._plan_with_retry("task", "", [], [])

    def test_cancellation_never_retried(self):
        """LLMCancelled propagates immediately without triggering a retry."""
        executor = _executor()
        executor.planner.plan.side_effect = LLMCancelled("cancelled")

        with pytest.raises(LLMCancelled):
            executor._plan_with_retry("task", "", [], [])

        executor.planner.replan.assert_not_called()

    def test_cancellation_in_replan_propagates(self):
        """LLMCancelled raised by replan also propagates immediately."""
        executor = _executor()
        executor.planner.plan.side_effect = ValueError("bad plan")
        executor.planner.replan.side_effect = LLMCancelled("cancelled in replan")

        with pytest.raises(LLMCancelled):
            executor._plan_with_retry("task", "", [], [])


class TestPlanRetrySuccessPath:
    """G1: retry succeeds on second attempt."""

    def test_retry_success_returns_valid_plan(self):
        executor = _executor()
        tc1 = _tc("read_file")
        tc2 = _tc("search_code")
        executor.planner.plan.side_effect = ValueError("plan failed")
        executor.planner.replan.return_value = [tc1, tc2]

        result = executor._plan_with_retry("explain code", "", [], [])
        assert result == [tc1, tc2]

    def test_first_success_skips_replan(self):
        executor = _executor()
        tc = _tc("read_file")
        executor.planner.plan.return_value = [tc]

        result = executor._plan_with_retry("explain code", "", [], [])
        assert result == [tc]
        executor.planner.replan.assert_not_called()

    def test_invalid_path_error_retried(self):
        executor = _executor()
        tc = _tc()
        executor.planner.plan.side_effect = PlanValidationError(
            "path '/etc/passwd' references a forbidden system path"
        )
        executor.planner.replan.return_value = [tc]

        result = executor._plan_with_retry("read config", "", [], [])
        assert result == [tc]


# ─────────────────────────────────────────────────────────────────────────────
# G3 — os.chdir() race: _build_index must NOT call os.chdir()
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildIndexNoChdirG3:
    def test_build_index_source_has_no_os_chdir(self):
        """
        _build_index must not call os.chdir() — that function is
        process-global and races with run_autonomous_stream() when both
        run in concurrent background threads.
        """
        from src.api.session import PearlSession
        source = inspect.getsource(PearlSession._build_index)
        assert "os.chdir" not in source, (
            "_build_index() still calls os.chdir() — G3 regression"
        )

    def test_build_index_passes_workspace_to_startup_index(self):
        """_build_index must use the explicit workspace path, not cwd."""
        from src.api.session import PearlSession
        source = inspect.getsource(PearlSession._build_index)
        # The workspace path (self.workspace) must be passed to build_startup_index.
        assert "self.workspace" in source or "workspace" in source


# ─────────────────────────────────────────────────────────────────────────────
# G5 — create_file safety: workspace boundary enforced at tool level
# ─────────────────────────────────────────────────────────────────────────────


class TestCreateFileSafety:
    """G5: create_file must enforce the workspace boundary at tool execution time."""

    def test_create_file_within_workspace_succeeds(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from src.tools.edit_tools import create_file, set_active_patch_manager

        # Ensure no patch manager is active (direct write mode).
        set_active_patch_manager(None)
        target = str(tmp_path / "safe.py")
        create_file(target, "x = 1\n")
        assert Path(target).exists()

    def test_create_file_outside_workspace_raises(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        import tempfile

        from src.tools.edit_tools import create_file, set_active_patch_manager

        set_active_patch_manager(None)
        outside = str(Path(tempfile.gettempdir()) / "escape.py")

        with pytest.raises(PermissionError):
            create_file(outside, "evil\n")

    def test_create_file_path_traversal_rejected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from src.tools.edit_tools import create_file, set_active_patch_manager

        set_active_patch_manager(None)
        traversal = str(tmp_path / ".." / "escape.py")

        with pytest.raises(PermissionError):
            create_file(traversal, "evil\n")

    def test_create_file_stages_in_preview_mode(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from src.tools.edit_tools import create_file, set_active_patch_manager
        from src.tools.patch_manager import ChangeManager

        pm = ChangeManager()
        set_active_patch_manager(pm)
        try:
            target = str(tmp_path / "preview.py")
            create_file(target, "x = 1\n")
            assert not Path(target).exists(), "File must not be on disk in preview mode"
            assert pm.has_pending(), "Edit must be staged in ChangeManager"
        finally:
            set_active_patch_manager(None)

    @pytest.mark.parametrize(
        "tool_name",
        [
            "create_file",
            "write_file",
            "append_file",
            "delete_file",
            "replace_in_file",
            "edit_lines",
            "patch_file",
        ],
    )
    def test_every_content_mutating_tool_stages_in_preview_mode(
        self, tool_name, tmp_path, monkeypatch
    ):
        """
        The approval invariant, applied to every registered tool that
        mutates file content — not just `create_file`.

        This is parametrized rather than written once because the gap it
        guards was exactly a per-tool one: `write_file`, `append_file`
        and `delete_file` were registered as agent tools while only the
        `edit_tools` module honoured the gate. A single-tool test passed
        the whole time. Add a row here when you add a write tool.
        """
        monkeypatch.chdir(tmp_path)

        from src.tools import edit_tools, file_tools
        from src.tools.edit_tools import set_active_patch_manager
        from src.tools.patch_manager import ChangeManager

        target = tmp_path / "subject.py"
        target.write_text("original = 1\n", encoding="utf-8")
        before = target.read_text(encoding="utf-8")

        # Each tool's minimal mutating call, and where it lives.
        calls = {
            "create_file": (edit_tools.create_file, (str(tmp_path / "fresh.py"), "x\n")),
            "write_file": (file_tools.write_file, (str(target), "replaced\n")),
            "append_file": (file_tools.append_file, (str(target), "added\n")),
            "delete_file": (file_tools.delete_file, (str(target),)),
            "replace_in_file": (
                edit_tools.replace_in_file,
                (str(target), "original", "changed"),
            ),
            "edit_lines": (edit_tools.edit_lines, (str(target), 1, 1, "changed = 2\n")),
            "patch_file": (
                edit_tools.patch_file,
                (str(target), "@@ -1 +1 @@\n-original = 1\n+changed = 2\n"),
            ),
        }

        func, args = calls[tool_name]

        pm = ChangeManager()
        set_active_patch_manager(pm)
        try:
            func(*args)

            assert pm.has_pending(), f"{tool_name} must stage its change"
            assert target.read_text(encoding="utf-8") == before, (
                f"{tool_name} modified the file on disk while the approval "
                f"gate was active"
            )
            assert not (tmp_path / "fresh.py").exists(), (
                f"{tool_name} created a file on disk while the approval "
                f"gate was active"
            )
        finally:
            set_active_patch_manager(None)

    def test_create_file_existing_raises(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from src.tools.edit_tools import create_file, set_active_patch_manager

        set_active_patch_manager(None)
        target = tmp_path / "exists.py"
        target.write_text("original\n")

        with pytest.raises(FileExistsError):
            create_file(str(target), "override\n")

        assert target.read_text() == "original\n"
