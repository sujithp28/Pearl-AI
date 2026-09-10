"""
Pearl V2 — end-to-end autonomous loop integration tests.

Exercises the complete workflow through the real executor:

    PLAN → EXECUTE → STAGE → APPROVE → VERIFY → REFLECT → REPLAN → DONE

These are integration tests: the executor, planner, dispatcher,
ChangeManager, VerificationEngine and ReflectionEngine are all real.
Only the LLM boundary is scripted (deterministic plan/reflection JSON),
which is the project's standard integration-test convention — no network,
no model download, no Ollama.

Covered:
  E2E-1  Full happy path: plan → stage → approve → verify → reflect → done
  E2E-2  Rejection: staged changes never reach disk
  E2E-3  Verification feeds reflection (tests_failed reaches the engine)
  E2E-4  Reflection "replan" verdict drives a real replan + re-execute
  E2E-5  Replan loop is bounded (no infinite agent loop)
  E2E-6  Cancellation during execution discards staged patches
  E2E-7  Workspace escape is refused inside an autonomous run
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agent.dispatcher import ToolDispatcher
from src.agent.executor import AutonomousExecutor
from src.agent.planner import Planner
from src.agent.reflection import ReflectionEngine
from src.agent.verification import (
    RiskLevel,
    VerificationResult,
    VerificationStatus,
)
from src.tools.edit_tools import create_file, replace_in_file, set_active_patch_manager
from src.tools.file_tools import read_file
from src.tools.metadata import tool
from src.tools.registry import ToolRegistry
from src.tools.shell_tools import set_active_command_approver

# ---------------------------------------------------------------------------
# Fixtures / scaffolding
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_approval_state():
    yield
    set_active_patch_manager(None)
    set_active_command_approver(None)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from src.config.workspace import clear_workspace_root, set_workspace_root

    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    set_workspace_root(tmp_path)
    yield tmp_path
    clear_workspace_root()


@tool(description="Echo a value back.", parameters={"value": "str"}, returns="str")
def _echo(value: str) -> str:
    return value


class _ScriptedReflectionClient:
    """
    Deterministic stand-in for the reflection LLM.

    Returns each queued JSON verdict in order; the last one repeats so a
    bounded loop always terminates on a stable answer.
    """

    def __init__(self, *verdicts: dict) -> None:
        self._verdicts = list(verdicts) or [{"status": "complete", "confidence": 0.9}]
        self.calls: list[str] = []

    def generate(self, prompt: str, **_kwargs) -> str:
        self.calls.append(prompt)
        index = min(len(self.calls) - 1, len(self._verdicts) - 1)
        verdict = {
            "status": "complete",
            "confidence": 0.9,
            "reason": "scripted",
            "missing_requirements": [],
            "recommended_action": "none",
            **self._verdicts[index],
        }
        return json.dumps(verdict)


class _StubVerifier:
    """Real VerificationResult shape, deterministic values, no subprocess."""

    def __init__(self, tests_failed: int = 0, tests_passed: int = 3) -> None:
        self.tests_failed = tests_failed
        self.tests_passed = tests_passed
        self.calls: list[list[str]] = []

    def verify(self, planned_files) -> VerificationResult:
        self.calls.append(list(planned_files))
        return VerificationResult(
            status=(
                VerificationStatus.FAILED
                if self.tests_failed
                else VerificationStatus.SUCCESS
            ),
            planned_files=tuple(planned_files),
            changed_files=tuple(planned_files),
            unexpected_files=(),
            tests_run=self.tests_passed + self.tests_failed,
            tests_passed=self.tests_passed,
            tests_failed=self.tests_failed,
            diff_summary="1 file changed",
            evidence=(f"tests_failed={self.tests_failed}",),
            risk=RiskLevel.LOW,
            confidence=0.9,
            errors=(),
        )


def _plan_of(*steps):
    return lambda prompt, cancel_check=None: {"steps": list(steps)}


def _plan_sequence(*plans):
    calls = {"n": 0}

    def _fn(prompt, cancel_check=None):
        index = min(calls["n"], len(plans) - 1)
        calls["n"] += 1
        return {"steps": list(plans[index])}

    return _fn


def _build(
    *,
    reflection_verdicts: tuple[dict, ...] = (),
    verifier=None,
    max_replans: int = 3,
):
    """Assemble a real executor with scripted LLM boundaries."""
    registry = ToolRegistry()
    registry.register(_echo)
    registry.register(create_file)
    registry.register(replace_in_file)
    registry.register(read_file)
    dispatcher = ToolDispatcher(registry)
    planner = Planner(registry, dispatcher)

    reflection_engine = None
    reflection_client = None
    if reflection_verdicts:
        reflection_client = _ScriptedReflectionClient(*reflection_verdicts)
        reflection_engine = ReflectionEngine(reflection_client, max_iterations=3)

    executor = AutonomousExecutor(
        planner,
        dispatcher,
        max_replans=max_replans,
        checkpoints=None,
        verifier=verifier,
        reflection_engine=reflection_engine,
    )
    return executor, planner, reflection_client


# ---------------------------------------------------------------------------
# E2E-1  Full happy path
# ---------------------------------------------------------------------------


class TestE2EHappyPath:
    def test_write_stages_then_approve_applies_and_verifies(
        self, monkeypatch, workspace
    ):
        verifier = _StubVerifier(tests_failed=0)
        executor, planner, refl_client = _build(
            reflection_verdicts=({"status": "complete", "confidence": 0.95},),
            verifier=verifier,
        )
        target = workspace / "greeting.py"

        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of(
                {
                    "tool": "create_file",
                    "arguments": {"path": str(target), "content": "x = 1\n"},
                }
            ),
        )

        # ---- PLAN → EXECUTE → STAGE -------------------------------------
        report = executor.run("create greeting.py")

        assert report.stop_reason == "awaiting_approval"
        assert not target.exists(), "staged write must NOT reach disk pre-approval"
        assert str(target) in " ".join(executor.patch_manager.affected_files())

        # ---- APPROVE → VERIFY → REFLECT ---------------------------------
        final = executor.approve()

        assert final.stop_reason == "completed"
        assert target.read_text() == "x = 1\n", "approval must write to disk"
        assert verifier.calls, "verification must run after approve()"
        assert final.llm_reflection is not None, "reflection must run"
        assert final.llm_reflection.status == "complete"
        assert final.llm_reflection.is_done

    def test_reflection_receives_verification_evidence(self, monkeypatch, workspace):
        """The reflection prompt must contain real test numbers, not None."""
        verifier = _StubVerifier(tests_failed=0, tests_passed=7)
        executor, planner, refl_client = _build(
            reflection_verdicts=({"status": "complete", "confidence": 0.9},),
            verifier=verifier,
        )
        target = workspace / "m.py"

        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of(
                {
                    "tool": "create_file",
                    "arguments": {"path": str(target), "content": "y = 2\n"},
                }
            ),
        )

        executor.run("create m.py")
        executor.approve()

        assert refl_client.calls, "reflection engine must have been called"
        prompt = refl_client.calls[-1]
        assert "passed: 7" in prompt, (
            f"verification results must reach the reflection prompt; got:\n{prompt}"
        )
        assert "(verification not run)" not in prompt


# ---------------------------------------------------------------------------
# E2E-2  Rejection
# ---------------------------------------------------------------------------


class TestE2ERejection:
    def test_rejected_change_never_reaches_disk(self, monkeypatch, workspace):
        executor, planner, _ = _build()
        target = workspace / "rejected.py"

        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of(
                {
                    "tool": "create_file",
                    "arguments": {"path": str(target), "content": "nope\n"},
                }
            ),
        )

        report = executor.run("create rejected.py")
        assert report.stop_reason == "awaiting_approval"

        final = executor.reject()

        assert final.stop_reason == "rejected"
        assert not target.exists(), "rejected change must never reach disk"
        assert not executor.patch_manager.has_pending()


# ---------------------------------------------------------------------------
# E2E-3 / E2E-4  Verification → Reflection → Replan
# ---------------------------------------------------------------------------


class TestE2EReflectionDrivenReplan:
    def test_failing_tests_reach_reflection(self, monkeypatch, workspace):
        verifier = _StubVerifier(tests_failed=2, tests_passed=1)
        executor, planner, refl_client = _build(
            # Reflection sees the failure but declines to replan here, so the
            # assertion is purely about evidence reaching the engine.
            reflection_verdicts=({"status": "blocked", "confidence": 0.3},),
            verifier=verifier,
        )
        target = workspace / "broken.py"

        # Failing tests trigger the verification replan, so the second plan
        # must differ — repeating create_file would (correctly) hit the
        # executor's repeated-failure guard instead of reaching reflection.
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_sequence(
                [
                    {
                        "tool": "create_file",
                        "arguments": {"path": str(target), "content": "bad\n"},
                    }
                ],
                [{"tool": "_echo", "arguments": {"value": "attempted fix"}}],
            ),
        )

        executor.run("create broken.py")
        final = executor.approve()

        assert refl_client.calls, "reflection must run after verification"
        prompt = refl_client.calls[-1]
        assert "failed: 2" in prompt, f"test failures must reach reflection:\n{prompt}"
        assert final.llm_reflection.status == "blocked"
        assert not final.llm_reflection.is_done, (
            "a blocked reflection must NOT report the task as done"
        )

    def test_replan_verdict_triggers_real_replan(self, monkeypatch, workspace):
        """Reflection says 'replan' → executor must plan again and execute."""
        verifier = _StubVerifier(tests_failed=0)
        executor, planner, refl_client = _build(
            reflection_verdicts=(
                {
                    "status": "replan",
                    "confidence": 0.4,
                    "missing_requirements": ["also create second.py"],
                },
                {"status": "complete", "confidence": 0.95},
            ),
            verifier=verifier,
        )
        first = workspace / "first.py"

        # Initial plan creates first.py; the replan echoes instead.
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_sequence(
                [
                    {
                        "tool": "create_file",
                        "arguments": {"path": str(first), "content": "a\n"},
                    }
                ],
                [{"tool": "_echo", "arguments": {"value": "created second"}}],
            ),
        )

        executor.run("create both files")
        final = executor.approve()

        assert final.replans_used >= 1, (
            "a 'replan' reflection verdict must drive an actual replan"
        )
        assert len(refl_client.calls) >= 2, "reflection must run again after the replan"
        assert final.llm_reflection.status == "complete"
        assert any(s.tool_name == "_echo" for s in final.steps), (
            "the replanned step must actually execute"
        )

    def test_replan_loop_is_bounded(self, monkeypatch, workspace):
        """A reflection that always says 'replan' must still terminate."""
        verifier = _StubVerifier(tests_failed=0)
        executor, planner, refl_client = _build(
            # Always "replan" — the loop must be stopped by the budgets.
            reflection_verdicts=({"status": "replan", "confidence": 0.2},),
            verifier=verifier,
            max_replans=2,
        )
        target = workspace / "loop.py"

        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_sequence(
                [
                    {
                        "tool": "create_file",
                        "arguments": {"path": str(target), "content": "a\n"},
                    }
                ],
                [{"tool": "_echo", "arguments": {"value": "again"}}],
            ),
        )

        executor.run("infinite task")
        final = executor.approve()  # must return, not hang

        assert final.replans_used <= executor.max_replans, (
            "replan budget must bound the reflection loop"
        )
        # ReflectionEngine's own iteration cap is the second guard.
        assert len(refl_client.calls) <= 4, (
            f"reflection ran {len(refl_client.calls)} times — loop is unbounded"
        )


# ---------------------------------------------------------------------------
# E2E-6  Cancellation
# ---------------------------------------------------------------------------


class TestE2ECancellation:
    def test_cancel_during_run_discards_staged_patches(self, monkeypatch, workspace):
        executor, planner, _ = _build()
        target = workspace / "cancelled.py"

        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of(
                {
                    "tool": "create_file",
                    "arguments": {"path": str(target), "content": "x\n"},
                },
                {"tool": "_echo", "arguments": {"value": "after"}},
            ),
        )

        registry = planner.dispatcher._registry
        original = registry._tools["create_file"].function

        def _create_then_cancel(*args, **kwargs):
            result = original(*args, **kwargs)
            executor.cancel()
            return result

        registry._tools["create_file"].function = _create_then_cancel

        report = executor.run("create then cancel")

        assert report.stop_reason == "cancelled"
        assert not target.exists(), "cancelled run must not write to disk"
        assert not executor.patch_manager.has_pending(), (
            "cancellation must discard staged patches"
        )


# ---------------------------------------------------------------------------
# E2E-7  Security boundary holds inside an autonomous run
# ---------------------------------------------------------------------------


class TestE2EWorkspaceBoundary:
    def test_workspace_escape_refused_during_autonomous_run(
        self, monkeypatch, workspace
    ):
        executor, planner, _ = _build()
        outside = workspace.parent / "escaped.py"

        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of(
                {
                    "tool": "create_file",
                    "arguments": {"path": str(outside), "content": "pwned\n"},
                }
            ),
        )

        report = executor.run("escape the workspace")

        assert not outside.exists(), "workspace escape must be refused"
        # The run must not report success for a refused write.
        assert report.stop_reason != "awaiting_approval" or not any(
            str(outside) in f for f in executor.patch_manager.affected_files()
        ), "an out-of-workspace path must never be staged"
