import pytest

from src.agent.agent import PearlAgent
from src.tools.metadata import tool
from src.tools.registry import ToolRegistry


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


def build_agent() -> PearlAgent:
    registry = ToolRegistry()
    registry.register(add)
    registry.register(boom)
    return PearlAgent(registry)


# `run()` (single-tool selection via LLMToolSelector) used to be
# tested here. Removed along with the method: it dispatched outside
# AutonomousExecutor, so it bypassed the approval gate entirely — a
# confirmed safety bug, not a style cleanup.


# ---------------------------------------------------------------------
# chat()
# ---------------------------------------------------------------------


def test_chat_records_conversation(monkeypatch):
    agent = build_agent()

    monkeypatch.setattr(agent.llm, "generate", lambda prompt, **kwargs: "hello there")

    response = agent.chat("hi")

    assert response == "hello there"
    assert [turn.content for turn in agent.memory.conversation] == [
        "hi",
        "hello there",
    ]


# `plan_and_run()` used to be tested here — same removal reason as
# `run()`: it called `Planner.run()`, which dispatches outside
# AutonomousExecutor and bypasses the approval gate.


# ---------------------------------------------------------------------
# run_autonomous() / approve() / reject()
# ---------------------------------------------------------------------


def test_run_autonomous_records_task_and_executions(monkeypatch):
    agent = build_agent()

    monkeypatch.setattr(
        agent.planner.client,
        "generate_json",
        lambda prompt, cancel_check=None: {
            "steps": [
                {"tool": "add", "arguments": {"a": 1, "b": 2}},
                {"tool": "add", "arguments": {"a": 3, "b": 4}},
            ]
        },
    )

    report = agent.run_autonomous("add twice")

    assert report.succeeded
    assert report.stop_reason == "completed"
    assert [step.result for step in report.steps] == [3, 7]

    assert len(agent.memory.tasks) == 1
    task = agent.memory.tasks[0]
    assert task.description == "add twice"
    assert task.status == "completed"

    assert len(agent.memory.execution_history) == 2
    assert agent.memory.conversation[0].content == "add twice"
    assert "completed" in agent.memory.conversation[-1].content


def test_run_autonomous_marks_task_failed_on_fatal_error(monkeypatch):
    agent = build_agent()

    monkeypatch.setattr(
        agent.planner.client,
        "generate_json",
        lambda prompt, cancel_check=None: {
            "steps": [{"tool": "boom", "arguments": {}}]
        },
    )

    report = agent.run_autonomous("do something that fails")

    assert not report.succeeded
    assert report.stop_reason == "fatal_error"
    assert agent.memory.tasks[0].status == "failed"
    assert not agent.memory.execution_history[0].succeeded


def test_run_autonomous_marks_task_failed_on_max_iterations(monkeypatch):
    agent = build_agent()

    monkeypatch.setattr(
        agent.planner.client,
        "generate_json",
        lambda prompt, cancel_check=None: {
            "steps": [
                {"tool": "add", "arguments": {"a": 1, "b": 1}},
                {"tool": "add", "arguments": {"a": 2, "b": 2}},
            ]
        },
    )

    report = agent.run_autonomous("add twice", max_iterations=1)

    assert not report.succeeded
    assert report.stop_reason == "max_iterations"
    assert len(report.steps) == 1
    assert agent.memory.tasks[0].status == "failed"


# `run()` and `plan_and_run()` used to be tested here for emitting
# DeprecationWarning. Both methods, and LLMToolSelector (which `run()`
# depended on), are gone: they bypassed the approval gate outside
# AutonomousExecutor, a confirmed safety bug rather than something to
# keep around deprecated. run_autonomous()/approve()/reject() is now
# the only execution path.


def test_run_autonomous_does_not_emit_deprecation_warning(monkeypatch, recwarn):
    agent = build_agent()

    monkeypatch.setattr(
        agent.planner.client,
        "generate_json",
        lambda prompt, cancel_check=None: {
            "steps": [{"tool": "add", "arguments": {"a": 1, "b": 2}}]
        },
    )

    agent.run_autonomous("add 1 and 2")

    assert not [w for w in recwarn.list if issubclass(w.category, DeprecationWarning)]


def test_agent_accepts_injected_memory():
    from src.memory import Memory

    registry = ToolRegistry()
    registry.register(add)

    memory = Memory()
    agent = PearlAgent(registry, memory=memory)

    assert agent.memory is memory


# ---------------------------------------------------------------------
# approve() / reject()
# ---------------------------------------------------------------------


def _build_agent_with_create_file():
    from src.tools.edit_tools import create_file

    registry = ToolRegistry()
    registry.register(add)
    registry.register(create_file)
    return PearlAgent(registry)


def test_run_autonomous_pauses_and_approve_writes_to_disk(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    agent = _build_agent_with_create_file()

    monkeypatch.setattr(
        agent.planner.client,
        "generate_json",
        lambda prompt, cancel_check=None: {
            "steps": [
                {
                    "tool": "create_file",
                    "arguments": {"path": "new.py", "content": "x = 1\n"},
                }
            ]
        },
    )

    paused = agent.run_autonomous("create a file")

    assert paused.stop_reason == "awaiting_approval"
    assert not (tmp_path / "new.py").exists()

    report = agent.approve()

    assert report.stop_reason == "completed"
    assert (tmp_path / "new.py").read_text() == "x = 1\n"


def test_reject_discards_staged_changes(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    agent = _build_agent_with_create_file()

    monkeypatch.setattr(
        agent.planner.client,
        "generate_json",
        lambda prompt, cancel_check=None: {
            "steps": [
                {
                    "tool": "create_file",
                    "arguments": {"path": "new.py", "content": "x = 1\n"},
                }
            ]
        },
    )

    agent.run_autonomous("create a file")
    report = agent.reject()

    assert report.stop_reason == "rejected"
    assert not (tmp_path / "new.py").exists()


def test_run_autonomous_raises_if_already_awaiting_approval(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    agent = _build_agent_with_create_file()

    monkeypatch.setattr(
        agent.planner.client,
        "generate_json",
        lambda prompt, cancel_check=None: {
            "steps": [
                {
                    "tool": "create_file",
                    "arguments": {"path": "new.py", "content": "x\n"},
                }
            ]
        },
    )

    agent.run_autonomous("create a file")

    with pytest.raises(RuntimeError, match="already awaiting approval"):
        agent.run_autonomous("do something else")


def test_approve_without_a_pending_run_raises():
    agent = build_agent()

    with pytest.raises(RuntimeError, match="No autonomous run"):
        agent.approve()


def test_reject_without_a_pending_run_raises():
    agent = build_agent()

    with pytest.raises(RuntimeError, match="No autonomous run"):
        agent.reject()


def test_steps_before_pause_are_not_double_recorded_after_approve(
    monkeypatch, tmp_path
):
    """
    ExecutionReport.steps accumulates across a pause/resume rather
    than resetting, so recording every step on every call would
    double-record whatever ran before the pause.
    """

    monkeypatch.chdir(tmp_path)
    agent = _build_agent_with_create_file()

    monkeypatch.setattr(
        agent.planner.client,
        "generate_json",
        lambda prompt, cancel_check=None: {
            "steps": [
                {"tool": "add", "arguments": {"a": 1, "b": 1}},
                {
                    "tool": "create_file",
                    "arguments": {"path": "new.py", "content": "x\n"},
                },
            ]
        },
    )

    agent.run_autonomous("add then create a file")
    agent.approve()

    assert len(agent.memory.execution_history) == 2


def test_task_is_not_marked_failed_while_merely_awaiting_approval(
    monkeypatch, tmp_path
):
    """
    Regression: the old run_autonomous() unconditionally completed the
    task with status "completed"/"failed" based on report.succeeded,
    even when the run had only paused for approval (succeeded is False
    for stop_reason="awaiting_approval") — marking an in-progress,
    nothing-wrong-yet run as failed.
    """

    monkeypatch.chdir(tmp_path)
    agent = _build_agent_with_create_file()

    monkeypatch.setattr(
        agent.planner.client,
        "generate_json",
        lambda prompt, cancel_check=None: {
            "steps": [
                {
                    "tool": "create_file",
                    "arguments": {"path": "new.py", "content": "x\n"},
                }
            ]
        },
    )

    agent.run_autonomous("create a file")

    assert agent.memory.tasks[0].status not in {"completed", "failed"}

    agent.approve()

    assert agent.memory.tasks[0].status == "completed"
