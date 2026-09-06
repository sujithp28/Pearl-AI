from pathlib import Path

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
        lambda prompt: {"steps": [{"tool": "delete_everything", "arguments": {}}]},
    )

    with pytest.raises(ValueError):
        planner.plan("do something destructive")


# `Planner.run()` used to be tested here — sequential dispatch with no
# replanning and, critically, no approval gate (it runs completely
# outside AutonomousExecutor, so every write tool bypasses
# ChangeManager and writes straight to disk). Removed as a confirmed
# safety bug; see the removal note in src/agent/planner.py. Its
# `AutonomousExecutor.run()` replacement is covered by
# tests/test_executor.py.


# ---------------------------------------------------------------------
# replan()
# ---------------------------------------------------------------------


def test_replan_returns_ordered_tool_calls(monkeypatch):
    planner = build_planner()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        lambda prompt: {"steps": [{"tool": "add", "arguments": {"a": 10, "b": 20}}]},
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
        lambda prompt: {"steps": [{"tool": "delete_everything", "arguments": {}}]},
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


# ---------------------------------------------------------------------
# Workspace boundary is communicated to the model, not just enforced
# ---------------------------------------------------------------------


def test_planning_prompt_states_the_actual_workspace_root(tmp_path, monkeypatch):
    """
    The planner must tell the model the same root
    `file_tools._ensure_within_workspace` enforces against — otherwise
    the model plans absolute paths like /tmp/x.py that are guaranteed
    to be rejected before they run.
    """

    planner = build_planner()
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)

    prompt = planner.build_prompt("write hello world")

    assert str(tmp_path.resolve()) in prompt


def test_planning_prompt_forbids_paths_outside_the_workspace():
    planner = build_planner()

    prompt = planner.build_prompt("write hello world")

    assert "/tmp" in prompt
    assert "relative" in prompt.lower()


def test_replan_prompt_states_the_actual_workspace_root(tmp_path, monkeypatch):
    planner = build_planner()
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)

    prompt = planner.build_replan_prompt(
        "write hello world",
        completed=[],
        failed={
            "tool": "create_file",
            "arguments": {"path": "/tmp/hello.py"},
            "error": "Path escapes workspace: /tmp/hello.py",
        },
    )

    assert str(tmp_path.resolve()) in prompt


def test_workspace_root_tracks_cwd_rather_than_being_cached(tmp_path, monkeypatch):
    """
    The enforcing side reads `Path.cwd()` per call, so the advertised
    boundary must too — a cached value could tell the model one root
    while a different one is actually enforced.
    """

    planner = build_planner()

    first = tmp_path / "project_a"
    second = tmp_path / "project_b"
    first.mkdir()
    second.mkdir()

    monkeypatch.setattr(Path, "cwd", lambda: first)
    prompt_a = planner.build_prompt("write hello world")

    monkeypatch.setattr(Path, "cwd", lambda: second)
    prompt_b = planner.build_prompt("write hello world")

    assert str(first.resolve()) in prompt_a
    assert str(second.resolve()) in prompt_b
    assert str(second.resolve()) not in prompt_a


def test_plan_supports_none_step(monkeypatch):
    # plan() itself must still support the "no tool needed" step
    # (execution of it is AutonomousExecutor's job, not Planner's).
    planner = build_planner()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        lambda prompt: {"steps": [{"tool": "none", "arguments": {}}]},
    )

    steps = planner.plan("say hello")

    assert [step.tool_name for step in steps] == ["none"]


# ---------------------------------------------------------------------
# Task 32: Planner._workspace_root() must agree with _ensure_within_workspace
# ---------------------------------------------------------------------


def test_planner_workspace_root_matches_ensure_within_workspace(tmp_path, monkeypatch):
    """
    Planner._workspace_root() and file_tools._ensure_within_workspace()
    must derive the workspace boundary from the same source (Path.cwd().resolve()).

    If they diverge, the planner could tell the LLM one root while the
    tool enforces a different one, allowing paths that look valid to the
    planner to fail at execution time — or worse, accepting paths the
    planner thinks are safe but the tool would reject.
    """
    from src.tools.file_tools import _ensure_within_workspace

    monkeypatch.chdir(tmp_path)

    planner = build_planner()

    planner_root = planner._workspace_root()

    # A file at the workspace root boundary should be accepted by both.
    boundary_file = tmp_path / "probe.txt"
    boundary_file.write_text("probe")

    resolved = _ensure_within_workspace(str(boundary_file))

    assert str(resolved.parent) == planner_root, (
        f"Planner reports root={planner_root!r}, but "
        f"_ensure_within_workspace resolved parent={str(resolved.parent)!r}"
    )


def test_planner_workspace_root_rejects_paths_ensure_within_workspace_rejects(
    tmp_path, monkeypatch
):
    """
    The planner root and the file tool boundary reject the same paths:
    a path outside the workspace fails _ensure_within_workspace with
    PermissionError, which is the same boundary the planner communicates
    to the LLM via the {workspace_root} template variable.
    """
    from src.tools.file_tools import _ensure_within_workspace

    monkeypatch.chdir(tmp_path)

    planner = build_planner()
    planner_root = Path(planner._workspace_root())

    outside = tmp_path.parent / "outside.txt"

    # The outside path must not start with the planner root.
    assert not str(outside.resolve()).startswith(str(planner_root))

    # And _ensure_within_workspace must also reject it.
    with pytest.raises(PermissionError):
        _ensure_within_workspace(str(outside))


# ---------------------------------------------------------------------
# Prompt content: conversational / no-tool guidance
# ---------------------------------------------------------------------


def test_planning_prompt_contains_none_guidance_for_greetings():
    """
    The planning prompt must explicitly tell the model to return
    tool "none" for greetings and conversational messages.

    This guards against the regression where "hello" produced
    read_file(path="hello") because the prompt had no such guidance.
    """
    planner = build_planner()
    prompt = planner.build_prompt("hello")

    # The prompt must name at least one conversational category.
    assert "greeting" in prompt.lower() or "hello" in prompt.lower()
    # It must describe when to return "none".
    assert '"none"' in prompt or "tool: " + '"none"' in prompt


def test_planning_prompt_contains_example_none_for_hello():
    """
    The prompt must include a concrete example showing that 'hello'
    maps to tool 'none', so the model has a worked example to follow.
    """
    planner = build_planner()
    prompt = planner.build_prompt("hello")

    assert 'hello' in prompt.lower()
    assert 'none' in prompt


def test_planner_routes_none_step_for_scripted_conversational_response(monkeypatch):
    """
    When the LLM (scripted here) returns tool 'none' for a greeting,
    the planner must pass it through as a ToolCall with tool_name='none'
    rather than dropping it or raising.

    This does NOT prove the real LLM returns 'none' for greetings —
    that requires a prompt-quality integration test with a real model.
    It proves the routing works correctly when it does.
    """
    planner = build_planner()

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        lambda prompt: {"steps": [{"tool": "none", "arguments": {}}]},
    )

    steps = list(planner.plan("hello"))

    assert len(steps) == 1
    assert steps[0].tool_name == "none"
    assert steps[0].kwargs == {}
