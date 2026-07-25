import pytest

from src.agent.agent import PearlAgent
from src.llm.parser import ToolCall
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


# ---------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------


def test_run_records_conversation_and_execution(monkeypatch):
    agent = build_agent()

    monkeypatch.setattr(
        agent.selector,
        "select",
        lambda prompt: ToolCall(tool_name="add", args=(), kwargs={"a": 1, "b": 2}),
    )

    result = agent.run("add 1 and 2")

    assert result == 3
    assert [turn.role for turn in agent.memory.conversation] == [
        "user",
        "agent",
    ]
    assert agent.memory.conversation[0].content == "add 1 and 2"
    assert agent.memory.conversation[1].content == "3"

    executions = agent.memory.execution_history
    assert len(executions) == 1
    assert executions[0].tool_name == "add"
    assert executions[0].result == 3
    assert executions[0].succeeded


def test_run_records_failed_execution_and_reraises(monkeypatch):
    agent = build_agent()

    monkeypatch.setattr(
        agent.selector,
        "select",
        lambda prompt: ToolCall(tool_name="boom", args=(), kwargs={}),
    )

    with pytest.raises(Exception):
        agent.run("do something that fails")

    executions = agent.memory.execution_history
    assert len(executions) == 1
    assert executions[0].tool_name == "boom"
    assert not executions[0].succeeded


def test_run_records_none_tool_response(monkeypatch):
    agent = build_agent()

    monkeypatch.setattr(
        agent.selector,
        "select",
        lambda prompt: ToolCall(tool_name="none", args=(), kwargs={}),
    )

    result = agent.run("say hello")

    assert result == "No suitable tool found for this request."
    assert agent.memory.conversation[-1].content == result
    assert agent.memory.execution_history == []


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


# ---------------------------------------------------------------------
# plan_and_run()
# ---------------------------------------------------------------------


def test_plan_and_run_records_task_and_executions(monkeypatch):
    agent = build_agent()

    monkeypatch.setattr(
        agent.planner.client,
        "generate_json",
        lambda prompt: {
            "steps": [
                {"tool": "add", "arguments": {"a": 1, "b": 2}},
                {"tool": "add", "arguments": {"a": 3, "b": 4}},
            ]
        },
    )

    results = agent.plan_and_run("add twice")

    assert [r.result for r in results] == [3, 7]

    assert len(agent.memory.tasks) == 1
    task = agent.memory.tasks[0]
    assert task.description == "add twice"
    assert task.status == "completed"

    assert len(agent.memory.execution_history) == 2
    assert agent.memory.conversation[0].content == "add twice"


def test_plan_and_run_marks_task_failed_on_step_error(monkeypatch):
    agent = build_agent()

    monkeypatch.setattr(
        agent.planner.client,
        "generate_json",
        lambda prompt: {"steps": [{"tool": "boom", "arguments": {}}]},
    )

    results = agent.plan_and_run("do something that fails")

    assert not results[0].succeeded
    assert agent.memory.tasks[0].status == "failed"


# ---------------------------------------------------------------------
# run_autonomous()
# ---------------------------------------------------------------------


def test_run_autonomous_records_task_and_executions(monkeypatch):
    agent = build_agent()

    monkeypatch.setattr(
        agent.planner.client,
        "generate_json",
        lambda prompt: {
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
        lambda prompt: {"steps": [{"tool": "boom", "arguments": {}}]},
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
        lambda prompt: {
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


# ---------------------------------------------------------------------
# Legacy execution paths: deprecated, still functional
# ---------------------------------------------------------------------


def test_run_emits_deprecation_warning(monkeypatch):
    agent = build_agent()

    monkeypatch.setattr(
        agent.selector,
        "select",
        lambda prompt: ToolCall(tool_name="add", args=(), kwargs={"a": 1, "b": 2}),
    )

    with pytest.warns(DeprecationWarning, match="run_autonomous"):
        agent.run("add 1 and 2")


def test_plan_and_run_emits_deprecation_warning(monkeypatch):
    agent = build_agent()

    monkeypatch.setattr(
        agent.planner.client,
        "generate_json",
        lambda prompt: {"steps": [{"tool": "add", "arguments": {"a": 1, "b": 2}}]},
    )

    with pytest.warns(DeprecationWarning, match="run_autonomous"):
        agent.plan_and_run("add 1 and 2")


def test_run_autonomous_does_not_emit_deprecation_warning(monkeypatch, recwarn):
    agent = build_agent()

    monkeypatch.setattr(
        agent.planner.client,
        "generate_json",
        lambda prompt: {"steps": [{"tool": "add", "arguments": {"a": 1, "b": 2}}]},
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
