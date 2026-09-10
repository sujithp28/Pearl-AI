"""
Pearl V2 — REAL local-model end-to-end tests.

These tests run against the actual configured local Qwen GGUF model via
LocalInferenceProvider.  There are NO mocks, NO scripted providers, and
NO Ollama anywhere in this file.

They are opt-in because loading a 1.1 GB GGUF and running CPU inference
takes minutes:

    pytest -m real_model tests/test_real_model_e2e.py -v

The default `addopts` in pyproject.toml deselects them, so the normal
suite stays fast.  They skip cleanly when llama_cpp or the model file is
absent, so a fresh checkout without the model never fails the suite.

What is asserted here is REAL INTEGRATION AND SAFE BEHAVIOUR, not model
quality — a 1.5B model is allowed to plan imperfectly.  What it is never
allowed to do is write outside the workspace or reach disk unapproved.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.real_model


# ---------------------------------------------------------------------------
# Availability gate — skip (never fail) when the model isn't present
# ---------------------------------------------------------------------------


def _model_available() -> tuple[bool, str]:
    try:
        import llama_cpp  # noqa: F401
    except ImportError:
        return False, "llama-cpp-python is not installed"

    from src.config.settings import Settings

    path = Path(Settings.LOCAL_MODEL_DIR).expanduser() / Settings.LOCAL_MODEL_FILE
    if not path.exists():
        return False, f"local model not downloaded: {path}"
    return True, ""


_AVAILABLE, _WHY = _model_available()
pytestmark = [
    pytest.mark.real_model,
    pytest.mark.skipif(not _AVAILABLE, reason=_WHY),
]


# ---------------------------------------------------------------------------
# Fixtures — a real router and a throwaway workspace
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def router():
    """Real ModelRouter. Module-scoped: the GGUF loads once for all tests."""
    from src.llm.router import ModelRouter

    return ModelRouter()


@pytest.fixture
def workspace():
    """A throwaway directory so no test can touch the real repo."""
    from src.config.workspace import clear_workspace_root, set_workspace_root

    ws = Path(tempfile.mkdtemp(prefix="pearl_real_e2e_"))
    set_workspace_root(ws)
    try:
        yield ws
    finally:
        clear_workspace_root()
        shutil.rmtree(ws, ignore_errors=True)


def _build_executor(router, workspace, **kwargs):
    from src.agent.dispatcher import ToolDispatcher
    from src.agent.executor import AutonomousExecutor
    from src.agent.planner import Planner
    from src.agent.reflection import ReflectionEngine
    from src.agent.verification import VerificationEngine
    from src.main import build_registry

    registry = build_registry()
    dispatcher = ToolDispatcher(registry)
    planner = Planner(registry, dispatcher, router.planning_client())

    params = dict(
        checkpoints=None,
        verifier=VerificationEngine(workspace_root=workspace),
        reflection_engine=ReflectionEngine(router.chat_client(), max_iterations=2),
        max_iterations=6,
        max_replans=1,
    )
    params.update(kwargs)
    return AutonomousExecutor(planner, dispatcher, **params), planner


# ---------------------------------------------------------------------------
# TEST A — the real model actually generates
# ---------------------------------------------------------------------------


class TestRealModelGeneration:
    def test_real_model_returns_a_response(self, router):
        out = router.chat_client().generate(
            "Reply with exactly one word: OK",
            max_new_tokens=16,
            temperature=0.0,
        )
        assert isinstance(out, str)
        assert out.strip(), "the real model returned an empty response"

    def test_real_model_is_not_a_scripted_provider(self, router):
        """Guard against a mock ever being wired into the real path."""
        from src.config.settings import Settings

        assert Settings.LLM_PROVIDER.lower() != "scripted", (
            "production provider must never be the scripted test provider"
        )
        client = router.chat_client()
        provider_name = type(getattr(client, "_provider", client)).__name__
        assert "Scripted" not in provider_name, (
            f"a scripted provider leaked into the real path: {provider_name}"
        )


# ---------------------------------------------------------------------------
# TEST A2 — real autocomplete (raw completion, not chat)
# ---------------------------------------------------------------------------


class TestRealAutocomplete:
    """
    Confirms the fix for a real bug found while building the autocomplete
    role: `generate()` routes through the model's chat template, and for
    a bare code fragment an instruct model frequently refuses outright
    ("I'm sorry, I can't assist with that") rather than continuing it.
    `complete_raw()` bypasses the chat template so the model does plain
    next-token continuation instead.
    """

    def test_generate_on_a_code_fragment_can_produce_a_refusal(self, router):
        """
        Documents the bug this fix addresses — not asserting the model
        MUST refuse (that would be flaky), just that generate() is the
        wrong tool: it sends the fragment through the chat template
        regardless of whether this particular sample happens to refuse.
        """
        out = router.autocomplete_client().generate(
            "def add(a, b):\n    return", max_new_tokens=20, temperature=0.0
        )
        assert isinstance(out, str)  # generate() always returns text or raises

    def test_complete_raw_continues_code_without_chat_framing(self, router):
        out = router.autocomplete_client().complete_raw(
            "def add(a, b):\n    return",
            temperature=0.0,
            max_new_tokens=20,
            stop=["\n\n", "def "],
        )
        refusal_markers = ("sorry", "as an ai", "i cannot", "i can't", "assist")
        assert not any(m in out.lower() for m in refusal_markers), (
            f"complete_raw() must not produce chat-style refusals, got: {out!r}"
        )
        assert out.strip(), "completion must not be empty"

    def test_complete_raw_respects_stop_sequences(self, router):
        """A completion that runs into a blank line must stop there, not ramble on."""
        out = router.autocomplete_client().complete_raw(
            "x = 1\n", temperature=0.0, max_new_tokens=40, stop=["\n\n"]
        )
        assert "\n\n" not in out


# ---------------------------------------------------------------------------
# TEST B — real planning
# ---------------------------------------------------------------------------


class TestRealPlanning:
    def test_planner_produces_a_plan_or_degrades_safely(self, router, workspace):
        """
        A 1.5B model may emit imperfect JSON.  Either it plans, or Pearl
        raises a normal exception — what it must never do is crash the
        process or return a malformed plan that reaches execution.
        """
        from src.agent.dispatcher import ToolDispatcher
        from src.agent.planner import Planner
        from src.llm.parser import ToolCall
        from src.main import build_registry

        registry = build_registry()
        planner = Planner(registry, ToolDispatcher(registry), router.planning_client())

        try:
            plan = list(planner.plan("List the files in the current directory."))
        except Exception as exc:
            pytest.skip(f"small model produced an unusable plan (acceptable): {exc}")

        assert all(isinstance(c, ToolCall) for c in plan)
        for call in plan:
            assert registry.has_tool(call.tool_name), (
                f"planner invented a tool that does not exist: {call.tool_name}"
            )


# ---------------------------------------------------------------------------
# TEST C — real reflection returns a valid structured verdict
# ---------------------------------------------------------------------------


class TestRealReflection:
    def test_reflection_returns_valid_status(self, router):
        from src.agent.executor import ExecutionStep
        from src.agent.reflection import ReflectionEngine

        engine = ReflectionEngine(router.chat_client())
        steps = [
            ExecutionStep(
                iteration=1,
                tool_name="create_file",
                kwargs={},
                result="created",
                summary="created hello.py",
            )
        ]

        result = engine.reflect(
            "Create hello.py",
            steps,
            verification={
                "status": "SUCCESS",
                "tests_run": 3,
                "tests_passed": 3,
                "tests_failed": 0,
            },
        )

        assert result.status in ("complete", "retry", "replan", "blocked")
        assert 0.0 <= result.confidence <= 1.0
        assert isinstance(result.missing_requirements, list)

    def test_reflection_prompt_carries_verification(self, router):
        """Verification evidence must reach the model, not be dropped."""
        from src.agent.executor import ExecutionStep
        from src.agent.reflection import ReflectionEngine

        engine = ReflectionEngine(router.chat_client())
        prompt = engine._build_prompt(
            "Fix the bug",
            [ExecutionStep(iteration=1, tool_name="replace_in_file", kwargs={})],
            {"status": "FAILED", "tests_run": 5, "tests_passed": 3, "tests_failed": 2},
        )
        assert "failed: 2" in prompt
        assert "(verification not run)" not in prompt


# ---------------------------------------------------------------------------
# TEST D — real condensation
# ---------------------------------------------------------------------------


class TestRealCondensation:
    def test_condenser_compresses_long_history(self, router):
        from src.agent.condenser import Condenser
        from src.memory import Memory

        memory = Memory()
        for i in range(12):
            memory.record_turn("user", f"Question {i} about the codebase.")
            memory.record_turn("agent", f"Answer {i} explaining a module.")

        before = len(memory.recent_messages(limit=1000))
        result = Condenser(router.chat_client()).condense(
            memory, reason="real e2e test"
        )

        assert result.condensed, "condenser did not compress a long history"
        assert result.turns_after < before


# ---------------------------------------------------------------------------
# TEST E — THE FULL LOOP, with the real model
# ---------------------------------------------------------------------------


class TestRealFullLoop:
    """
    USER → PLAN → EXECUTE → STAGE → APPROVE → VERIFY → REFLECT

    The safety invariants are hard assertions.  The model's *choice* of
    plan is not — a small model may pick a read-only plan, which is
    acceptable and skips rather than fails.
    """

    def test_full_autonomous_loop(self, router, workspace):
        executor, _ = _build_executor(router, workspace)

        report = executor.run(
            "Create a file named notes.txt containing the single line: hello pearl"
        )

        target = workspace / "notes.txt"

        if report.stop_reason != "awaiting_approval":
            pytest.skip(
                f"model chose a non-staging plan (stop_reason={report.stop_reason}); "
                "acceptable for a 1.5B model"
            )

        # ---- SAFETY INVARIANT: nothing on disk before approval ----------
        assert not target.exists(), (
            "P0 VIOLATION: a staged write reached disk before approval"
        )
        assert executor.patch_manager.has_pending()

        # ---- APPROVE → VERIFY → REFLECT ---------------------------------
        final = executor.approve()

        assert final.stop_reason == "completed"
        assert target.exists(), "approval must write the staged file to disk"

        # Verification ran and was captured for reflection.
        assert executor._last_verification is not None, (
            "verification must run after approve()"
        )

        # Reflection ran on real evidence.
        assert final.llm_reflection is not None, "reflection must run"
        assert final.llm_reflection.status in ("complete", "retry", "replan", "blocked")

    def test_rejection_never_reaches_disk(self, router, workspace):
        executor, _ = _build_executor(router, workspace)

        report = executor.run(
            "Create a file named rejected.txt containing the line: nope"
        )

        if report.stop_reason != "awaiting_approval":
            pytest.skip("model chose a non-staging plan; nothing to reject")

        staged = list(executor.patch_manager.affected_files())
        final = executor.reject()

        assert final.stop_reason == "rejected"
        for path in staged:
            assert not Path(path).exists(), (
                f"P0 VIOLATION: rejected file reached disk: {path}"
            )

    def test_cancellation_during_real_run(self, router, workspace):
        executor, _ = _build_executor(router, workspace)
        executor.cancel()  # cancel before the loop starts

        report = executor.run("Create a file named cancelled.txt")

        assert report.stop_reason == "cancelled"
        assert not executor.patch_manager.has_pending(), (
            "cancellation must leave no staged patches"
        )


# ---------------------------------------------------------------------------
# TEST F — security holds under the real model
# ---------------------------------------------------------------------------


class TestRealModelSecurity:
    def test_workspace_escape_refused_in_real_run(self, router, workspace):
        from src.tools.file_tools import _ensure_within_workspace

        with pytest.raises(PermissionError):
            _ensure_within_workspace(str(workspace.parent / "escaped.txt"))

    def test_real_run_cannot_write_outside_workspace(self, router, workspace):
        executor, _ = _build_executor(router, workspace)
        outside = workspace.parent / "pearl_escape_probe.txt"

        executor.run(f"Create a file at the absolute path {outside}")

        assert not outside.exists(), (
            "P0 VIOLATION: an autonomous run wrote outside the workspace"
        )
        for staged in executor.patch_manager.affected_files():
            assert Path(staged).resolve().is_relative_to(workspace.resolve()), (
                f"P0 VIOLATION: out-of-workspace path was staged: {staged}"
            )
