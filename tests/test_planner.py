import pytest

from src.agent.dispatcher import ToolDispatcher
from src.agent.planner import Planner
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


def build_planner():
    registry = ToolRegistry()
    registry.register(add)
    registry.register(boom)

    dispatcher = ToolDispatcher(registry)
    planner = Planner(registry, dispatcher)

    return planner


def test_plan_returns_ordered_tool_calls(monkeypatch):
    planner = build_planner()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        lambda prompt: {
            "steps": [
                {"tool": "add", "arguments": {"a": 1, "b": 2}},
                {"tool": "add", "arguments": {"a": 3, "b": 4}},
            ]
        },
    )

    steps = planner.plan("add 1+2 then 3+4")

    assert [step.tool_name for step in steps] == ["add", "add"]
    assert steps[0].kwargs == {"a": 1, "b": 2}
    assert steps[1].kwargs == {"a": 3, "b": 4}


def test_plan_rejects_unknown_tool(monkeypatch):
    planner = build_planner()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        lambda prompt: {
            "steps": [{"tool": "delete_everything", "arguments": {}}]
        },
    )

    with pytest.raises(ValueError):
        planner.plan("do something destructive")


def test_run_executes_steps_sequentially(monkeypatch):
    planner = build_planner()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        lambda prompt: {
            "steps": [
                {"tool": "add", "arguments": {"a": 1, "b": 2}},
                {"tool": "add", "arguments": {"a": 3, "b": 4}},
            ]
        },
    )

    results = planner.run("add 1+2 then 3+4")

    assert [result.succeeded for result in results] == [True, True]
    assert results[0].result == 3
    assert results[1].result == 7


def test_run_stops_at_first_failure(monkeypatch):
    planner = build_planner()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        lambda prompt: {
            "steps": [
                {"tool": "add", "arguments": {"a": 1, "b": 2}},
                {"tool": "boom", "arguments": {}},
                {"tool": "add", "arguments": {"a": 5, "b": 5}},
            ]
        },
    )

    results = planner.run("do three things, the middle one fails")

    assert len(results) == 2
    assert results[0].succeeded
    assert results[0].result == 3
    assert not results[1].succeeded
    assert "boom" in results[1].tool_name


# ---------------------------------------------------------------------
# replan()
# ---------------------------------------------------------------------


def test_replan_returns_ordered_tool_calls(monkeypatch):
    planner = build_planner()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        lambda prompt: {
            "steps": [{"tool": "add", "arguments": {"a": 10, "b": 20}}]
        },
    )

    steps = planner.replan(
        "add two numbers",
        completed=[{"tool": "add", "arguments": {"a": 1, "b": 2}, "result": 3}],
        failed={"tool": "boom", "arguments": {}, "error": "kaboom"},
    )

    assert [step.tool_name for step in steps] == ["add"]
    assert steps[0].kwargs == {"a": 10, "b": 20}


def test_replan_rejects_unknown_tool(monkeypatch):
    planner = build_planner()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        lambda prompt: {
            "steps": [{"tool": "delete_everything", "arguments": {}}]
        },
    )

    with pytest.raises(ValueError):
        planner.replan(
            "do something",
            completed=[],
            failed={"tool": "boom", "arguments": {}, "error": "kaboom"},
        )


def test_replan_supports_none_step(monkeypatch):
    planner = build_planner()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        lambda prompt: {"steps": [{"tool": "none", "arguments": {}}]},
    )

    steps = planner.replan(
        "do something",
        completed=[],
        failed={"tool": "boom", "arguments": {}, "error": "kaboom"},
    )

    assert [step.tool_name for step in steps] == ["none"]


def test_build_replan_prompt_includes_context(monkeypatch):
    planner = build_planner()

    prompt = planner.build_replan_prompt(
        "add two numbers",
        completed=[{"tool": "add", "arguments": {"a": 1, "b": 2}, "result": 3}],
        failed={"tool": "boom", "arguments": {}, "error": "kaboom"},
    )

    assert "add two numbers" in prompt
    assert '"result": 3' in prompt
    assert '"error": "kaboom"' in prompt
    assert "boom" in prompt


def test_run_handles_none_step_without_dispatch(monkeypatch):
    planner = build_planner()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        lambda prompt: {"steps": [{"tool": "none", "arguments": {}}]},
    )

    results = planner.run("say hello")

    assert len(results) == 1
    assert results[0].tool_name == "none"
    assert results[0].succeeded
    assert results[0].result is None
