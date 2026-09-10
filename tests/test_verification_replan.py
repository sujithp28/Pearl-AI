"""
Tests for P2 (verification replan) and P3 (dirty-commit detection)
from OPEN_SOURCE_ARCHITECTURE_REVIEW.

P2 — After approve(), if VerificationEngine reports test failures,
     AutonomousExecutor replans with the failure output, bounded by
     max_replans.

P3 — At the start of run(), AutonomousExecutor logs a warning if
     git reports uncommitted changes (non-fatal).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.agent.executor import AutonomousExecutor
from src.agent.verification import (
    RiskLevel,
    VerificationResult,
    VerificationStatus,
)
from src.llm.parser import ToolCall

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_vr(tests_failed: int = 0, status: VerificationStatus = VerificationStatus.SUCCESS) -> VerificationResult:
    return VerificationResult(
        status=status,
        planned_files=(),
        changed_files=(),
        unexpected_files=(),
        tests_run=tests_failed + 1,
        tests_passed=1 - min(tests_failed, 1),
        tests_failed=tests_failed,
        diff_summary="",
        evidence=("planned=[]", "changed=[]", f"test_output={tests_failed} failed"),
        risk=RiskLevel.HIGH if tests_failed else RiskLevel.LOW,
        confidence=0.0 if tests_failed else 1.0,
        errors=(),
    )


def _tc(name: str = "read_file") -> ToolCall:
    tc = MagicMock(spec=ToolCall)
    tc.tool_name = name
    tc.kwargs = {"path": "src/main.py"}
    tc.step_id = None
    tc.depends_on = []
    return tc


def _make_command_approver_mock():
    """Return a MagicMock that passes as a CommandApprovalManager for approve() tests."""
    m = MagicMock()
    m.has_pending.return_value = False
    m.approve_all.return_value = []
    m.pending = []  # MagicMock allows attribute assignment
    m.affected_commands.return_value = []
    m.discard_all.return_value = []
    return m


def _make_executor(verifier=None, max_replans: int = 3) -> AutonomousExecutor:
    planner = MagicMock()
    dispatcher = MagicMock()
    command_approver = _make_command_approver_mock()
    return AutonomousExecutor(
        planner,
        dispatcher,
        max_replans=max_replans,
        command_approver=command_approver,
        verifier=verifier,
    )


# ---------------------------------------------------------------------------
# P2 — Verification replan
# ---------------------------------------------------------------------------


class TestVerificationReplan:
    def test_no_verifier_skips_verification(self):
        """When verifier=None, approve() must not run any verification."""
        executor = _make_executor(verifier=None)

        # Manually construct paused state
        from src.agent.executor import _PausedState
        executor._paused = _PausedState(
            prompt="fix tests",
            pending=[],
            steps=[],
            events=[],
            completed_for_replan=[],
            replans_used=0,
            iteration=1,
        )

        executor.patch_manager.has_pending = MagicMock(return_value=False)
        executor.patch_manager.apply_all = MagicMock(return_value=[])

        with patch("src.agent.executor.set_active_patch_manager"), \
             patch("src.agent.executor.set_active_command_approver"), \
             patch("src.agent.executor.refresh_indexed_file"):
            executor.approve()

        # Planner.replan must not have been called (no verifier)
        executor.planner.replan.assert_not_called()

    def test_passing_tests_no_replan(self):
        """When tests pass, no replan is triggered."""
        verifier = MagicMock()
        verifier.verify.return_value = _make_vr(tests_failed=0)

        executor = _make_executor(verifier=verifier)

        from src.agent.executor import _PausedState
        executor._paused = _PausedState(
            prompt="task",
            pending=[],
            steps=[],
            events=[],
            completed_for_replan=[],
            replans_used=0,
            iteration=1,
        )

        executor.patch_manager.has_pending = MagicMock(return_value=False)
        executor.patch_manager.apply_all = MagicMock(return_value=["src/main.py"])
        executor.command_approver.has_pending = MagicMock(return_value=[])
        executor.command_approver.approve_all = MagicMock(return_value=[])
        executor.command_approver.pending = []

        with patch("src.agent.executor.set_active_patch_manager"), \
             patch("src.agent.executor.set_active_command_approver"), \
             patch("src.agent.executor.refresh_indexed_file"):
            executor.approve()

        executor.planner.replan.assert_not_called()

    def test_failing_tests_trigger_replan(self):
        """When tests fail, planner.replan() is called with test failure info."""
        verifier = MagicMock()
        verifier.verify.return_value = _make_vr(
            tests_failed=3, status=VerificationStatus.FAILED
        )

        executor = _make_executor(verifier=verifier, max_replans=3)
        fix_step = _tc("write_file")
        executor.planner.replan.return_value = [fix_step]

        from src.agent.executor import _PausedState
        executor._paused = _PausedState(
            prompt="implement feature",
            pending=[],
            steps=[],
            events=[],
            completed_for_replan=[],
            replans_used=0,
            iteration=2,
        )

        executor.patch_manager.has_pending = MagicMock(return_value=False)
        executor.patch_manager.apply_all = MagicMock(return_value=["src/foo.py"])
        executor.command_approver.has_pending = MagicMock(return_value=[])
        executor.command_approver.approve_all = MagicMock(return_value=[])
        executor.command_approver.pending = []

        with patch("src.agent.executor.set_active_patch_manager"), \
             patch("src.agent.executor.set_active_command_approver"), \
             patch("src.agent.executor.refresh_indexed_file"):
            executor.approve()

        executor.planner.replan.assert_called_once()
        call_kwargs = executor.planner.replan.call_args
        failed_arg = call_kwargs[1].get("failed") or call_kwargs[0][2]
        assert "test_failure" in str(failed_arg.get("error_type", ""))
        assert "3" in str(failed_arg.get("error", ""))

    def test_replan_increments_replans_used(self):
        """A verification replan increments replans_used to prevent loops."""
        verifier = MagicMock()
        verifier.verify.return_value = _make_vr(
            tests_failed=1, status=VerificationStatus.FAILED
        )

        executor = _make_executor(verifier=verifier, max_replans=3)
        executor.planner.replan.return_value = [_tc()]

        from src.agent.executor import _PausedState
        state = _PausedState(
            prompt="task",
            pending=[],
            steps=[],
            events=[],
            completed_for_replan=[],
            replans_used=0,
            iteration=1,
        )
        executor._paused = state

        executor.patch_manager.has_pending = MagicMock(return_value=False)
        executor.patch_manager.apply_all = MagicMock(return_value=["x.py"])
        executor.command_approver.has_pending = MagicMock(return_value=[])
        executor.command_approver.approve_all = MagicMock(return_value=[])
        executor.command_approver.pending = []

        with patch("src.agent.executor.set_active_patch_manager"), \
             patch("src.agent.executor.set_active_command_approver"), \
             patch("src.agent.executor.refresh_indexed_file"):
            executor.approve()

        # replans_used was 0, should have been incremented to 1 in state.
        # We can't inspect state after _paused is cleared, but we can check
        # replan was called exactly once (not zero, not more).
        executor.planner.replan.assert_called_once()

    def test_no_replan_when_max_replans_exhausted(self):
        """When replans_used == max_replans, verification replan is skipped."""
        verifier = MagicMock()
        verifier.verify.return_value = _make_vr(
            tests_failed=2, status=VerificationStatus.FAILED
        )

        executor = _make_executor(verifier=verifier, max_replans=2)

        from src.agent.executor import _PausedState
        executor._paused = _PausedState(
            prompt="task",
            pending=[],
            steps=[],
            events=[],
            completed_for_replan=[],
            replans_used=2,  # already at max
            iteration=3,
        )

        executor.patch_manager.has_pending = MagicMock(return_value=False)
        executor.patch_manager.apply_all = MagicMock(return_value=["f.py"])
        executor.command_approver.has_pending = MagicMock(return_value=[])
        executor.command_approver.approve_all = MagicMock(return_value=[])
        executor.command_approver.pending = []

        with patch("src.agent.executor.set_active_patch_manager"), \
             patch("src.agent.executor.set_active_command_approver"), \
             patch("src.agent.executor.refresh_indexed_file"):
            executor.approve()

        executor.planner.replan.assert_not_called()

    def test_replan_failure_is_non_fatal(self):
        """If verification replan itself throws, approve() still completes."""
        verifier = MagicMock()
        verifier.verify.return_value = _make_vr(
            tests_failed=1, status=VerificationStatus.FAILED
        )

        executor = _make_executor(verifier=verifier, max_replans=3)
        executor.planner.replan.side_effect = RuntimeError("LLM timeout")

        from src.agent.executor import _PausedState
        executor._paused = _PausedState(
            prompt="task",
            pending=[],
            steps=[],
            events=[],
            completed_for_replan=[],
            replans_used=0,
            iteration=1,
        )

        executor.patch_manager.has_pending = MagicMock(return_value=False)
        executor.patch_manager.apply_all = MagicMock(return_value=["f.py"])
        executor.command_approver.has_pending = MagicMock(return_value=[])
        executor.command_approver.approve_all = MagicMock(return_value=[])
        executor.command_approver.pending = []

        with patch("src.agent.executor.set_active_patch_manager"), \
             patch("src.agent.executor.set_active_command_approver"), \
             patch("src.agent.executor.refresh_indexed_file"):
            # Must not raise — replan failure is logged and swallowed
            report = executor.approve()

        assert report is not None


# ---------------------------------------------------------------------------
# P3 — Dirty-commit detection
# ---------------------------------------------------------------------------


class TestDirtyWorkspaceDetection:
    def test_clean_workspace_no_warning(self, caplog):
        """No warning logged when git reports a clean workspace."""
        executor = _make_executor()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            executor._warn_if_dirty_workspace()

        assert "uncommitted" not in caplog.text.lower()

    def test_dirty_workspace_logs_warning(self, caplog):
        """A warning is logged when git reports uncommitted changes."""
        import logging

        executor = _make_executor()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=" M src/main.py\n?? scratch.txt\n"
            )
            with caplog.at_level(logging.WARNING, logger="src.agent.executor"):
                executor._warn_if_dirty_workspace()

        assert "uncommitted" in caplog.text.lower()

    def test_git_unavailable_is_non_fatal(self):
        """If git is not available, _warn_if_dirty_workspace() must not raise."""
        executor = _make_executor()

        with patch("subprocess.run", side_effect=FileNotFoundError("git not found")):
            # Must not raise
            executor._warn_if_dirty_workspace()

    def test_git_timeout_is_non_fatal(self):
        """If git times out, _warn_if_dirty_workspace() must not raise."""
        import subprocess

        executor = _make_executor()

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 10)):
            executor._warn_if_dirty_workspace()

    def test_run_calls_warn_if_dirty(self):
        """AutonomousExecutor.run() calls _warn_if_dirty_workspace() before planning."""
        executor = _make_executor()
        executor.planner.plan.side_effect = RuntimeError("stop early")

        warn_called = []

        def _capture():
            warn_called.append(True)

        executor._warn_if_dirty_workspace = _capture

        with patch("src.agent.executor.set_active_patch_manager"), \
             patch("src.agent.executor.set_active_command_approver"):
            try:
                executor.run("do something")
            except Exception:
                pass

        assert warn_called, "_warn_if_dirty_workspace() was not called by run()"
