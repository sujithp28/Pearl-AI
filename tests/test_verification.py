"""
Tests for src/agent/verification.py — VerificationEngine.

Coverage targets:
  - All ten failure/edge scenarios per spec
  - Pure function unit tests (_parse_pytest_counts, _determine_status,
    _compute_risk, _compute_confidence)
  - VerificationResult immutability
  - Test selection heuristics
  - Evidence is machine-readable (file+line format, command strings)
  - Risk is deterministic from observable facts only
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from src.agent.verification import (
    RiskLevel,
    VerificationEngine,
    VerificationResult,
    VerificationStatus,
    _compute_confidence,
    _compute_risk,
    _determine_status,
    _parse_pytest_counts,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_git_mock(stdout: str = "", returncode: int = 0) -> MagicMock:
    return MagicMock(returncode=returncode, stdout=stdout, stderr="")


def _engine(tmp_path: Path, **kwargs: object) -> VerificationEngine:
    return VerificationEngine(workspace_root=tmp_path, **kwargs)


def _with_tests_dir(tmp_path: Path, *stems: str) -> list[str]:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(exist_ok=True)
    for stem in stems:
        (tests_dir / f"test_{stem}.py").write_text("# test stub")
    return [f"tests/test_{s}.py" for s in stems]


# ---------------------------------------------------------------------------
# VerificationResult: frozen dataclass
# ---------------------------------------------------------------------------


class TestVerificationResultIsFrozen:
    def test_result_fields_immutable(self) -> None:
        r = VerificationResult(
            status=VerificationStatus.SUCCESS,
            planned_files=("a.py",),
            changed_files=("a.py",),
            unexpected_files=(),
            tests_run=1,
            tests_passed=1,
            tests_failed=0,
            diff_summary="",
            evidence=("planned=['a.py']",),
            risk=RiskLevel.LOW,
            confidence=1.0,
            errors=(),
        )
        with pytest.raises((AttributeError, TypeError)):
            r.status = VerificationStatus.FAILED  # type: ignore[misc]

    def test_result_has_all_required_fields(self) -> None:
        r = VerificationResult(
            status=VerificationStatus.SUCCESS,
            planned_files=(),
            changed_files=(),
            unexpected_files=(),
            tests_run=0,
            tests_passed=0,
            tests_failed=0,
            diff_summary="",
            evidence=(),
            risk=RiskLevel.LOW,
            confidence=1.0,
            errors=(),
        )
        assert hasattr(r, "status")
        assert hasattr(r, "planned_files")
        assert hasattr(r, "changed_files")
        assert hasattr(r, "unexpected_files")
        assert hasattr(r, "tests_run")
        assert hasattr(r, "tests_passed")
        assert hasattr(r, "tests_failed")
        assert hasattr(r, "diff_summary")
        assert hasattr(r, "evidence")
        assert hasattr(r, "risk")
        assert hasattr(r, "confidence")
        assert hasattr(r, "errors")


# ---------------------------------------------------------------------------
# Failure scenario 1: tests fail → FAILED
# ---------------------------------------------------------------------------


class TestScenario1TestsFail:
    def test_status_is_failed_when_tests_fail(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        _with_tests_dir(tmp_path, "foo")

        with patch.object(engine, "_git") as mock_git:
            mock_git.side_effect = [
                _make_git_mock(" M foo.py\n"),   # status
                _make_git_mock("foo.py | 2 ++"),  # diff --stat
            ]
            with patch.object(engine, "_run_tests") as mock_run:
                mock_run.return_value = ("1 failed in 0.1s", 0, 1, [])
                result = engine.verify(["foo.py"])

        assert result.status == VerificationStatus.FAILED
        assert result.tests_failed == 1
        assert result.tests_passed == 0
        assert result.risk == RiskLevel.HIGH

    def test_failed_with_multiple_test_failures(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        _with_tests_dir(tmp_path, "bar")

        with patch.object(engine, "_git") as mock_git:
            mock_git.side_effect = [
                _make_git_mock(" M bar.py\n"),
                _make_git_mock(""),
            ]
            with patch.object(engine, "_run_tests") as mock_run:
                mock_run.return_value = ("3 passed, 5 failed in 1.0s", 3, 5, [])
                result = engine.verify(["bar.py"])

        assert result.status == VerificationStatus.FAILED
        assert result.tests_failed == 5
        assert result.tests_passed == 3
        assert result.tests_run == 8


# ---------------------------------------------------------------------------
# Failure scenario 2: planned file not written → TOOL_SUCCESS_BUT_TASK_UNVERIFIED
# ---------------------------------------------------------------------------


class TestScenario2PlannedFilesNotWritten:
    def test_unverified_when_planned_file_missing_from_changed(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)

        with patch.object(engine, "_git") as mock_git:
            mock_git.side_effect = [
                _make_git_mock(""),  # nothing changed
                _make_git_mock(""),
            ]
            result = engine.verify(["planned.py"])

        assert result.status == VerificationStatus.TOOL_SUCCESS_BUT_TASK_UNVERIFIED
        assert "planned.py" in result.planned_files
        assert result.changed_files == ()

    def test_unverified_when_different_file_changed_than_planned(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)

        with patch.object(engine, "_git") as mock_git:
            mock_git.side_effect = [
                _make_git_mock(" M other.py\n"),  # not the planned one
                _make_git_mock(""),
            ]
            result = engine.verify(["planned.py"])

        assert result.status == VerificationStatus.TOOL_SUCCESS_BUT_TASK_UNVERIFIED
        assert "other.py" in result.unexpected_files


# ---------------------------------------------------------------------------
# Failure scenario 3: unexpected files in src/ → HIGH risk
# ---------------------------------------------------------------------------


class TestScenario3UnexpectedSrcFiles:
    def test_high_risk_when_unexpected_src_file(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)

        with patch.object(engine, "_git") as mock_git:
            mock_git.side_effect = [
                _make_git_mock(" M planned.py\n M src/unplanned.py\n"),
                _make_git_mock(""),
            ]
            result = engine.verify(["planned.py"])

        assert "src/unplanned.py" in result.unexpected_files
        assert result.risk == RiskLevel.HIGH

    def test_unexpected_file_appears_in_evidence(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)

        with patch.object(engine, "_git") as mock_git:
            mock_git.side_effect = [
                _make_git_mock(" M planned.py\n M src/agent/secret.py\n"),
                _make_git_mock(""),
            ]
            result = engine.verify(["planned.py"])

        assert any("unexpected" in ev for ev in result.evidence)


# ---------------------------------------------------------------------------
# Failure scenario 4: git command fails → errors captured, still returns result
# ---------------------------------------------------------------------------


class TestScenario4GitFails:
    def test_git_failure_captured_in_errors(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)

        with patch.object(engine, "_git") as mock_git:
            mock_git.side_effect = [
                MagicMock(returncode=128, stdout="", stderr="not a git repository"),
                _make_git_mock(""),
            ]
            result = engine.verify(["foo.py"])

        assert any("git status failed" in e for e in result.errors)
        assert isinstance(result.status, VerificationStatus)

    def test_git_failure_leads_to_unverified(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)

        with patch.object(engine, "_git") as mock_git:
            mock_git.side_effect = [
                MagicMock(returncode=1, stdout="", stderr="fatal error"),
                _make_git_mock(""),
            ]
            result = engine.verify(["some.py"])

        assert result.status == VerificationStatus.TOOL_SUCCESS_BUT_TASK_UNVERIFIED


# ---------------------------------------------------------------------------
# Failure scenario 5: git status timeout → errors captured
# ---------------------------------------------------------------------------


class TestScenario5GitTimeout:
    def test_git_timeout_captured_in_errors(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)

        with patch.object(engine, "_git") as mock_git:
            mock_git.side_effect = subprocess.TimeoutExpired(
                ["git", "status"], _timeout := 30
            )
            result = engine.verify(["foo.py"])

        assert any("timed out" in e for e in result.errors)

    def test_git_timeout_returns_empty_changed_files(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)

        with patch.object(engine, "_git") as mock_git:
            mock_git.side_effect = subprocess.TimeoutExpired(["git"], 30)
            result = engine.verify(["foo.py"])

        assert result.changed_files == ()


# ---------------------------------------------------------------------------
# Failure scenario 6: nothing planned, nothing changed → UNVERIFIED
# ---------------------------------------------------------------------------


class TestScenario6NothingPlannedNothingChanged:
    def test_unverified_when_both_empty(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)

        with patch.object(engine, "_git") as mock_git:
            mock_git.side_effect = [_make_git_mock(""), _make_git_mock("")]
            result = engine.verify([])

        assert result.status == VerificationStatus.TOOL_SUCCESS_BUT_TASK_UNVERIFIED
        assert result.planned_files == ()
        assert result.changed_files == ()


# ---------------------------------------------------------------------------
# Failure scenario 7: test runner timeout → errors captured
# ---------------------------------------------------------------------------


class TestScenario7TestRunnerTimeout:
    def test_test_timeout_captured(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path, test_timeout=5)
        _with_tests_dir(tmp_path, "foo")

        with patch.object(engine, "_git") as mock_git:
            mock_git.side_effect = [
                _make_git_mock(" M foo.py\n"),
                _make_git_mock(""),
            ]
            with patch.object(engine, "_run_tests") as mock_run:
                mock_run.return_value = ("", 0, 0, ["tests timed out after 5s"])
                result = engine.verify(["foo.py"])

        assert any("timed out" in e for e in result.errors)
        assert result.tests_run == 0


# ---------------------------------------------------------------------------
# Failure scenario 8: multiple unexpected files → HIGH risk
# ---------------------------------------------------------------------------


class TestScenario8ManyUnexpectedFiles:
    def test_three_unexpected_files_high_risk(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        git_out = " M planned.py\n M extra1.py\n M extra2.py\n M extra3.py\n"

        with patch.object(engine, "_git") as mock_git:
            mock_git.side_effect = [_make_git_mock(git_out), _make_git_mock("")]
            result = engine.verify(["planned.py"])

        assert len(result.unexpected_files) == 3
        assert result.risk == RiskLevel.HIGH

    def test_unexpected_file_set_never_silently_empty(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        git_out = " M a.py\n M b.py\n"

        with patch.object(engine, "_git") as mock_git:
            mock_git.side_effect = [_make_git_mock(git_out), _make_git_mock("")]
            result = engine.verify(["a.py"])  # b.py is unexpected

        assert "b.py" in result.unexpected_files


# ---------------------------------------------------------------------------
# Failure scenario 9: no test files found → no tests run, unverified possible
# ---------------------------------------------------------------------------


class TestScenario9NoTestsFound:
    def test_no_tests_run_when_no_test_files(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        # tests/ dir doesn't exist

        with patch.object(engine, "_git") as mock_git:
            mock_git.side_effect = [
                _make_git_mock(" M foo.py\n"),
                _make_git_mock(""),
            ]
            result = engine.verify(["foo.py"])

        assert result.tests_run == 0
        assert result.tests_passed == 0

    def test_evidence_records_empty_test_selection(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)

        with patch.object(engine, "_git") as mock_git:
            mock_git.side_effect = [
                _make_git_mock(" M foo.py\n"),
                _make_git_mock(""),
            ]
            result = engine.verify(["foo.py"])

        assert any("tests_selected=[]" in ev for ev in result.evidence)


# ---------------------------------------------------------------------------
# Failure scenario 10: partial overlap — some planned files not written
# ---------------------------------------------------------------------------


class TestScenario10PartialWrite:
    def test_success_when_at_least_one_planned_file_changed(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)

        with patch.object(engine, "_git") as mock_git:
            # Only a.py was actually written; b.py was planned but not written
            mock_git.side_effect = [
                _make_git_mock(" M a.py\n"),
                _make_git_mock(""),
            ]
            result = engine.verify(["a.py", "b.py"])

        assert result.status == VerificationStatus.SUCCESS
        assert "a.py" in result.changed_files

    def test_unverified_when_none_of_planned_files_were_changed(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)

        with patch.object(engine, "_git") as mock_git:
            mock_git.side_effect = [
                _make_git_mock(""),  # neither a.py nor b.py changed
                _make_git_mock(""),
            ]
            result = engine.verify(["a.py", "b.py"])

        assert result.status == VerificationStatus.TOOL_SUCCESS_BUT_TASK_UNVERIFIED


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


class TestSuccessPath:
    def test_success_all_planned_changed_tests_pass(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        _with_tests_dir(tmp_path, "foo")

        with patch.object(engine, "_git") as mock_git:
            mock_git.side_effect = [
                _make_git_mock(" M foo.py\n"),
                _make_git_mock("foo.py | 2 +-\n 1 file changed"),
            ]
            with patch.object(engine, "_run_tests") as mock_run:
                mock_run.return_value = ("3 passed in 0.1s", 3, 0, [])
                result = engine.verify(["foo.py"])

        assert result.status == VerificationStatus.SUCCESS
        assert result.tests_passed == 3
        assert result.tests_failed == 0
        assert result.risk == RiskLevel.LOW
        assert result.diff_summary != ""

    def test_success_confidence_is_high_when_clean(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        _with_tests_dir(tmp_path, "bar")

        with patch.object(engine, "_git") as mock_git:
            mock_git.side_effect = [
                _make_git_mock(" M bar.py\n"),
                _make_git_mock(""),
            ]
            with patch.object(engine, "_run_tests") as mock_run:
                mock_run.return_value = ("5 passed", 5, 0, [])
                result = engine.verify(["bar.py"])

        assert result.confidence > 0.5


# ---------------------------------------------------------------------------
# Evidence is machine-readable
# ---------------------------------------------------------------------------


class TestEvidenceFormat:
    def test_evidence_includes_planned_list(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)

        with patch.object(engine, "_git") as mock_git:
            mock_git.side_effect = [
                _make_git_mock(" M foo.py\n"),
                _make_git_mock(""),
            ]
            result = engine.verify(["foo.py"])

        assert any(ev.startswith("planned=") for ev in result.evidence)

    def test_evidence_includes_changed_list(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)

        with patch.object(engine, "_git") as mock_git:
            mock_git.side_effect = [
                _make_git_mock(" M foo.py\n"),
                _make_git_mock(""),
            ]
            result = engine.verify(["foo.py"])

        assert any(ev.startswith("changed=") for ev in result.evidence)

    def test_evidence_includes_test_command(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        _with_tests_dir(tmp_path, "foo")

        with patch.object(engine, "_git") as mock_git:
            mock_git.side_effect = [
                _make_git_mock(" M foo.py\n"),
                _make_git_mock(""),
            ]
            with patch.object(engine, "_run_tests") as mock_run:
                mock_run.return_value = ("1 passed", 1, 0, [])
                result = engine.verify(["foo.py"])

        assert any(ev.startswith("test_cmd=") for ev in result.evidence)


# ---------------------------------------------------------------------------
# Path normalisation
# ---------------------------------------------------------------------------


class TestPathNormalisation:
    def test_absolute_planned_path_normalised_to_relative(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        absolute = str(tmp_path / "foo.py")

        with patch.object(engine, "_git") as mock_git:
            mock_git.side_effect = [
                _make_git_mock(" M foo.py\n"),
                _make_git_mock(""),
            ]
            result = engine.verify([absolute])

        assert "foo.py" in result.planned_files
        # Should not contain the tmp_path prefix
        assert not any(p.startswith(str(tmp_path)) for p in result.planned_files)

    def test_relative_planned_path_unchanged(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)

        with patch.object(engine, "_git") as mock_git:
            mock_git.side_effect = [_make_git_mock(""), _make_git_mock("")]
            result = engine.verify(["src/agent/foo.py"])

        assert "src/agent/foo.py" in result.planned_files


# ---------------------------------------------------------------------------
# Pure function unit tests
# ---------------------------------------------------------------------------


class TestParsePytestCounts:
    def test_parses_passed_and_failed(self) -> None:
        assert _parse_pytest_counts("3 passed, 2 failed in 0.5s") == (3, 2)

    def test_parses_only_passed(self) -> None:
        assert _parse_pytest_counts("5 passed in 1.0s") == (5, 0)

    def test_parses_only_failed(self) -> None:
        assert _parse_pytest_counts("4 failed in 0.2s") == (0, 4)

    def test_returns_zeros_for_no_match(self) -> None:
        assert _parse_pytest_counts("no tests collected") == (0, 0)

    def test_parses_large_counts(self) -> None:
        assert _parse_pytest_counts("100 passed, 12 failed") == (100, 12)


class TestDetermineStatus:
    def test_failed_when_tests_fail(self) -> None:
        assert _determine_status(("a.py",), ("a.py",), 1) == VerificationStatus.FAILED

    def test_unverified_when_both_empty(self) -> None:
        assert (
            _determine_status((), (), 0)
            == VerificationStatus.TOOL_SUCCESS_BUT_TASK_UNVERIFIED
        )

    def test_unverified_when_planned_disjoint_from_changed(self) -> None:
        assert (
            _determine_status(("planned.py",), ("other.py",), 0)
            == VerificationStatus.TOOL_SUCCESS_BUT_TASK_UNVERIFIED
        )

    def test_success_when_planned_in_changed(self) -> None:
        assert _determine_status(("a.py",), ("a.py", "b.py"), 0) == VerificationStatus.SUCCESS

    def test_success_when_no_planned_but_files_changed(self) -> None:
        # Nothing was explicitly planned (e.g., shell-only run) but files changed
        assert _determine_status((), ("a.py",), 0) == VerificationStatus.SUCCESS


class TestComputeRisk:
    def test_high_when_tests_failed(self) -> None:
        assert _compute_risk(("a.py",), ("a.py",), (), 1) == RiskLevel.HIGH

    def test_high_when_unexpected_src_file(self) -> None:
        assert (
            _compute_risk(("a.py",), ("a.py", "src/foo.py"), ("src/foo.py",), 0)
            == RiskLevel.HIGH
        )

    def test_high_when_more_than_five_changed(self) -> None:
        changed = tuple(f"f{i}.py" for i in range(6))
        assert _compute_risk(changed, changed, (), 0) == RiskLevel.HIGH

    def test_high_when_more_than_two_unexpected(self) -> None:
        unexpected = ("a.py", "b.py", "c.py")
        assert _compute_risk(("x.py",), ("x.py",) + unexpected, unexpected, 0) == RiskLevel.HIGH

    def test_medium_when_three_changed(self) -> None:
        changed = ("a.py", "b.py", "c.py")
        assert _compute_risk(changed, changed, (), 0) == RiskLevel.MEDIUM

    def test_medium_when_one_unexpected_non_src(self) -> None:
        assert _compute_risk(("a.py",), ("a.py", "docs/note.md"), ("docs/note.md",), 0) == RiskLevel.MEDIUM

    def test_low_when_one_file_no_unexpected_no_failure(self) -> None:
        assert _compute_risk(("a.py",), ("a.py",), (), 0) == RiskLevel.LOW


class TestComputeConfidence:
    def test_full_confidence_when_all_planned_changed_tests_pass(self) -> None:
        score = _compute_confidence(("a.py",), ("a.py",), 5, 5, ())
        assert score == 1.0

    def test_confidence_zero_when_no_overlap_and_no_tests(self) -> None:
        score = _compute_confidence(("a.py",), (), 0, 0, ())
        # planned but nothing changed → 0 overlap → 0 * anything + no test penalty
        assert score == 0.0

    def test_unexpected_files_reduce_confidence(self) -> None:
        base = _compute_confidence(("a.py",), ("a.py",), 0, 0, ())
        with_unexpected = _compute_confidence(
            ("a.py",), ("a.py", "extra.py"), 0, 0, ("extra.py",)
        )
        assert with_unexpected < base

    def test_failing_tests_reduce_confidence(self) -> None:
        all_pass = _compute_confidence(("a.py",), ("a.py",), 5, 5, ())
        some_fail = _compute_confidence(("a.py",), ("a.py",), 5, 3, ())
        assert some_fail < all_pass

    def test_confidence_bounded_zero_to_one(self) -> None:
        score = _compute_confidence(
            ("a.py", "b.py", "c.py"),
            (),
            0, 0,
            ("x.py", "y.py", "z.py", "w.py"),
        )
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# Test selection
# ---------------------------------------------------------------------------


class TestTestSelection:
    def test_selects_test_file_matching_changed_stem(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        _with_tests_dir(tmp_path, "executor")

        selected = engine._select_tests(["src/agent/executor.py"])

        assert any("test_executor.py" in f for f in selected)

    def test_no_selection_when_tests_dir_missing(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        selected = engine._select_tests(["foo.py"])
        assert selected == []

    def test_no_duplicate_test_files_in_selection(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        _with_tests_dir(tmp_path, "foo")

        selected = engine._select_tests(["foo.py", "foo.py", "src/foo.py"])

        assert len(selected) == len(set(selected))

    def test_skips_stems_with_no_matching_test(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        _with_tests_dir(tmp_path, "bar")

        selected = engine._select_tests(["nonexistent_module.py"])

        assert selected == []
