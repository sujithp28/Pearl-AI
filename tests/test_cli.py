"""
Tests for the Pearl CLI (src/cli/).

The CLI is a thin client over the same executor the web UI and the VS
Code extension use, so these tests focus on what is genuinely new:
argument handling, exit codes, rendering, and — most importantly — that
the approval gate is not weakened by the convenience flags.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.cli import render
from src.cli.__main__ import (
    EXIT_BLOCKED,
    EXIT_FAILED,
    EXIT_OK,
    EXIT_REJECTED,
    _decide,
    _report_json,
    main,
)

# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


class TestRender:
    def test_diff_marks_additions_and_deletions(self):
        out = render.diff("--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n")
        assert "old" in out and "new" in out

    def test_diff_truncates_long_output(self):
        """A large refactor must not flood the terminal."""
        out = render.diff("\n".join(f"+line{i}" for i in range(500)), max_lines=10)
        assert "more line(s) not shown" in out

    def test_diff_handles_empty(self):
        assert "no textual diff" in render.diff("")

    def test_verification_reports_not_run_clearly(self):
        """Absent verification must read as 'not run', never as success."""
        out = render.verification(None)
        assert "not run" in out
        assert "SUCCESS" not in out

    def test_verification_shows_failed_tests(self):
        out = render.verification(
            {"status": "FAILED", "tests_run": 5, "tests_passed": 3, "tests_failed": 2}
        )
        assert "FAILED" in out
        assert "3/5" in out

    def test_verification_surfaces_unexpected_files(self):
        """A file changed but never planned is a correctness signal."""
        out = render.verification(
            {"status": "SUCCESS", "tests_run": 0, "unexpected_files": ["config.py"]}
        )
        assert "Unexpected" in out
        assert "config.py" in out

    def test_reflection_blocked_is_not_shown_as_done(self):
        refl = MagicMock(
            status="blocked",
            confidence=0.2,
            reason="tests still fail",
            missing_requirements=["fix auth"],
        )
        out = render.reflection(refl)
        assert "blocked" in out
        assert "fix auth" in out

    def test_reflection_not_run_is_explicit(self):
        assert "not run" in render.reflection(None)

    def test_steps_marks_failures(self):
        steps = [
            MagicMock(succeeded=True, tool_name="read_file", summary="ok"),
            MagicMock(succeeded=False, tool_name="write_file", summary="boom"),
        ]
        out = render.steps(steps)
        assert "read_file" in out and "write_file" in out


# ---------------------------------------------------------------------------
# Approval — the gate must not be weakened by CLI flags
# ---------------------------------------------------------------------------


class TestApprovalGate:
    def test_interactive_yes_applies(self):
        executor = MagicMock()
        with patch("builtins.input", return_value="y"):
            assert _decide(executor, auto_yes=False) is True

    @pytest.mark.parametrize("answer", ["n", "no", "", "maybe", "Y E S"])
    def test_anything_but_yes_rejects(self, answer):
        """Default-deny: only an explicit yes may write to disk."""
        executor = MagicMock()
        with patch("builtins.input", return_value=answer):
            assert _decide(executor, auto_yes=False) is False

    def test_eof_on_stdin_rejects(self):
        """
        Piped stdin with no answer must never be read as consent —
        silence is not approval for a filesystem write.
        """
        executor = MagicMock()
        with patch("builtins.input", side_effect=EOFError):
            assert _decide(executor, auto_yes=False) is False

    def test_yes_flag_defers_to_execution_policy(self, monkeypatch):
        """--yes routes through ExecutionPolicy rather than assuming consent."""
        executor = MagicMock()
        policy = MagicMock()
        policy.can_auto_approve.return_value = True
        policy.approval_reason.return_value = "headless"
        with patch("src.agent.headless.get_execution_policy", return_value=policy):
            assert _decide(executor, auto_yes=True) is True
        policy.can_auto_approve.assert_called_once_with("staged")

    def test_yes_flag_respects_a_blocking_policy(self):
        """
        In a mode that blocks staged writes (e.g. CI), --yes must not
        override the policy — otherwise the flag would be a bypass.
        """
        executor = MagicMock()
        policy = MagicMock()
        policy.can_auto_approve.return_value = False
        policy.approval_reason.return_value = "CI mode blocks staged writes"
        with patch("src.agent.headless.get_execution_policy", return_value=policy):
            assert _decide(executor, auto_yes=True) is False

    def test_no_flag_exists_to_skip_the_gate(self):
        """
        Guard against a future 'convenience' flag that writes without
        any approval decision at all.
        """
        import src.cli.__main__ as cli_main

        source = Path(cli_main.__file__).read_text(encoding="utf-8")
        for banned in ("--force", "--no-approval", "--skip-approval", "--unsafe"):
            assert banned not in source, (
                f"{banned} would bypass Pearl's approval guarantee"
            )


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


class TestJsonOutput:
    def _report(self, **kwargs):
        base = dict(
            succeeded=True,
            stop_reason="completed",
            replans_used=0,
            steps=[],
            llm_reflection=None,
        )
        base.update(kwargs)
        return MagicMock(**base)

    def test_output_is_valid_json(self):
        payload = _report_json(self._report(), None)
        json.dumps(payload)  # must not raise

    def test_includes_verification_and_reflection(self):
        refl = MagicMock(
            status="complete",
            confidence=0.9,
            reason="done",
            missing_requirements=[],
        )
        payload = _report_json(
            self._report(llm_reflection=refl),
            {"status": "SUCCESS", "tests_passed": 3, "tests_run": 3},
        )
        assert payload["verification"]["status"] == "SUCCESS"
        assert payload["reflection"]["status"] == "complete"

    def test_null_reflection_is_explicit(self):
        """Absent reflection must be null, not omitted or faked."""
        payload = _report_json(self._report(), None)
        assert payload["reflection"] is None
        assert payload["verification"] is None

    def test_failure_is_reported_honestly(self):
        payload = _report_json(
            self._report(succeeded=False, stop_reason="fatal_error"), None
        )
        assert payload["succeeded"] is False
        assert payload["stop_reason"] == "fatal_error"


# ---------------------------------------------------------------------------
# Argument handling
# ---------------------------------------------------------------------------


class TestMain:
    def test_rejects_missing_workspace(self, tmp_path, capsys):
        missing = tmp_path / "nope"
        assert main(["--workspace", str(missing), "task"]) == EXIT_FAILED
        assert "Not a directory" in capsys.readouterr().err

    def test_chat_requires_a_message(self, tmp_path, capsys):
        assert main(["--workspace", str(tmp_path), "--chat"]) == EXIT_FAILED
        assert "requires a message" in capsys.readouterr().err

    def test_sets_workspace_root_before_running(self, tmp_path):
        """
        Tools resolve every path against the workspace root, so it must
        be set before any tool can run — otherwise writes would be
        validated against the wrong directory.
        """
        seen: dict = {}

        def _capture(prompt, workspace, auto_yes, as_json):
            from src.config.workspace import get_workspace_root

            seen["root"] = get_workspace_root()
            return EXIT_OK

        with patch("src.cli.__main__._run_task", _capture):
            main(["--workspace", str(tmp_path), "do something"])

        assert seen["root"] == tmp_path.resolve()

    @pytest.mark.parametrize(
        "stop_reason,succeeded,expected",
        [
            ("completed", True, EXIT_OK),
            ("fatal_error", False, EXIT_FAILED),
            ("rejected", False, EXIT_REJECTED),
            ("awaiting_approval", False, EXIT_BLOCKED),
        ],
    )
    def test_exit_codes_match_outcome(
        self, tmp_path, stop_reason, succeeded, expected, monkeypatch
    ):
        """Exit codes are the CI contract — they must track the outcome."""
        from src.cli import __main__ as cli_main

        report = MagicMock(
            succeeded=succeeded,
            stop_reason=stop_reason,
            replans_used=0,
            steps=[],
            llm_reflection=None,
        )
        executor = MagicMock()
        executor.run.return_value = report
        executor.reject.return_value = MagicMock(
            succeeded=False,
            stop_reason="rejected",
            replans_used=0,
            steps=[],
            llm_reflection=None,
        )
        executor._last_verification = None

        monkeypatch.setattr(
            cli_main, "_build_executor", lambda ws, quiet: (executor, MagicMock())
        )
        # awaiting_approval would otherwise loop waiting for input
        monkeypatch.setattr(cli_main, "_show_pending", lambda e: None)
        monkeypatch.setattr(cli_main, "_decide", lambda e, y: False)

        result = cli_main._run_task("t", tmp_path, auto_yes=False, as_json=True)

        if stop_reason == "awaiting_approval":
            # Declining turns it into a rejection, which is the correct
            # terminal state rather than leaving the run stuck.
            assert result == EXIT_REJECTED
        else:
            assert result == expected
