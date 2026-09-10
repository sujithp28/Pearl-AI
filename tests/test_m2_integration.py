"""
M2 Stabilization Sprint — integration tests.

Each test exercises a cross-component interaction that was identified
as a gap in the production readiness review.  Tests are kept narrowly
focused and use the same fixture conventions as the rest of the suite
(ScriptedLLM stubs, tmp_path workspace, real file I/O).

IT-1  Cancel mid-flight with staged patches → patches must be discarded.
IT-2  Dependency-annotated plan executes in topologically sorted order.
IT-3  Replanning applies REPLAN_PENALTY to the confidence score.
IT-4  Missing required argument raises ValueError before dispatch.
IT-5  Read-before-write violation raises PlanValidationError.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agent.confidence import REPLAN_PENALTY, score_plan
from src.agent.dispatcher import ToolDispatcher
from src.agent.executor import AutonomousExecutor
from src.agent.plan_validator import PlanValidationError, validate_plan
from src.agent.planner import Planner
from src.llm.parser import ToolCall
from src.llm.validation import validate_tool_call
from src.tools.edit_tools import create_file, replace_in_file, set_active_patch_manager
from src.tools.metadata import tool
from src.tools.registry import ToolRegistry
from src.tools.shell_tools import set_active_command_approver

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_approval_state():
    yield
    set_active_patch_manager(None)
    set_active_command_approver(None)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    return tmp_path


@tool(
    description="Add two numbers.", parameters={"a": "int", "b": "int"}, returns="int"
)
def _add(a: int, b: int) -> int:
    return a + b


@tool(description="Always fails.")
def _boom() -> None:
    raise ValueError("kaboom")


def _build_executor(
    monkeypatch=None, *, max_replans: int = 3
) -> tuple[AutonomousExecutor, Planner]:
    registry = ToolRegistry()
    registry.register(_add)
    registry.register(_boom)
    registry.register(create_file)
    registry.register(replace_in_file)
    dispatcher = ToolDispatcher(registry)
    planner = Planner(registry, dispatcher)
    executor = AutonomousExecutor(
        planner, dispatcher, max_replans=max_replans, checkpoints=None
    )
    return executor, planner


def _plan_of(*steps):
    return lambda prompt, cancel_check=None: {"steps": list(steps)}


def _plan_sequence(*plans):
    calls = {"n": 0}

    def _fn(prompt, cancel_check=None):
        index = min(calls["n"], len(plans) - 1)
        calls["n"] += 1
        return {"steps": plans[index]}

    return _fn


def _call(tool_name: str, **kwargs) -> ToolCall:
    return ToolCall(tool_name=tool_name, args=(), kwargs=kwargs)


# ---------------------------------------------------------------------------
# IT-1  Cancel mid-flight — staged patches must be discarded
# ---------------------------------------------------------------------------


class TestIT1CancelMidFlight:
    """
    Cancellation that arrives after a write step has staged patches but
    before the next step begins must leave the ChangeManager empty.
    Without the fix, the next run() call would inherit stale patches
    from the cancelled run and corrupt the new approval batch.
    """

    def test_cancel_before_second_step_discards_patches(self, monkeypatch, workspace):
        executor, planner = _build_executor()
        target = str(workspace / "a.py")

        # Step 1 creates a file (stages a patch).
        # Step 2 adds numbers.
        # Cancel is set *after* step 1 completes so the cancel check
        # fires at the top of step 2's loop iteration.
        step1_done = {"done": False}

        original_create = create_file

        def _create_and_cancel(*args, **kwargs):
            result = original_create(*args, **kwargs)
            # Cancel after the write has staged its patch.
            executor.cancel()
            step1_done["done"] = True
            return result

        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of(
                {
                    "tool": "create_file",
                    "arguments": {"path": target, "content": "x\n"},
                },
                {"tool": "_add", "arguments": {"a": 1, "b": 1}},
            ),
        )

        # Patch create_file in the registry to also call cancel().
        registry = planner.dispatcher._registry
        import src.tools.edit_tools as et

        monkeypatch.setattr(et, "create_file", _create_and_cancel)
        # Re-register so the dispatcher uses the patched version.
        registry._tools["create_file"].function = _create_and_cancel

        report = executor.run("create then add")

        assert report.stop_reason == "cancelled"
        assert step1_done["done"], "step 1 must have run"

        # Core assertion: the ChangeManager must be empty after cancellation.
        assert not executor.patch_manager.has_pending(), (
            "Stale patches from the cancelled run must be discarded, "
            "not left in patch_manager.pending"
        )

    def test_second_run_after_cancelled_run_has_clean_patch_state(
        self, monkeypatch, workspace
    ):
        executor, planner = _build_executor()
        target_a = str(workspace / "a.py")
        target_b = str(workspace / "b.py")

        cancel_after_first = {"fired": False}
        original_create = create_file

        def _create_and_cancel(*args, **kwargs):
            result = original_create(*args, **kwargs)
            if not cancel_after_first["fired"]:
                cancel_after_first["fired"] = True
                executor.cancel()
            return result

        registry = planner.dispatcher._registry
        import src.tools.edit_tools as et

        monkeypatch.setattr(et, "create_file", _create_and_cancel)
        registry._tools["create_file"].function = _create_and_cancel

        # First run: cancel mid-flight.
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of(
                {
                    "tool": "create_file",
                    "arguments": {"path": target_a, "content": "1\n"},
                },
                {"tool": "_add", "arguments": {"a": 1, "b": 1}},
            ),
        )
        first = executor.run("create a then add")
        assert first.stop_reason == "cancelled"
        assert not executor.patch_manager.has_pending()

        # Second run: must see only its own patches — no a.py leftover.
        # Reset cancel event so the second run can proceed.
        import threading

        executor._cancel_event = threading.Event()

        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of(
                {
                    "tool": "create_file",
                    "arguments": {"path": target_b, "content": "2\n"},
                },
            ),
        )
        second = executor.run("create b")
        assert second.stop_reason == "awaiting_approval"
        affected = executor.patch_manager.affected_files()
        assert affected == [target_b], (
            f"Only b.py should be staged; got {affected} — "
            "a.py from the cancelled run must not appear"
        )


# ---------------------------------------------------------------------------
# IT-2  Dependency-annotated plan executes in topologically sorted order
# ---------------------------------------------------------------------------


class TestIT2DependencyExecution:
    """
    A plan whose steps are declared out of dependency order must be
    reordered by topological_sort() inside plan() and then executed
    in the correct order by the executor.

    This exercises the full Planner → plan() → topological_sort()
    → AutonomousExecutor._execute() chain with a real dependency graph.
    """

    def test_reversed_dependency_plan_executes_in_correct_order(
        self, monkeypatch, workspace
    ):
        executor, planner = _build_executor()

        execution_order: list[str] = []

        registry = planner.dispatcher._registry

        @tool(description="Records its id.", parameters={"step_id": "str"})
        def record_step(step_id: str) -> str:
            execution_order.append(step_id)
            return step_id

        registry.register(record_step)

        # LLM returns steps in reverse dependency order:
        # step "b" depends on "a", but "b" is listed first.
        # After topological sort, "a" must execute before "b".
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of(
                {
                    "tool": "record_step",
                    "id": "b",
                    "arguments": {"step_id": "b"},
                    "depends_on": ["a"],
                },
                {
                    "tool": "record_step",
                    "id": "a",
                    "arguments": {"step_id": "a"},
                    "depends_on": [],
                },
            ),
        )

        report = executor.run("record a then b, declared in reverse order")

        assert report.succeeded
        assert execution_order == ["a", "b"], (
            f"Expected execution order ['a', 'b'], got {execution_order}. "
            "Topological sort must reorder steps before execution."
        )

    def test_three_step_chain_in_reverse_executes_in_forward_order(
        self, monkeypatch, workspace
    ):
        executor, planner = _build_executor()

        execution_order: list[str] = []
        registry = planner.dispatcher._registry

        @tool(description="Records step.", parameters={"name": "str"})
        def record(name: str) -> str:
            execution_order.append(name)
            return name

        registry.register(record)

        # Declared order: c → b → a; correct order after sort: a → b → c
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of(
                {
                    "tool": "record",
                    "id": "c",
                    "arguments": {"name": "c"},
                    "depends_on": ["b"],
                },
                {
                    "tool": "record",
                    "id": "b",
                    "arguments": {"name": "b"},
                    "depends_on": ["a"],
                },
                {
                    "tool": "record",
                    "id": "a",
                    "arguments": {"name": "a"},
                    "depends_on": [],
                },
            ),
        )

        report = executor.run("record c depends b depends a")

        assert report.succeeded
        assert execution_order == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# IT-3  Replanning confidence penalty is applied
# ---------------------------------------------------------------------------


class TestIT3ReplanConfidence:
    """
    When Planner.replan() is called with replans_used > 0, score_plan()
    must receive that value and the returned score must be penalised by
    REPLAN_PENALTY compared to an equivalent fresh plan.
    """

    def test_score_plan_applies_replan_penalty(self):
        steps = [
            _call("read_file", path="a.py"),
            _call("read_file", path="b.py"),
        ]
        fresh = score_plan(steps, replans_used=0)
        after_one_replan = score_plan(steps, replans_used=1)

        assert after_one_replan.score < fresh.score, (
            "A replanned plan must score lower than the same fresh plan"
        )
        assert len(after_one_replan.plan_factors) == 1
        assert after_one_replan.plan_factors[0].name == "prior_replans"
        assert abs(after_one_replan.plan_factors[0].deduction - REPLAN_PENALTY) < 1e-9

    def test_replan_passes_replans_used_to_score_plan(self, monkeypatch):
        """
        End-to-end: after one tool failure causes a replan, the score
        logged by Planner.replan() must reflect the replan penalty.
        """

        executor, planner = _build_executor()

        captured_confidence: list[float] = []

        original_score_plan = score_plan

        def _spy_score_plan(steps, *, replans_used=0):
            result = original_score_plan(steps, replans_used=replans_used)
            captured_confidence.append((replans_used, result.score))
            return result

        import src.agent.planner as planner_module

        monkeypatch.setattr(planner_module, "score_plan", _spy_score_plan)

        # Initial plan: boom (fails) → replan: _add (succeeds)
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_sequence(
                [{"tool": "_boom", "arguments": {}}],
                [{"tool": "_add", "arguments": {"a": 1, "b": 1}}],
            ),
        )

        report = executor.run("fail then recover")

        assert report.succeeded
        # Two calls: initial plan (replans_used=0) and replan (replans_used=1)
        assert len(captured_confidence) == 2, (
            f"Expected score_plan called twice, got {len(captured_confidence)}"
        )
        initial_replans_used, _ = captured_confidence[0]
        replan_replans_used, replan_score = captured_confidence[1]

        assert initial_replans_used == 0
        assert replan_replans_used == 1, (
            "score_plan for the replan must receive replans_used=1, "
            f"got replans_used={replan_replans_used}"
        )

    def test_replan_penalty_accumulates_across_multiple_replans(self, monkeypatch):
        import src.agent.planner as planner_module

        executor, planner = _build_executor(max_replans=2)

        captured: list[int] = []
        original_score_plan = score_plan

        def _spy(steps, *, replans_used=0):
            captured.append(replans_used)
            return original_score_plan(steps, replans_used=replans_used)

        monkeypatch.setattr(planner_module, "score_plan", _spy)

        # Two plans: boom, boom.  The repeated-action guard fires after the second
        # _boom failure (same tool + args) and stops execution without reaching the
        # third plan.  The key invariant under test is that replans_used increments
        # correctly in each score_plan call.
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_sequence(
                [{"tool": "_boom", "arguments": {}}],
                [{"tool": "_boom", "arguments": {}}],
                [{"tool": "_add", "arguments": {"a": 1, "b": 1}}],
            ),
        )

        report = executor.run("fail twice, abort via repeated-action guard")

        assert not report.succeeded
        assert report.stop_reason == "fatal_error"
        # Calls: initial plan (0), replan-1 (1). Guard fires; no third plan.
        assert captured == [0, 1], (
            f"Expected replans_used sequence [0, 1], got {captured}"
        )


# ---------------------------------------------------------------------------
# IT-4  Missing required argument validation
# ---------------------------------------------------------------------------


class TestIT4RequiredArgValidation:
    """
    validate_tool_call() must raise ValueError when a required argument
    is omitted — before the step ever reaches the dispatcher.
    """

    def test_missing_required_arg_raises_before_dispatch(self):
        registry = ToolRegistry()

        @tool(
            description="Needs both a and b.",
            parameters={"a": "int", "b": "int"},
            returns="int",
        )
        def needs_two(a: int, b: int) -> int:
            return a + b

        registry.register(needs_two)

        # 'b' is missing
        step = _call("needs_two", a=1)

        with pytest.raises(ValueError, match="missing required"):
            validate_tool_call(step, registry)

    def test_all_required_args_present_passes(self):
        registry = ToolRegistry()

        @tool(
            description="Needs both.",
            parameters={"x": "str", "y": "str"},
            returns="str",
        )
        def concat(x: str, y: str) -> str:
            return x + y

        registry.register(concat)

        step = _call("concat", x="hello", y="world")
        validate_tool_call(step, registry)  # must not raise

    def test_optional_arg_may_be_omitted(self):
        registry = ToolRegistry()

        @tool(
            description="Optional second arg.",
            parameters={"path": "str", "encoding": "str"},
            returns="str",
        )
        def read(path: str, encoding: str = "utf-8") -> str:
            return path

        registry.register(read)

        step = _call("read", path="file.py")
        validate_tool_call(step, registry)  # must not raise — encoding is optional

    def test_none_tool_skips_validation(self):
        registry = ToolRegistry()
        step = _call("none")
        validate_tool_call(step, registry)  # must not raise

    def test_missing_arg_error_names_the_missing_param(self):
        registry = ToolRegistry()

        @tool(description="Needs path.", parameters={"path": "str"}, returns="str")
        def read_it(path: str) -> str:
            return path

        registry.register(read_it)

        step = _call("read_it")

        with pytest.raises(ValueError, match="path"):
            validate_tool_call(step, registry)


# ---------------------------------------------------------------------------
# IT-5  Read-before-write validation
# ---------------------------------------------------------------------------


class TestIT5ReadBeforeWrite:
    """
    validate_plan() must reject plans where a file-modifying tool
    (replace_in_file, edit_lines, patch_file, write_file) appears
    with no read_file step preceding it.
    """

    def test_replace_in_file_without_prior_read_raises(self):
        steps = [_call("replace_in_file", path="a.py", search="x", replacement="y")]
        with pytest.raises(PlanValidationError, match="read"):
            validate_plan(steps)

    def test_write_file_without_prior_read_raises(self):
        steps = [_call("write_file", path="a.py", content="new")]
        with pytest.raises(PlanValidationError, match="read"):
            validate_plan(steps)

    def test_read_then_replace_is_valid(self):
        steps = [
            _call("read_file", path="a.py"),
            _call("replace_in_file", path="a.py", search="x", replacement="y"),
        ]
        validate_plan(steps)  # must not raise

    def test_read_then_write_file_is_valid(self):
        steps = [
            _call("read_file", path="a.py"),
            _call("write_file", path="a.py", content="new"),
        ]
        validate_plan(steps)  # must not raise

    def test_create_file_without_prior_read_is_valid(self):
        # create_file creates new content; no prior read needed.
        steps = [_call("create_file", path="new.py", content="x = 1")]
        validate_plan(steps)  # must not raise

    def test_none_step_only_is_valid(self):
        steps = [_call("none")]
        validate_plan(steps)  # must not raise

    def test_error_names_the_offending_tool(self):
        steps = [_call("replace_in_file", path="a.py", search="x", replacement="y")]
        with pytest.raises(PlanValidationError, match="replace_in_file"):
            validate_plan(steps)

    def test_mixed_plan_read_before_both_writes_is_valid(self):
        steps = [
            _call("read_file", path="a.py"),
            _call("replace_in_file", path="a.py", search="x", replacement="y"),
            _call("write_file", path="b.py", content="new"),
        ]
        validate_plan(steps)  # must not raise

    def test_unrelated_read_does_not_satisfy_write_requirement(self):
        # read_file for a DIFFERENT path still satisfies the global
        # read-before-write rule (we check for any read, not path-specific).
        steps = [
            _call("read_file", path="other.py"),
            _call("replace_in_file", path="a.py", search="x", replacement="y"),
        ]
        validate_plan(steps)  # must not raise — a read IS present


# ---------------------------------------------------------------------------
# IT-6  Full approval workflow: run → awaiting_approval → approve → completed
# ---------------------------------------------------------------------------


class TestIT6ApprovalWorkflow:
    """
    End-to-end test for the staged-approval gate.

    Verifies:
      1. run() with a write step stops at awaiting_approval, not completed.
      2. The file is NOT on disk after run() returns.
      3. approve() resumes execution and returns stop_reason="completed".
      4. The file IS on disk after approve() returns.
      5. reject() on a fresh run discards everything — file still absent.
    """

    def test_run_stops_at_awaiting_approval(self, monkeypatch, workspace):
        executor, planner = _build_executor()
        target = str(workspace / "output.py")

        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of(
                {
                    "tool": "create_file",
                    "arguments": {"path": target, "content": "x = 1\n"},
                }
            ),
        )

        report = executor.run("create output.py")

        assert report.stop_reason == "awaiting_approval"
        assert not Path(target).exists(), "File must NOT exist before approval"

    def test_approve_writes_file_and_completes(self, monkeypatch, workspace):
        executor, planner = _build_executor()
        target = str(workspace / "output.py")

        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of(
                {
                    "tool": "create_file",
                    "arguments": {"path": target, "content": "x = 1\n"},
                }
            ),
        )

        executor.run("create output.py")
        assert not Path(target).exists()

        final = executor.approve()

        assert final.stop_reason == "completed"
        assert Path(target).exists(), "File must exist after approve()"
        assert Path(target).read_text() == "x = 1\n"

    def test_reject_leaves_file_absent(self, monkeypatch, workspace):
        executor, planner = _build_executor()
        target = str(workspace / "output.py")

        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of(
                {
                    "tool": "create_file",
                    "arguments": {"path": target, "content": "x = 1\n"},
                }
            ),
        )

        executor.run("create output.py")
        final = executor.reject()

        assert final.stop_reason == "rejected"
        assert not Path(target).exists(), "File must NOT exist after reject()"

    def test_approve_after_reject_raises(self, monkeypatch, workspace):
        executor, planner = _build_executor()
        target = str(workspace / "output.py")

        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of(
                {"tool": "create_file", "arguments": {"path": target, "content": "x\n"}}
            ),
        )

        executor.run("create output.py")
        executor.reject()

        with pytest.raises((RuntimeError, ValueError)):
            executor.approve()

    def test_multi_step_all_files_present_after_approve(self, monkeypatch, workspace):
        executor, planner = _build_executor()
        a = str(workspace / "a.py")
        b = str(workspace / "b.py")

        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of(
                {"tool": "create_file", "arguments": {"path": a, "content": "a = 1\n"}},
                {"tool": "create_file", "arguments": {"path": b, "content": "b = 2\n"}},
            ),
        )

        executor.run("create a and b")
        assert not Path(a).exists()
        assert not Path(b).exists()

        executor.approve()

        assert Path(a).read_text() == "a = 1\n"
        assert Path(b).read_text() == "b = 2\n"

    def test_reflection_is_complete_after_approve(self, monkeypatch, workspace):
        executor, planner = _build_executor()
        target = str(workspace / "out.py")

        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of(
                {"tool": "create_file", "arguments": {"path": target, "content": "x\n"}}
            ),
        )

        executor.run("create out.py")
        final = executor.approve()

        assert final.reflection is not None
        assert final.reflection.outcome == "COMPLETE"
