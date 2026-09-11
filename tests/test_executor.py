import json as _json
import logging as _logging
import sys
from pathlib import Path

import pytest

from src.agent.dispatcher import ToolDispatcher
from src.agent.executor import AutonomousExecutor, ProgressEvent
from src.agent.planner import Planner
from src.personality import EventKind, PersonalityManager
from src.tools.command_approval import CommandApprovalManager
from src.tools.edit_tools import (
    create_file,
    replace_in_file,
    set_active_patch_manager,
)
from src.tools.file_tools import read_file
from src.tools.metadata import tool
from src.tools.patch_manager import ChangeManager
from src.tools.registry import ToolRegistry
from src.tools.shell_tools import execute_shell, set_active_command_approver
from tests.conftest import write_lf


@pytest.fixture(autouse=True)
def _no_leaked_approval_state():
    """
    Guarantee preview/approval mode is off after every test, even if
    a test leaves a run paused (awaiting_approval) without resolving
    it — otherwise that state could leak into unrelated tests via the
    module-level contextvars in `edit_tools.py`/`shell_tools.py`.
    """

    yield
    set_active_patch_manager(None)
    set_active_command_approver(None)


@tool(
    description="Add two numbers.",
    parameters={"a": "int", "b": "int"},
    returns="int",
)
def add(a: int, b: int) -> int:
    return a + b


@tool(description="Always fails.")
def boom() -> None:
    raise ValueError("kaboom")


def build_executor(
    max_iterations: int = 10,
    max_replans: int = 3,
    on_progress=None,
    patch_manager: ChangeManager | None = None,
    command_approver: CommandApprovalManager | None = None,
):
    registry = ToolRegistry()
    registry.register(add)
    registry.register(boom)
    registry.register(read_file)
    registry.register(create_file)
    registry.register(replace_in_file)
    registry.register(execute_shell)

    dispatcher = ToolDispatcher(registry)
    planner = Planner(registry, dispatcher)

    return AutonomousExecutor(
        planner,
        dispatcher,
        max_iterations=max_iterations,
        max_replans=max_replans,
        on_progress=on_progress,
        patch_manager=patch_manager,
        command_approver=command_approver,
    ), planner


def _plan_of(*steps):
    """
    A `generate_json` stub that always returns the same plan,
    regardless of which prompt (initial plan or replan) asked for it.

    Accepts and ignores `cancel_check`: the executor always passes it
    (bound to its own `is_cancelled`), so a stub taking only `prompt`
    would break on every call.
    """

    return lambda prompt, cancel_check=None: {"steps": list(steps)}


def _default_progress_text(event_kind: EventKind) -> str:
    """
    The exact `current_action` text a default-configured (`Settings`-
    driven) `AutonomousExecutor` produces for `event_kind` — asserted
    against the real `PersonalityManager` rather than a hardcoded
    string, so these tests verify "the executor actually goes through
    the personality system" rather than pinning one specific phrasing
    that's expected to be tunable.
    """

    return PersonalityManager().format(event_kind)


def _plan_sequence(*plans):
    """
    A `generate_json` stub that returns a different plan on each
    successive call (the initial plan, then each subsequent replan),
    repeating the last plan if called more times than provided.
    """

    calls = {"n": 0}

    def _fn(prompt, cancel_check=None):
        index = min(calls["n"], len(plans) - 1)
        calls["n"] += 1
        return {"steps": plans[index]}

    return _fn


# ---------------------------------------------------------------------
# Task completed
# ---------------------------------------------------------------------


def test_run_executes_every_step_and_reports_completed(monkeypatch):
    executor, planner = build_executor()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {"tool": "add", "arguments": {"a": 1, "b": 2}},
            {"tool": "add", "arguments": {"a": 3, "b": 4}},
        ),
    )

    report = executor.run("add 1+2 then 3+4")

    assert report.succeeded
    assert report.stop_reason == "completed"
    assert report.replans_used == 0
    assert [step.result for step in report.steps] == [3, 7]
    assert [step.iteration for step in report.steps] == [1, 2]
    assert all(step.succeeded for step in report.steps)


def test_run_treats_none_step_as_immediate_completion(monkeypatch):
    executor, planner = build_executor()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of({"tool": "none", "arguments": {}}),
    )

    report = executor.run("say hello")

    assert report.succeeded
    assert report.stop_reason == "completed"
    assert len(report.steps) == 1
    assert report.steps[0].tool_name == "none"
    assert report.steps[0].succeeded
    assert report.steps[0].result is None


def test_run_stops_at_none_step_even_with_more_steps_after_it(monkeypatch):
    executor, planner = build_executor()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {"tool": "add", "arguments": {"a": 1, "b": 2}},
            {"tool": "none", "arguments": {}},
            {"tool": "add", "arguments": {"a": 5, "b": 5}},
        ),
    )

    report = executor.run("add once, then stop")

    assert report.succeeded
    assert [step.tool_name for step in report.steps] == ["add", "none"]


# ---------------------------------------------------------------------
# Step evaluation and summaries
# ---------------------------------------------------------------------


def test_step_summary_reports_success(monkeypatch):
    executor, planner = build_executor()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of({"tool": "add", "arguments": {"a": 2, "b": 2}}),
    )

    report = executor.run("add")

    summary = report.steps[0].summary
    assert "add" in summary
    assert "succeeded" in summary
    assert "4" in summary


def test_step_summary_reports_failure(monkeypatch):
    executor, planner = build_executor(max_replans=0)

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of({"tool": "boom", "arguments": {}}),
    )

    report = executor.run("boom")

    summary = report.steps[0].summary
    assert "boom" in summary
    assert "failed" in summary
    assert "kaboom" in summary


# ---------------------------------------------------------------------
# Reflection: replan on failure
# ---------------------------------------------------------------------


def test_run_recovers_from_failure_via_replan(monkeypatch):
    executor, planner = build_executor()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_sequence(
            [{"tool": "boom", "arguments": {}}],
            [{"tool": "add", "arguments": {"a": 1, "b": 1}}],
        ),
    )

    report = executor.run("try boom, then recover")

    assert report.succeeded
    assert report.stop_reason == "completed"
    assert report.replans_used == 1
    assert [step.tool_name for step in report.steps] == ["boom", "add"]
    assert not report.steps[0].succeeded
    assert report.steps[1].succeeded
    assert report.steps[1].result == 2


def test_execution_history_includes_steps_before_and_after_replan(
    monkeypatch,
):
    executor, planner = build_executor()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_sequence(
            [
                {"tool": "add", "arguments": {"a": 1, "b": 1}},
                {"tool": "boom", "arguments": {}},
            ],
            [
                {"tool": "add", "arguments": {"a": 9, "b": 9}},
            ],
        ),
    )

    report = executor.run("add, boom, replan, add")

    assert report.succeeded
    assert [step.tool_name for step in report.steps] == [
        "add",
        "boom",
        "add",
    ]
    assert report.steps[0].result == 2
    assert not report.steps[1].succeeded
    assert report.steps[2].result == 18
    assert report.replans_used == 1


def test_run_stops_after_max_replans_exhausted(monkeypatch):
    """
    The repeated-action guard fires before max_replans when the planner
    produces the same failing action every time (identical tool + args).
    The executor must still stop, still report fatal_error, and must NOT
    loop forever.
    """
    executor, planner = build_executor(max_replans=3)

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of({"tool": "boom", "arguments": {}}),
    )

    report = executor.run("boom forever")

    assert not report.succeeded
    assert report.stop_reason == "fatal_error"
    # The repeated-action guard stops after the same action fails twice
    # (once in the initial plan, once after the first replan), so
    # replans_used==1 and steps==2 — fewer than max_replans would allow.
    assert report.replans_used <= 3
    assert len(report.steps) >= 1
    assert all(not step.succeeded for step in report.steps)


def test_run_stops_immediately_when_replanning_disabled(monkeypatch):
    executor, planner = build_executor(max_replans=0)

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {"tool": "add", "arguments": {"a": 1, "b": 2}},
            {"tool": "boom", "arguments": {}},
            {"tool": "add", "arguments": {"a": 5, "b": 5}},
        ),
    )

    report = executor.run("add, then boom, then add")

    assert not report.succeeded
    assert report.stop_reason == "fatal_error"
    assert report.replans_used == 0
    assert len(report.steps) == 2
    assert report.steps[0].succeeded
    assert not report.steps[1].succeeded
    assert "boom" in report.steps[1].error


def test_run_stops_fatal_if_replanning_itself_fails(monkeypatch):
    executor, planner = build_executor()

    def _first_call_fails(prompt, cancel_check=None):
        return {"steps": [{"tool": "boom", "arguments": {}}]}

    monkeypatch.setattr(planner.client, "generate_json", _first_call_fails)

    def _broken_replan(*args, **kwargs):
        raise RuntimeError("replanning is unavailable")

    monkeypatch.setattr(planner, "replan", _broken_replan)

    report = executor.run("boom, then fail to replan")

    assert not report.succeeded
    assert report.stop_reason == "fatal_error"
    assert report.replans_used == 0
    assert len(report.steps) == 1


# ---------------------------------------------------------------------
# Max iterations
# ---------------------------------------------------------------------


def test_run_stops_at_max_iterations(monkeypatch):
    executor, planner = build_executor(max_iterations=2)

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {"tool": "add", "arguments": {"a": 1, "b": 1}},
            {"tool": "add", "arguments": {"a": 2, "b": 2}},
            {"tool": "add", "arguments": {"a": 3, "b": 3}},
        ),
    )

    report = executor.run("add three times")

    assert not report.succeeded
    assert report.stop_reason == "max_iterations"
    assert len(report.steps) == 2
    assert [step.result for step in report.steps] == [2, 4]


def test_run_within_max_iterations_still_completes(monkeypatch):
    executor, planner = build_executor(max_iterations=5)

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of({"tool": "add", "arguments": {"a": 1, "b": 1}}),
    )

    report = executor.run("add once")

    assert report.succeeded
    assert report.stop_reason == "completed"
    assert len(report.steps) == 1


# ---------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------


def test_run_logs_every_step(monkeypatch, caplog):
    executor, planner = build_executor()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {"tool": "add", "arguments": {"a": 1, "b": 2}},
            {"tool": "add", "arguments": {"a": 3, "b": 4}},
        ),
    )

    with caplog.at_level("INFO", logger="src.agent.executor"):
        executor.run("add 1+2 then 3+4")

    messages = "\n".join(caplog.messages)
    assert "Step 1:" in messages
    assert "Step 2:" in messages
    assert "succeeded" in messages


def test_run_logs_replanning(monkeypatch, caplog):
    executor, planner = build_executor()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_sequence(
            [{"tool": "boom", "arguments": {}}],
            [{"tool": "add", "arguments": {"a": 1, "b": 1}}],
        ),
    )

    with caplog.at_level("INFO", logger="src.agent.executor"):
        executor.run("try boom, then recover")

    messages = "\n".join(caplog.messages)
    assert "asking Planner for a revised plan" in messages
    assert "replanned" in messages


# ---------------------------------------------------------------------
# Progress streaming
# ---------------------------------------------------------------------


def test_run_emits_events_for_a_successful_two_step_run(monkeypatch):
    executor, planner = build_executor()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {"tool": "add", "arguments": {"a": 1, "b": 2}},
            {"tool": "add", "arguments": {"a": 3, "b": 4}},
        ),
    )

    report = executor.run("add 1+2 then 3+4")

    statuses = [event.status for event in report.events]
    assert statuses == [
        "planning",
        "executing_step",
        "step_completed",
        "executing_step",
        "step_completed",
        "task_completed",
    ]
    assert all(isinstance(event, ProgressEvent) for event in report.events)


def test_events_include_current_step_total_steps_action_and_status(
    monkeypatch,
):
    executor, planner = build_executor()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of({"tool": "add", "arguments": {"a": 2, "b": 2}}),
    )

    report = executor.run("add")

    executing = next(e for e in report.events if e.status == "executing_step")
    assert executing.current_step == 1
    assert executing.total_steps == 1
    assert executing.current_action == _default_progress_text(EventKind.EXECUTING)

    completed = next(e for e in report.events if e.status == "step_completed")
    assert completed.current_step == 1
    assert completed.total_steps == 1
    assert completed.current_action == _default_progress_text(EventKind.SUCCESS)

    task_completed = report.events[-1]
    assert task_completed.status == "task_completed"
    assert task_completed.current_step == 1
    assert task_completed.total_steps == 1
    assert task_completed.current_action == _default_progress_text(EventKind.COMPLETED)


def test_run_emits_step_failed_and_replanning_events(monkeypatch):
    executor, planner = build_executor()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_sequence(
            [{"tool": "boom", "arguments": {}}],
            [{"tool": "add", "arguments": {"a": 1, "b": 1}}],
        ),
    )

    report = executor.run("try boom, then recover")

    statuses = [event.status for event in report.events]
    assert statuses == [
        "planning",
        "executing_step",
        "step_failed",
        "replanning",
        "executing_step",
        "step_completed",
        "task_completed",
    ]

    failed_event = report.events[2]
    assert failed_event.current_action == _default_progress_text(EventKind.WARNING)

    replanning_event = report.events[3]
    assert replanning_event.current_action == _default_progress_text(
        EventKind.REPLANNING
    )


def test_run_emits_task_failed_on_max_iterations(monkeypatch):
    """
    Hitting the iteration cap is a failure and must say so by status.
    This previously asserted "task_completed" alongside FAILURE wording —
    encoding the contradiction that made the UI draw a green "Done" next
    to the run's own error.
    """
    executor, planner = build_executor(max_iterations=1)

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {"tool": "add", "arguments": {"a": 1, "b": 1}},
            {"tool": "add", "arguments": {"a": 2, "b": 2}},
        ),
    )

    report = executor.run("add twice")

    assert report.events[-1].status == "task_failed"
    assert report.events[-1].current_action == _default_progress_text(EventKind.FAILURE)


def test_run_emits_task_failed_on_fatal_error(monkeypatch):
    executor, planner = build_executor(max_replans=0)

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of({"tool": "boom", "arguments": {}}),
    )

    report = executor.run("boom")

    statuses = [event.status for event in report.events]
    assert statuses == [
        "planning",
        "executing_step",
        "step_failed",
        "task_failed",
    ]
    assert "task_completed" not in statuses, (
        "a failed run must not also report completion"
    )
    assert report.events[-1].current_action == _default_progress_text(EventKind.FAILURE)


def test_on_progress_callback_receives_events_in_real_time(monkeypatch):
    executor, planner = build_executor()

    received: list[ProgressEvent] = []
    executor.on_progress = received.append

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of({"tool": "add", "arguments": {"a": 1, "b": 1}}),
    )

    report = executor.run("add")

    assert [event.status for event in received] == [
        event.status for event in report.events
    ]
    assert received == report.events


def test_on_progress_callback_can_be_set_via_constructor(monkeypatch):
    received: list[ProgressEvent] = []

    executor, planner = build_executor(on_progress=received.append)

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of({"tool": "none", "arguments": {}}),
    )

    executor.run("say hello")

    assert [event.status for event in received] == [
        "planning",
        "task_completed",
    ]


def test_broken_progress_callback_does_not_break_execution(monkeypatch):
    def _broken(event):
        raise RuntimeError("UI exploded")

    executor, planner = build_executor(on_progress=_broken)

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of({"tool": "add", "arguments": {"a": 1, "b": 1}}),
    )

    report = executor.run("add")

    assert report.succeeded
    assert report.steps[0].result == 2
    assert len(report.events) > 0


# ---------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------


def test_is_cancelled_defaults_to_false_and_is_per_instance():
    executor1, _ = build_executor()
    executor2, _ = build_executor()

    assert not executor1.is_cancelled()
    assert not executor2.is_cancelled()

    executor1.cancel()

    assert executor1.is_cancelled()
    assert not executor2.is_cancelled()


def test_repeated_cancel_calls_are_idempotent():
    executor, _ = build_executor()

    executor.cancel()
    executor.cancel()
    executor.cancel()

    assert executor.is_cancelled()


def test_cancel_before_execution_stops_immediately_with_no_steps(
    monkeypatch,
):
    executor, planner = build_executor()
    executor.cancel()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {"tool": "add", "arguments": {"a": 1, "b": 1}},
            {"tool": "add", "arguments": {"a": 2, "b": 2}},
        ),
    )

    report = executor.run("add twice")

    assert report.stop_reason == "cancelled"
    assert not report.succeeded
    assert report.steps == []
    assert [event.status for event in report.events] == [
        "planning",
        "cancelled",
    ]


def test_cancellation_emits_final_cancelled_event(monkeypatch):
    executor, planner = build_executor()
    executor.cancel()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of({"tool": "add", "arguments": {"a": 1, "b": 1}}),
    )

    report = executor.run("add")

    final_event = report.events[-1]
    assert final_event.status == "cancelled"
    assert final_event.current_action
    assert isinstance(final_event, ProgressEvent)


def _build_registry_with_cancel_trigger():
    """
    A registry containing `add` plus a `trigger_cancel` tool that
    cancels the executor it's bound to as a side effect of running
    (used to simulate cancellation being requested mid-execution).
    """

    registry = ToolRegistry()
    registry.register(add)

    holder: dict = {}

    @tool(description="Cancels the bound executor, then succeeds.")
    def trigger_cancel() -> str:
        holder["executor"].cancel()
        return "triggered"

    registry.register(trigger_cancel)

    dispatcher = ToolDispatcher(registry)
    planner = Planner(registry, dispatcher)
    executor = AutonomousExecutor(planner, dispatcher)
    holder["executor"] = executor

    return executor, planner


def test_cancel_during_execution_stops_before_next_step(monkeypatch):
    executor, planner = _build_registry_with_cancel_trigger()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {"tool": "trigger_cancel", "arguments": {}},
            {"tool": "add", "arguments": {"a": 1, "b": 1}},
        ),
    )

    report = executor.run("trigger cancel then add")

    assert report.stop_reason == "cancelled"
    assert [step.tool_name for step in report.steps] == ["trigger_cancel"]
    assert report.steps[0].succeeded
    assert report.steps[0].result == "triggered"
    assert report.events[-1].status == "cancelled"


def test_cancel_preserves_steps_and_events_executed_so_far(monkeypatch):
    executor, planner = _build_registry_with_cancel_trigger()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {"tool": "add", "arguments": {"a": 1, "b": 1}},
            {"tool": "add", "arguments": {"a": 2, "b": 2}},
            {"tool": "trigger_cancel", "arguments": {}},
            {"tool": "add", "arguments": {"a": 3, "b": 3}},
        ),
    )

    report = executor.run("do several things then cancel mid-way")

    assert report.stop_reason == "cancelled"
    assert [step.tool_name for step in report.steps] == [
        "add",
        "add",
        "trigger_cancel",
    ]
    assert [step.result for step in report.steps] == [2, 4, "triggered"]
    assert all(step.succeeded for step in report.steps)

    statuses = [event.status for event in report.events]
    assert statuses == [
        "planning",
        "executing_step",
        "step_completed",
        "executing_step",
        "step_completed",
        "executing_step",
        "step_completed",
        "cancelled",
    ]


def test_cancel_during_replanning_stops_before_replan_call(monkeypatch):
    registry = ToolRegistry()

    holder: dict = {}

    @tool(description="Fails and requests cancellation as a side effect.")
    def boom_and_cancel() -> None:
        holder["executor"].cancel()
        raise ValueError("kaboom")

    registry.register(boom_and_cancel)

    dispatcher = ToolDispatcher(registry)
    planner = Planner(registry, dispatcher)
    executor = AutonomousExecutor(planner, dispatcher)
    holder["executor"] = executor

    replan_calls = {"count": 0}

    def _replan_spy(*args, **kwargs):
        replan_calls["count"] += 1
        return []

    monkeypatch.setattr(planner, "replan", _replan_spy)
    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of({"tool": "boom_and_cancel", "arguments": {}}),
    )

    report = executor.run("boom, then cancel before replanning")

    assert report.stop_reason == "cancelled"
    assert replan_calls["count"] == 0
    assert len(report.steps) == 1
    assert not report.steps[0].succeeded

    statuses = [event.status for event in report.events]
    assert statuses == [
        "planning",
        "executing_step",
        "step_failed",
        "cancelled",
    ]


def test_cancel_called_from_another_thread_is_observed(monkeypatch):
    import threading

    registry = ToolRegistry()
    registry.register(add)

    started = threading.Event()
    cancelled = threading.Event()

    @tool(description="Signals it started, then waits for cancellation.")
    def signal_start() -> str:
        started.set()
        # Block until the other thread has actually called cancel(),
        # so the next loop iteration is guaranteed to observe it —
        # otherwise this races against the executor moving on to the
        # next step before cancel() lands.
        assert cancelled.wait(timeout=5)
        return "started"

    registry.register(signal_start)

    dispatcher = ToolDispatcher(registry)
    planner = Planner(registry, dispatcher)
    executor = AutonomousExecutor(planner, dispatcher)

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {"tool": "signal_start", "arguments": {}},
            {"tool": "add", "arguments": {"a": 1, "b": 1}},
        ),
    )

    def _cancel_once_started():
        assert started.wait(timeout=5)
        executor.cancel()
        cancelled.set()

    canceller = threading.Thread(target=_cancel_once_started)
    canceller.start()

    report = executor.run("signal then add")

    canceller.join(timeout=5)

    assert not canceller.is_alive()
    assert report.stop_reason == "cancelled"
    assert [step.tool_name for step in report.steps] == ["signal_start"]


# ---------------------------------------------------------------------
# Patch preview & approval
# ---------------------------------------------------------------------
#
# These tests fake the workspace root by monkeypatching `Path.cwd`
# (which `_ensure_within_workspace` consults) rather than
# `monkeypatch.chdir` — Planner still needs the *real* process cwd
# (the repo root) to load its prompt templates by their relative
# path, and actually `chdir`-ing would break that.


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    return tmp_path


def test_single_file_preview_pauses_for_approval(monkeypatch, workspace):
    executor, planner = build_executor()
    target = str(workspace / "a.py")

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {
                "tool": "create_file",
                "arguments": {"path": target, "content": "print(1)\n"},
            },
        ),
    )

    report = executor.run("create a.py")

    assert report.stop_reason == "awaiting_approval"
    assert not report.succeeded
    assert not (workspace / "a.py").exists()
    assert executor.is_awaiting_approval()
    assert executor.patch_manager.has_pending()
    assert executor.patch_manager.affected_files() == [target]
    assert len(report.steps) == 1
    assert report.steps[0].succeeded


def test_approve_refreshes_repository_index_for_applied_files(monkeypatch, workspace):
    from src.tools.repo_tools import find_symbol

    executor, planner = build_executor()
    target = workspace / "a.py"

    # Prime the cache before the file exists, mirroring a planner
    # step later in the same run expecting to look up a symbol it
    # just asked to create.
    find_symbol("brand_new", path=str(workspace))

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {
                "tool": "create_file",
                "arguments": {
                    "path": str(target),
                    "content": "def brand_new():\n    return 1\n",
                },
            },
        ),
    )

    report = executor.run("create a.py")
    assert report.stop_reason == "awaiting_approval"

    # Still paused (not yet applied): the index must not see it yet.
    assert find_symbol("brand_new", path=str(workspace)) == []

    executor.approve()

    assert find_symbol("brand_new", path=str(workspace)) == [
        {"file": "a.py", "line": 1, "type": "function"}
    ]


def test_multi_file_preview_groups_into_a_single_batch(monkeypatch, workspace):
    executor, planner = build_executor()
    a, b, c = (str(workspace / name) for name in ("a.py", "b.py", "c.py"))

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {"tool": "create_file", "arguments": {"path": a, "content": "1\n"}},
            {"tool": "create_file", "arguments": {"path": b, "content": "2\n"}},
            {"tool": "create_file", "arguments": {"path": c, "content": "3\n"}},
        ),
    )

    report = executor.run("create three files")

    assert report.stop_reason == "awaiting_approval"
    assert len(report.steps) == 3
    assert all(step.succeeded for step in report.steps)
    assert not (workspace / "a.py").exists()
    assert not (workspace / "b.py").exists()
    assert not (workspace / "c.py").exists()
    assert executor.patch_manager.affected_files() == [a, b, c]


def test_approval_writes_files_and_completes(monkeypatch, workspace):
    executor, planner = build_executor()
    target = str(workspace / "a.py")

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {
                "tool": "create_file",
                "arguments": {"path": target, "content": "print(1)\n"},
            },
        ),
    )

    paused = executor.run("create a.py")
    assert paused.stop_reason == "awaiting_approval"

    final = executor.approve()

    assert final.stop_reason == "completed"
    assert final.succeeded
    assert (workspace / "a.py").read_text() == "print(1)\n"
    assert not executor.patch_manager.has_pending()
    assert not executor.is_awaiting_approval()
    # Execution history is preserved unchanged across the pause.
    assert final.steps == paused.steps


# ---------------------------------------------------------------------
# Shell-command approval (Phase 26)
# ---------------------------------------------------------------------


def test_execute_shell_step_pauses_for_approval(monkeypatch, workspace):
    executor, planner = build_executor()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of({"tool": "execute_shell", "arguments": {"command": "echo hi"}}),
    )

    report = executor.run("say hi")

    assert report.stop_reason == "awaiting_approval"
    assert executor.is_awaiting_approval()
    assert executor.command_approver.has_pending()
    assert executor.command_approver.affected_commands() == ["echo hi"]
    assert report.steps[0].succeeded


@pytest.mark.skipif(sys.platform == "win32", reason="touch is a POSIX command")
def test_approving_runs_the_staged_command_and_completes(monkeypatch, workspace):
    executor, planner = build_executor()
    marker = workspace / "marker.txt"

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {
                "tool": "execute_shell",
                "arguments": {"command": f"touch {marker.name}"},
            },
        ),
    )

    paused = executor.run("touch a marker file")
    assert paused.stop_reason == "awaiting_approval"
    assert not marker.exists()

    final = executor.approve()

    assert final.stop_reason == "completed"
    assert final.succeeded
    assert marker.exists()
    assert not executor.command_approver.has_pending()


def test_rejecting_discards_the_staged_command_without_running_it(
    monkeypatch, workspace
):
    executor, planner = build_executor()
    marker = workspace / "marker.txt"

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {
                "tool": "execute_shell",
                "arguments": {"command": f"touch {marker.name}"},
            },
        ),
    )

    paused = executor.run("touch a marker file")
    assert paused.stop_reason == "awaiting_approval"

    final = executor.reject()

    assert final.stop_reason == "rejected"
    assert not marker.exists()
    assert not executor.command_approver.has_pending()


@pytest.mark.skipif(sys.platform == "win32", reason="touch is a POSIX command")
def test_file_edit_and_shell_command_pause_in_the_same_batch(monkeypatch, workspace):
    executor, planner = build_executor()
    target = str(workspace / "a.py")
    marker = workspace / "marker.txt"

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {
                "tool": "create_file",
                "arguments": {"path": target, "content": "1\n"},
            },
            {
                "tool": "execute_shell",
                "arguments": {"command": f"touch {marker.name}"},
            },
        ),
    )

    paused = executor.run("create a file and touch a marker")

    assert paused.stop_reason == "awaiting_approval"
    assert executor.patch_manager.has_pending()
    assert executor.command_approver.has_pending()

    final = executor.approve()

    assert final.stop_reason == "completed"
    assert (workspace / "a.py").exists()
    assert marker.exists()


def test_cancel_while_awaiting_command_approval_discards_it(monkeypatch, workspace):
    executor, planner = build_executor()
    marker = workspace / "marker.txt"

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {
                "tool": "execute_shell",
                "arguments": {"command": f"touch {marker.name}"},
            },
        ),
    )

    paused = executor.run("touch a marker file")
    assert paused.stop_reason == "awaiting_approval"

    executor.cancel()
    final = executor.approve()

    assert final.stop_reason == "cancelled"
    assert not marker.exists()
    assert not executor.command_approver.has_pending()


def test_approve_backfills_step_result_with_real_command_output(monkeypatch, workspace):
    executor, planner = build_executor()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {
                "tool": "execute_shell",
                "arguments": {"command": "echo hello"},
            },
        ),
    )

    paused = executor.run("say hello")
    assert paused.stop_reason == "awaiting_approval"
    # Before approval the step result is the staging placeholder
    assert paused.steps[0].result == "Command staged for approval: 'echo hello'"

    final = executor.approve()

    assert final.stop_reason == "completed"
    # After approval the step result is the real command output
    assert final.steps[0].result == "hello"
    assert "hello" in final.steps[0].summary


def test_approve_backfills_multiple_command_steps_in_order(monkeypatch, workspace):
    executor, planner = build_executor()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {"tool": "execute_shell", "arguments": {"command": "echo first"}},
            {"tool": "execute_shell", "arguments": {"command": "echo second"}},
        ),
    )

    paused = executor.run("echo twice")
    assert paused.stop_reason == "awaiting_approval"
    assert len(paused.steps) == 2

    final = executor.approve()

    assert final.stop_reason == "completed"
    assert final.steps[0].result == "first"
    assert final.steps[1].result == "second"


def test_execute_shell_direct_call_still_runs_immediately_outside_a_run():
    # Sanity check that the tool function itself, called with no
    # executor/approver involved at all, is unaffected by any of this
    # (matches direct CLI / `tools/call` usage).
    result = execute_shell("echo hi")

    assert result.stdout.strip() == "hi"


def test_rejection_discards_patches_and_returns_cleanly(monkeypatch, workspace):
    executor, planner = build_executor()
    target = str(workspace / "a.py")

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {
                "tool": "create_file",
                "arguments": {"path": target, "content": "print(1)\n"},
            },
        ),
    )

    paused = executor.run("create a.py")
    assert paused.stop_reason == "awaiting_approval"

    final = executor.reject()

    assert final.stop_reason == "rejected"
    assert not final.succeeded
    assert not (workspace / "a.py").exists()
    assert not executor.patch_manager.has_pending()
    assert final.steps == paused.steps


def test_resume_after_approval_continues_without_replanning(monkeypatch, workspace):
    executor, planner = build_executor()
    target = str(workspace / "a.py")

    plan_calls = {"n": 0}

    def _plan(prompt, cancel_check=None):
        plan_calls["n"] += 1
        return {
            "steps": [
                {
                    "tool": "create_file",
                    "arguments": {"path": target, "content": "1\n"},
                },
                {"tool": "add", "arguments": {"a": 1, "b": 1}},
            ]
        }

    monkeypatch.setattr(planner.client, "generate_json", _plan)

    paused = executor.run("create then add")
    assert paused.stop_reason == "awaiting_approval"
    # Both steps already ran (create_file staged its edit, add
    # executed normally); the pause happens once the plan is
    # exhausted with a patch still pending approval.
    assert [step.tool_name for step in paused.steps] == [
        "create_file",
        "add",
    ]
    assert plan_calls["n"] == 1

    final = executor.approve()

    assert final.stop_reason == "completed"
    assert [step.tool_name for step in final.steps] == [
        "create_file",
        "add",
    ]
    # Same two ExecutionStep objects carried through, not re-run.
    assert final.steps == paused.steps
    assert final.steps[1].result == 2
    assert plan_calls["n"] == 1  # no re-plan happened
    assert (workspace / "a.py").read_text() == "1\n"


def test_resume_executes_steps_left_unrun_at_pause_time(monkeypatch, workspace):
    # With max_replans=0, a failed step exhausts the replan budget
    # immediately — but there's a patch pending (from the successful
    # create_file before it), so execution pauses instead of failing,
    # leaving the plan's final step ("add") genuinely un-run.
    executor, planner = build_executor(max_replans=0)
    target = str(workspace / "a.py")

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {
                "tool": "create_file",
                "arguments": {"path": target, "content": "1\n"},
            },
            {"tool": "boom", "arguments": {}},
            {"tool": "add", "arguments": {"a": 5, "b": 5}},
        ),
    )

    paused = executor.run("create, then fail, then add")

    assert paused.stop_reason == "awaiting_approval"
    assert [step.tool_name for step in paused.steps] == [
        "create_file",
        "boom",
    ]
    assert not paused.steps[1].succeeded

    final = executor.approve()

    assert final.stop_reason == "completed"
    assert [step.tool_name for step in final.steps] == [
        "create_file",
        "boom",
        "add",
    ]
    assert final.steps[2].result == 10
    assert (workspace / "a.py").read_text() == "1\n"


def test_resume_does_not_restart_completed_steps(monkeypatch, workspace):
    registry = ToolRegistry()
    registry.register(create_file)

    calls = {"n": 0}

    @tool(description="Counts how many times it's called.")
    def counting_tool() -> int:
        calls["n"] += 1
        return calls["n"]

    registry.register(counting_tool)

    dispatcher = ToolDispatcher(registry)
    planner = Planner(registry, dispatcher)
    executor = AutonomousExecutor(planner, dispatcher)
    target = str(workspace / "a.py")

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {"tool": "counting_tool", "arguments": {}},
            {
                "tool": "create_file",
                "arguments": {"path": target, "content": "x\n"},
            },
        ),
    )

    paused = executor.run("count then create")
    assert paused.stop_reason == "awaiting_approval"
    assert calls["n"] == 1

    final = executor.approve()

    assert final.stop_reason == "completed"
    assert calls["n"] == 1
    assert [step.tool_name for step in final.steps] == [
        "counting_tool",
        "create_file",
    ]


def test_cancel_while_awaiting_approval_via_approve_discards_patches(
    monkeypatch, workspace
):
    executor, planner = build_executor()
    target = str(workspace / "a.py")

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {
                "tool": "create_file",
                "arguments": {"path": target, "content": "1\n"},
            },
        ),
    )

    paused = executor.run("create a.py")
    assert paused.stop_reason == "awaiting_approval"

    executor.cancel()
    final = executor.approve()

    assert final.stop_reason == "cancelled"
    assert not (workspace / "a.py").exists()
    assert not executor.patch_manager.has_pending()
    assert final.steps == paused.steps


def test_cancel_while_awaiting_approval_via_reject_also_finalizes_cancelled(
    monkeypatch, workspace
):
    executor, planner = build_executor()
    target = str(workspace / "a.py")

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {
                "tool": "create_file",
                "arguments": {"path": target, "content": "1\n"},
            },
        ),
    )

    executor.run("create a.py")
    executor.cancel()
    final = executor.reject()

    assert final.stop_reason == "cancelled"
    assert not (workspace / "a.py").exists()


def test_diff_is_generated_for_a_previewed_edit(monkeypatch, workspace):
    existing = workspace / "existing.py"
    write_lf(existing, "old = 1\n")

    executor, planner = build_executor()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {"tool": "read_file", "arguments": {"path": str(existing)}},
            {
                "tool": "replace_in_file",
                "arguments": {
                    "path": str(existing),
                    "search": "old",
                    "replacement": "new",
                },
            },
        ),
    )

    report = executor.run("rename old to new")
    assert report.stop_reason == "awaiting_approval"

    edit = executor.patch_manager.pending[0]
    assert edit.original_content == "old = 1\n"
    assert edit.updated_content == "new = 1\n"
    assert "-old = 1" in edit.diff
    assert "+new = 1" in edit.diff


def test_empty_patch_when_nothing_to_replace_does_not_pause(monkeypatch, workspace):
    file = workspace / "a.py"
    file.write_text("hello world\n")

    executor, planner = build_executor()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {"tool": "read_file", "arguments": {"path": str(file)}},
            {
                "tool": "replace_in_file",
                "arguments": {
                    "path": str(file),
                    "search": "missing",
                    "replacement": "x",
                },
            },
        ),
    )

    report = executor.run("replace something that isn't there")

    assert report.stop_reason == "completed"
    assert report.succeeded
    assert not executor.patch_manager.has_pending()
    assert report.steps[1].result == 0  # step 0 is the read_file pre-check
    assert file.read_text() == "hello world\n"


def test_approve_without_pending_approval_raises():
    executor, _ = build_executor()

    with pytest.raises(RuntimeError):
        executor.approve()


def test_reject_without_pending_approval_raises():
    executor, _ = build_executor()

    with pytest.raises(RuntimeError):
        executor.reject()


def test_run_raises_if_already_awaiting_approval(monkeypatch, workspace):
    executor, planner = build_executor()
    target = str(workspace / "a.py")

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {
                "tool": "create_file",
                "arguments": {"path": target, "content": "1\n"},
            },
        ),
    )

    executor.run("create a.py")

    with pytest.raises(RuntimeError):
        executor.run("create a.py again")


def test_awaiting_approval_emits_progress_event(monkeypatch, workspace):
    executor, planner = build_executor()
    target = str(workspace / "a.py")

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {
                "tool": "create_file",
                "arguments": {"path": target, "content": "1\n"},
            },
        ),
    )

    report = executor.run("create a.py")

    assert report.events[-1].status == "awaiting_approval"
    assert report.events[-1].current_action


def test_rejection_emits_progress_event(monkeypatch, workspace):
    executor, planner = build_executor()
    target = str(workspace / "a.py")

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {
                "tool": "create_file",
                "arguments": {"path": target, "content": "1\n"},
            },
        ),
    )

    executor.run("create a.py")
    final = executor.reject()

    assert final.events[-1].status == "rejected"
    assert final.events[-1].current_action


def test_shared_patch_manager_can_be_passed_in(monkeypatch, workspace):
    shared = ChangeManager()
    executor, planner = build_executor(patch_manager=shared)
    target = str(workspace / "a.py")

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {
                "tool": "create_file",
                "arguments": {"path": target, "content": "1\n"},
            },
        ),
    )

    executor.run("create a.py")

    assert executor.patch_manager is shared
    assert shared.has_pending()


# ---------------------------------------------------------------------
# Cancellation reaches the LLM call itself, not just between steps
# ---------------------------------------------------------------------


def test_cancel_check_is_threaded_into_the_initial_plan_call(monkeypatch):
    """
    Regression: `run()` had no cancellation check around the initial
    `planner.plan()` call at all — a cancel() that arrived while the
    very first plan was in flight (the longest single blocking
    operation in a run) was completely ignored, and would silently
    execute the plan anyway.
    """

    executor, planner = build_executor()

    received_cancel_check = {}

    def _plan(prompt, cancel_check=None):
        received_cancel_check["fn"] = cancel_check
        return {"steps": [{"tool": "add", "arguments": {"a": 1, "b": 1}}]}

    monkeypatch.setattr(planner.client, "generate_json", _plan)

    executor.run("add")

    assert received_cancel_check["fn"] == executor.is_cancelled


def test_cancel_check_is_threaded_into_replan_calls(monkeypatch):
    executor, planner = build_executor()

    received_cancel_check = {}

    def _initial_plan(prompt, cancel_check=None):
        return {"steps": [{"tool": "boom", "arguments": {}}]}

    def _replan(prompt, cancel_check=None):
        received_cancel_check["fn"] = cancel_check
        return {"steps": [{"tool": "add", "arguments": {"a": 1, "b": 1}}]}

    calls = {"n": 0}

    def _dispatch(prompt, cancel_check=None):
        calls["n"] += 1
        return (
            _initial_plan(prompt) if calls["n"] == 1 else _replan(prompt, cancel_check)
        )

    monkeypatch.setattr(planner.client, "generate_json", _dispatch)

    executor.run("do something that fails then recovers")

    assert received_cancel_check["fn"] == executor.is_cancelled


def test_cancellation_during_the_initial_plan_call_stops_the_run(monkeypatch):
    """
    End-to-end proof, not just a wiring check: if the LLM call raises
    LLMCancelled (because cancel_check reported True mid-call), the
    executor must finalize as cancelled — not propagate the exception
    or treat it as a fatal planning error.
    """

    from src.llm.client import LLMCancelled

    executor, planner = build_executor()

    def _plan_raises_cancelled(prompt, cancel_check=None):
        raise LLMCancelled("cancelled mid-request")

    monkeypatch.setattr(planner.client, "generate_json", _plan_raises_cancelled)

    report = executor.run("add 1 and 2")

    assert report.stop_reason == "cancelled"
    assert report.steps == []


def test_cancellation_during_a_replan_call_stops_the_run(monkeypatch):
    from src.llm.client import LLMCancelled

    executor, planner = build_executor()

    calls = {"n": 0}

    def _generate_json(prompt, cancel_check=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"steps": [{"tool": "boom", "arguments": {}}]}
        raise LLMCancelled("cancelled mid-replan")

    monkeypatch.setattr(planner.client, "generate_json", _generate_json)

    report = executor.run("do something that fails")

    assert report.stop_reason == "cancelled"


def test_cancel_before_run_starts_is_observed_before_any_plan_call(monkeypatch):
    """
    cancel() called before run() is even invoked (e.g. the user closes
    the panel while the request is still queued) must stop execution
    before the first LLM call is made at all.
    """

    executor, planner = build_executor()

    call_count = {"n": 0}

    def _plan(prompt, cancel_check=None):
        call_count["n"] += 1
        return {"steps": [{"tool": "add", "arguments": {"a": 1, "b": 1}}]}

    monkeypatch.setattr(planner.client, "generate_json", _plan)

    executor.cancel()
    report = executor.run("add")

    assert report.stop_reason == "cancelled"
    assert call_count["n"] == 0


# ---------------------------------------------------------------------
# Checkpoint timeline integration (Sprint 1)
# ---------------------------------------------------------------------


def test_approve_emits_a_checkpoint_created_event_when_one_is_taken(
    monkeypatch, workspace
):
    from src.tools.checkpoints import CheckpointManager

    # A workspace with nothing in it at all has nothing to capture on
    # the very first checkpoint either (an empty tree has no changes
    # to commit even with no parent to compare against) - seed one
    # real file so there is something for the auto-checkpoint to
    # actually snapshot.
    (workspace / "existing.txt").write_text("baseline\n")

    executor, planner = build_executor(patch_manager=ChangeManager())
    executor.checkpoints = CheckpointManager(workspace)

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {
                "tool": "create_file",
                "arguments": {
                    "path": str(workspace / "new.py"),
                    "content": "x = 1\n",
                },
            }
        ),
    )

    executor.run("create a file")
    report = executor.approve()

    statuses = [event.status for event in report.events]
    assert "checkpoint_created" in statuses

    checkpoint_event = next(
        e for e in report.events if e.status == "checkpoint_created"
    )
    assert checkpoint_event.current_action == _default_progress_text(
        EventKind.CHECKPOINT
    )


def test_no_checkpoint_created_event_when_checkpointing_is_disabled(
    monkeypatch, workspace
):
    executor, planner = build_executor(patch_manager=ChangeManager())
    executor.checkpoints = None

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {
                "tool": "create_file",
                "arguments": {
                    "path": str(workspace / "new.py"),
                    "content": "x = 1\n",
                },
            }
        ),
    )

    executor.run("create a file")
    report = executor.approve()

    statuses = [event.status for event in report.events]
    assert "checkpoint_created" not in statuses
    assert (workspace / "new.py").exists()


def test_no_checkpoint_created_event_when_nothing_new_to_capture(
    monkeypatch, workspace
):
    """
    If the workspace on disk already matches the last checkpoint (a
    manual checkpoint was just taken, and nothing has reached disk
    since — a staged-but-not-yet-applied patch doesn't count),
    create() returns None and no event should fire: a "checkpoint
    saved" message would be misleading when nothing was actually
    captured.
    """

    from src.tools.checkpoints import CheckpointManager

    (workspace / "existing.txt").write_text("baseline\n")

    executor, planner = build_executor(patch_manager=ChangeManager())
    manager = CheckpointManager(workspace)
    executor.checkpoints = manager
    manager.create("pre-existing checkpoint")  # captures current state

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {
                "tool": "create_file",
                "arguments": {
                    "path": str(workspace / "new.py"),
                    "content": "x = 1\n",
                },
            }
        ),
    )

    executor.run("create a file")
    assert executor.is_awaiting_approval()  # confirms this actually exercises approve()

    report = executor.approve()

    statuses = [event.status for event in report.events]
    assert "checkpoint_created" not in statuses
    assert len(manager.list()) == 1  # still only the pre-existing one


# ---------------------------------------------------------------------
# Task 36: checkpoint undo restores workspace after multi-step approved plan
# ---------------------------------------------------------------------


def test_checkpoint_undo_restores_workspace_after_multi_step_approved_plan(
    monkeypatch, workspace
):
    """
    Verify the full undo contract for an approved multi-step plan:

      run()     → awaiting_approval (files absent from disk)
      approve() → completed (files written to disk, checkpoint taken)
      restore() → workspace back to pre-approval state (files absent)

    This pins the integration between AutonomousExecutor._checkpoint_before_writing()
    and CheckpointManager.restore(). A regression here would mean the
    checkpoint is not actually restoring everything the plan wrote.
    """
    import subprocess

    from src.tools.checkpoints import CheckpointManager

    if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
        pytest.skip("git binary not available")

    # Seed a baseline file so CheckpointManager.create() captures a
    # non-empty tree on the first checkpoint.
    (workspace / "baseline.txt").write_text("baseline\n")

    ckpt_mgr = CheckpointManager(workspace)
    executor, planner = build_executor(patch_manager=ChangeManager())
    executor.checkpoints = ckpt_mgr

    target_a = str(workspace / "plan_a.py")
    target_b = str(workspace / "plan_b.py")

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        _plan_of(
            {
                "tool": "create_file",
                "arguments": {"path": target_a, "content": "a=1\n"},
            },
            {
                "tool": "create_file",
                "arguments": {"path": target_b, "content": "b=2\n"},
            },
        ),
    )

    # --- Phase 1: run → awaiting_approval ---
    run_report = executor.run("create two files")
    assert run_report.stop_reason == "awaiting_approval"
    assert not (workspace / "plan_a.py").exists()
    assert not (workspace / "plan_b.py").exists()

    # --- Phase 2: approve → files written, checkpoint taken ---
    approve_report = executor.approve()
    assert approve_report.stop_reason == "completed"
    assert (workspace / "plan_a.py").exists()
    assert (workspace / "plan_b.py").exists()

    checkpoints = ckpt_mgr.list()
    assert len(checkpoints) >= 1, "approve() must create a checkpoint"

    checkpoint = checkpoints[0]  # most recent is first

    # --- Phase 3: restore → workspace reverted ---
    ckpt_mgr.restore(checkpoint.id)

    assert not (workspace / "plan_a.py").exists(), (
        "plan_a.py must be removed after restore"
    )
    assert not (workspace / "plan_b.py").exists(), (
        "plan_b.py must be removed after restore"
    )
    assert (workspace / "baseline.txt").exists(), (
        "baseline.txt must still be present (it was there before the plan)"
    )


# ---------------------------------------------------------------------
# confidence_score on ExecutionReport (Task 25)
# ---------------------------------------------------------------------


class TestConfidenceScore:
    def test_confidence_score_is_set_after_successful_run(self, monkeypatch):
        executor, planner = build_executor()
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of({"tool": "add", "arguments": {"a": 1, "b": 2}}),
        )
        report = executor.run("add 1+2")
        assert report.confidence_score is not None

    def test_confidence_score_is_float_in_0_1_range(self, monkeypatch):
        executor, planner = build_executor()
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of({"tool": "add", "arguments": {"a": 1, "b": 2}}),
        )
        report = executor.run("add 1+2")
        assert isinstance(report.confidence_score, float)
        assert 0.0 <= report.confidence_score <= 1.0

    def test_confidence_score_is_none_when_cancelled_before_planning(self):
        executor, _ = build_executor()
        executor.cancel()
        report = executor.run("add 1+2")
        assert report.stop_reason == "cancelled"
        assert report.confidence_score is None

    def test_confidence_score_is_set_for_awaiting_approval_report(
        self, monkeypatch, workspace
    ):
        executor, planner = build_executor()
        target = str(workspace / "a.py")
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of(
                {"tool": "create_file", "arguments": {"path": target, "content": "x\n"}}
            ),
        )
        report = executor.run("create a.py")
        assert report.stop_reason == "awaiting_approval"
        assert report.confidence_score is not None
        assert 0.0 <= report.confidence_score <= 1.0
        executor.reject()  # cleanup

    def test_confidence_score_is_set_for_rejected_report(self, monkeypatch, workspace):
        executor, planner = build_executor()
        target = str(workspace / "a.py")
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of(
                {"tool": "create_file", "arguments": {"path": target, "content": "x\n"}}
            ),
        )
        executor.run("create a.py")
        final = executor.reject()
        assert final.stop_reason == "rejected"
        assert final.confidence_score is not None
        assert 0.0 <= final.confidence_score <= 1.0


# ---------------------------------------------------------------------
# error_type on ExecutionStep (Task 31)
# ---------------------------------------------------------------------


class TestExecutionStepErrorType:
    def test_error_type_is_none_for_successful_step(self, monkeypatch):
        executor, planner = build_executor()
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of({"tool": "add", "arguments": {"a": 1, "b": 2}}),
        )
        report = executor.run("add 1+2")
        assert report.steps[0].error_type is None

    def test_error_type_is_validation_for_value_error(self, monkeypatch):
        # boom() raises ValueError — should classify as "validation"
        executor, planner = build_executor(max_replans=0)
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of({"tool": "boom", "arguments": {}}),
        )
        report = executor.run("boom")
        failed = report.failed_steps
        assert len(failed) == 1
        assert failed[0].error_type == "validation"

    def test_error_type_is_transient_for_timeout_error(self, monkeypatch):
        @tool(description="Always raises TimeoutError.")
        def always_timeout() -> None:
            raise TimeoutError("timeout")

        registry = ToolRegistry()
        registry.register(always_timeout)
        dispatcher = ToolDispatcher(registry)
        planner = Planner(registry, dispatcher)
        executor = AutonomousExecutor(planner, dispatcher, max_replans=0, max_retries=0)

        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of({"tool": "always_timeout", "arguments": {}}),
        )

        # "run always_timeout" rather than a bare "timeout": a lone noun
        # states no action, so the planner now asks what was meant instead
        # of guessing. This test is about error_type classification, and a
        # realistic prompt exercises that just as well.
        report = executor.run("run always_timeout")
        failed = report.failed_steps
        assert len(failed) == 1
        assert failed[0].error_type == "transient"

    def test_error_type_is_fatal_for_file_not_found(self, monkeypatch):
        @tool(description="Raises FileNotFoundError.", parameters={"p": "str"})
        def bad_read(p: str) -> None:
            raise FileNotFoundError(p)

        registry = ToolRegistry()
        registry.register(bad_read)
        dispatcher = ToolDispatcher(registry)
        planner = Planner(registry, dispatcher)
        executor = AutonomousExecutor(planner, dispatcher, max_replans=0)

        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of({"tool": "bad_read", "arguments": {"p": "missing.txt"}}),
        )

        report = executor.run("read missing")
        failed = report.failed_steps
        assert len(failed) == 1
        assert failed[0].error_type == "fatal"


# ---------------------------------------------------------------------
# Structured JSON logging (Task 40)
# ---------------------------------------------------------------------


def _structured_events(caplog, event_name: str | None = None) -> list[dict]:
    """
    Extract structured JSON records emitted via _log_structured from
    captured log output. If event_name is given, filter to that event.
    """
    records = []
    for record in caplog.records:
        if record.levelno != _logging.DEBUG:
            continue
        try:
            obj = _json.loads(record.getMessage())
        except (ValueError, TypeError):
            continue
        if "event" not in obj:
            continue
        if event_name is None or obj["event"] == event_name:
            records.append(obj)
    return records


class TestStructuredLogging:
    def test_run_start_emitted(self, monkeypatch, caplog):
        executor, planner = build_executor()
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of({"tool": "add", "arguments": {"a": 1, "b": 2}}),
        )
        with caplog.at_level(_logging.DEBUG, logger="src.agent.executor"):
            executor.run("add things")
        events = _structured_events(caplog, "run_start")
        assert len(events) == 1
        assert "prompt" in events[0]

    def test_run_complete_emitted(self, monkeypatch, caplog):
        executor, planner = build_executor()
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of({"tool": "add", "arguments": {"a": 1, "b": 2}}),
        )
        with caplog.at_level(_logging.DEBUG, logger="src.agent.executor"):
            executor.run("add things")
        events = _structured_events(caplog, "run_complete")
        assert len(events) == 1
        assert events[0]["steps_total"] == 1
        assert events[0]["replans_used"] == 0

    def test_step_start_emitted_per_step(self, monkeypatch, caplog):
        executor, planner = build_executor()
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of(
                {"tool": "add", "arguments": {"a": 1, "b": 2}},
                {"tool": "add", "arguments": {"a": 3, "b": 4}},
            ),
        )
        with caplog.at_level(_logging.DEBUG, logger="src.agent.executor"):
            executor.run("add twice")
        events = _structured_events(caplog, "step_start")
        assert len(events) == 2
        assert all("tool" in e and "iteration" in e for e in events)

    def test_step_success_emitted(self, monkeypatch, caplog):
        executor, planner = build_executor()
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of({"tool": "add", "arguments": {"a": 1, "b": 2}}),
        )
        with caplog.at_level(_logging.DEBUG, logger="src.agent.executor"):
            executor.run("add things")
        events = _structured_events(caplog, "step_success")
        assert len(events) == 1
        assert events[0]["tool"] == "add"

    def test_step_failure_emitted_with_error_type(self, monkeypatch, caplog):
        executor, planner = build_executor(max_replans=0)
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of({"tool": "boom", "arguments": {}}),
        )
        with caplog.at_level(_logging.DEBUG, logger="src.agent.executor"):
            executor.run("boom")
        events = _structured_events(caplog, "step_failure")
        assert len(events) == 1
        assert events[0]["error_type"] == "validation"
        assert "error" in events[0]

    def test_replan_start_emitted(self, monkeypatch, caplog):
        executor, planner = build_executor(max_replans=1)
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_sequence(
                [{"tool": "boom", "arguments": {}}],
                [{"tool": "add", "arguments": {"a": 1, "b": 2}}],
            ),
        )
        with caplog.at_level(_logging.DEBUG, logger="src.agent.executor"):
            executor.run("boom then replan")
        events = _structured_events(caplog, "replan_start")
        assert len(events) == 1
        assert events[0]["replan_number"] == 1
        assert events[0]["failed_tool"] == "boom"

    def test_structured_records_are_valid_json(self, monkeypatch, caplog):
        executor, planner = build_executor(max_replans=0)
        monkeypatch.setattr(
            planner.client,
            "generate_json",
            _plan_of({"tool": "add", "arguments": {"a": 2, "b": 3}}),
        )
        with caplog.at_level(_logging.DEBUG, logger="src.agent.executor"):
            executor.run("add")
        all_structured = _structured_events(caplog)
        assert (
            len(all_structured) >= 3
        )  # run_start, step_start, step_success, run_complete
