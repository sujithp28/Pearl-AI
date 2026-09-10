"""
Verification engine for Pearl — post-execution pipeline.

After AutonomousExecutor applies patches, VerificationEngine compares
planned changes to actual changes, detects unexpected writes, selects
and runs targeted tests, and produces a structured VerificationResult.

Pipeline:
  planned_files → actual changes (git status) → unexpected detection
  → test selection → targeted test run → VerificationResult

Integration point: call verify() AFTER ChangeManager.apply_all() with
the list of paths that were staged (from ChangeManager.affected_files()
before apply).
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_GIT_TIMEOUT = 30
_TEST_TIMEOUT = 120


class VerificationStatus(str, Enum):
    SUCCESS = "SUCCESS"
    TOOL_SUCCESS_BUT_TASK_UNVERIFIED = "TOOL_SUCCESS_BUT_TASK_UNVERIFIED"
    FAILED = "FAILED"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass(frozen=True)
class VerificationResult:
    status: VerificationStatus
    planned_files: tuple[str, ...]
    changed_files: tuple[str, ...]
    unexpected_files: tuple[str, ...]
    tests_run: int
    tests_passed: int
    tests_failed: int
    diff_summary: str
    evidence: tuple[str, ...]
    risk: RiskLevel
    confidence: float
    errors: tuple[str, ...]


class VerificationEngine:
    """
    Post-execution verification pipeline.

    Optional `index` and `graph` are the Phase 4/5 repository
    intelligence objects. When provided, test selection uses impact
    analysis from `RepositoryGraph.impact()`; without them, selection
    falls back to name-based heuristics (changed `src/foo.py` →
    `tests/test_foo.py`).
    """

    def __init__(
        self,
        workspace_root: Path | None = None,
        index: object | None = None,
        graph: object | None = None,
        test_timeout: int = _TEST_TIMEOUT,
    ) -> None:
        self._root = (workspace_root or Path.cwd()).resolve()
        self._index = index
        self._graph = graph
        self._test_timeout = test_timeout

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def verify(
        self,
        planned_files: Sequence[str],
    ) -> VerificationResult:
        """
        Run the full verification pipeline and return a structured
        result. `planned_files` is the list of paths that were staged in
        ChangeManager before apply (from `ChangeManager.affected_files()`).
        """
        errors: list[str] = []
        evidence: list[str] = []

        planned = tuple(self._normalize(p) for p in planned_files)

        changed, git_errors = self._changed_files()
        errors.extend(git_errors)

        diff_summary = self._diff_stat()

        planned_set = set(planned)
        unexpected = tuple(f for f in changed if f not in planned_set)

        evidence.append(f"planned={list(planned)}")
        evidence.append(f"changed={list(changed)}")
        if unexpected:
            evidence.append(f"unexpected={list(unexpected)}")

        test_files = self._select_tests(list(planned) + list(changed))
        evidence.append(f"tests_selected={test_files}")

        tests_run = tests_passed = tests_failed = 0
        if test_files:
            cmd = [
                "python",
                "-m",
                "pytest",
                *test_files,
                "--tb=short",
                "-q",
                "--no-header",
            ]
            evidence.append(f"test_cmd={' '.join(cmd)}")
            out, passed, failed, run_errors = self._run_tests(cmd)
            tests_run = passed + failed
            tests_passed = passed
            tests_failed = failed
            tail = out[-400:] if len(out) > 400 else out
            evidence.append(f"test_output={tail}")
            errors.extend(run_errors)

        risk = _compute_risk(planned, changed, unexpected, tests_failed)
        confidence = _compute_confidence(
            planned, changed, tests_run, tests_passed, unexpected
        )
        status = _determine_status(planned, changed, tests_failed)

        return VerificationResult(
            status=status,
            planned_files=planned,
            changed_files=changed,
            unexpected_files=unexpected,
            tests_run=tests_run,
            tests_passed=tests_passed,
            tests_failed=tests_failed,
            diff_summary=diff_summary,
            evidence=tuple(evidence),
            risk=risk,
            confidence=confidence,
            errors=tuple(errors),
        )

    # ------------------------------------------------------------------
    # Git helpers
    # ------------------------------------------------------------------

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            cwd=str(self._root),
        )

    def _changed_files(self) -> tuple[tuple[str, ...], list[str]]:
        errors: list[str] = []
        try:
            result = self._git("status", "--porcelain")
            if result.returncode != 0:
                errors.append(f"git status failed: {result.stderr.strip()}")
                return (), errors
            files: list[str] = []
            for line in result.stdout.splitlines():
                if len(line) >= 3:
                    path = line[3:].strip()
                    if path:
                        files.append(path)
            return tuple(files), errors
        except subprocess.TimeoutExpired:
            errors.append("git status timed out")
            return (), errors
        except Exception as exc:
            errors.append(f"git status error: {exc}")
            return (), errors

    def _diff_stat(self) -> str:
        try:
            result = self._git("diff", "--stat")
            return result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # Test selection
    # ------------------------------------------------------------------

    def _select_tests(self, files: list[str]) -> list[str]:
        tests_dir = self._root / "tests"
        if not tests_dir.exists():
            return []

        candidates: set[str] = set(files)

        if self._graph is not None:
            for f in list(candidates):
                try:
                    impacted = self._graph.impact(f)  # type: ignore[union-attr]
                    candidates.update(str(i) for i in impacted)
                except Exception:
                    pass

        seen: set[str] = set()
        selected: list[str] = []

        for file_path in candidates:
            stem = Path(file_path).stem
            candidate = tests_dir / f"test_{stem}.py"
            key = str(candidate)
            if key not in seen and candidate.exists():
                seen.add(key)
                try:
                    selected.append(str(candidate.relative_to(self._root)))
                except ValueError:
                    selected.append(str(candidate))

        return selected

    # ------------------------------------------------------------------
    # Test runner
    # ------------------------------------------------------------------

    def _run_tests(self, cmd: list[str]) -> tuple[str, int, int, list[str]]:
        errors: list[str] = []
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(self._root),
                timeout=self._test_timeout,
            )
            output = result.stdout + result.stderr
            passed, failed = _parse_pytest_counts(output)
            return output, passed, failed, errors
        except subprocess.TimeoutExpired:
            errors.append(f"tests timed out after {self._test_timeout}s")
            return "", 0, 0, errors
        except Exception as exc:
            errors.append(f"test runner error: {exc}")
            return "", 0, 0, errors

    # ------------------------------------------------------------------
    # Path normalisation
    # ------------------------------------------------------------------

    def _normalize(self, path: str) -> str:
        p = Path(path)
        if p.is_absolute():
            try:
                return str(p.relative_to(self._root))
            except ValueError:
                return path
        return path


# ---------------------------------------------------------------------------
# Pure functions — deterministic, no I/O, fully testable in isolation
# ---------------------------------------------------------------------------


def _parse_pytest_counts(output: str) -> tuple[int, int]:
    """Return (passed, failed) counts extracted from pytest summary output."""
    passed_m = re.search(r"(\d+) passed", output)
    failed_m = re.search(r"(\d+) failed", output)
    return (
        int(passed_m.group(1)) if passed_m else 0,
        int(failed_m.group(1)) if failed_m else 0,
    )


def _determine_status(
    planned: tuple[str, ...],
    changed: tuple[str, ...],
    tests_failed: int,
) -> VerificationStatus:
    if tests_failed > 0:
        return VerificationStatus.FAILED

    planned_set = set(planned)
    changed_set = set(changed)

    if not planned and not changed:
        return VerificationStatus.TOOL_SUCCESS_BUT_TASK_UNVERIFIED

    if planned and planned_set.isdisjoint(changed_set):
        return VerificationStatus.TOOL_SUCCESS_BUT_TASK_UNVERIFIED

    return VerificationStatus.SUCCESS


def _compute_risk(
    planned: tuple[str, ...],
    changed: tuple[str, ...],
    unexpected: tuple[str, ...],
    tests_failed: int,
) -> RiskLevel:
    if tests_failed > 0:
        return RiskLevel.HIGH

    critical_unexpected = [f for f in unexpected if f.startswith(("src/", "src\\"))]
    if critical_unexpected:
        return RiskLevel.HIGH

    if len(changed) > 5 or len(unexpected) > 2:
        return RiskLevel.HIGH

    if len(changed) >= 3 or len(unexpected) > 0:
        return RiskLevel.MEDIUM

    return RiskLevel.LOW


def _compute_confidence(
    planned: tuple[str, ...],
    changed: tuple[str, ...],
    tests_run: int,
    tests_passed: int,
    unexpected: tuple[str, ...],
) -> float:
    score = 1.0

    if planned:
        overlap = len(set(planned) & set(changed))
        score *= overlap / len(planned)

    if unexpected:
        score -= min(0.3, 0.1 * len(unexpected))

    if tests_run > 0:
        pass_rate = tests_passed / tests_run
        score = score * 0.5 + pass_rate * 0.5
    elif not changed:
        score *= 0.5

    return round(max(0.0, min(1.0, score)), 4)
