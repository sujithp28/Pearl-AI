"""
The one set of tests that matters most for this feature: proving
personality/emoji configuration has ZERO effect on planning, tool
selection, tool execution, code generation, or the LLM prompt/output
pipeline — only on the wording of Pearl's own status messages.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.agent.dispatcher import ToolDispatcher
from src.agent.executor import AutonomousExecutor
from src.agent.planner import Planner
from src.tools.metadata import tool
from src.tools.registry import ToolRegistry

PERSONALITY_PACKAGE = Path(__file__).resolve().parents[1] / "src" / "personality"


@tool(
    description="Add two numbers.",
    parameters={"a": "int", "b": "int"},
    returns="int",
)
def add(a: int, b: int) -> int:
    return a + b


def _build() -> tuple[Planner, ToolDispatcher]:
    registry = ToolRegistry()
    registry.register(add)
    dispatcher = ToolDispatcher(registry)
    return Planner(registry, dispatcher), dispatcher


# ---------------------------------------------------------------------
# Planning prompt is byte-for-byte identical regardless of personality
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "personality", ["professional", "friendly", "cheeky", "savage"]
)
@pytest.mark.parametrize("emoji_mode", ["none", "minimal", "normal", "fun"])
def test_planning_prompt_is_unaffected_by_personality_settings(
    monkeypatch, personality, emoji_mode
):
    from src.config.settings import Settings

    planner, _ = _build()
    baseline = planner.build_prompt("fix the login bug")

    monkeypatch.setattr(Settings, "PERSONALITY", personality)
    monkeypatch.setattr(Settings, "EMOJI_MODE", emoji_mode)

    varied = planner.build_prompt("fix the login bug")

    assert varied == baseline


def test_replan_prompt_is_unaffected_by_personality_settings(monkeypatch):
    from src.config.settings import Settings

    planner, _ = _build()
    baseline = planner.build_replan_prompt(
        "fix the bug",
        completed=[{"tool": "add", "arguments": {"a": 1, "b": 2}, "result": 3}],
        failed={"tool": "add", "arguments": {}, "error": "boom"},
    )

    monkeypatch.setattr(Settings, "PERSONALITY", "savage")
    monkeypatch.setattr(Settings, "EMOJI_MODE", "fun")

    varied = planner.build_replan_prompt(
        "fix the bug",
        completed=[{"tool": "add", "arguments": {"a": 1, "b": 2}, "result": 3}],
        failed={"tool": "add", "arguments": {}, "error": "boom"},
    )

    assert varied == baseline


# ---------------------------------------------------------------------
# Tool dispatch / execution results are identical regardless of
# personality — only ProgressEvent.current_action's wording differs.
# ---------------------------------------------------------------------


def test_tool_execution_result_is_identical_across_personalities(monkeypatch):
    from src.config.settings import Settings

    _, dispatcher = _build()

    monkeypatch.setattr(Settings, "PERSONALITY", "professional")
    professional_result = dispatcher.execute("add", a=2, b=3)

    monkeypatch.setattr(Settings, "PERSONALITY", "savage")
    savage_result = dispatcher.execute("add", a=2, b=3)

    assert professional_result == savage_result == 5


def test_autonomous_run_outcome_is_identical_across_personalities(monkeypatch):
    """
    Same plan, same steps, same results, same stop reason — the only
    thing allowed to differ between a "professional"-run and a
    "savage"-run is each ProgressEvent's `current_action` text.
    """

    registry = ToolRegistry()
    registry.register(add)
    dispatcher = ToolDispatcher(registry)
    planner = Planner(registry, dispatcher)

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        lambda prompt, cancel_check=None: {
            "steps": [{"tool": "add", "arguments": {"a": 1, "b": 2}}]
        },
    )

    professional_executor = AutonomousExecutor(
        planner, dispatcher, personality=_manager("professional")
    )
    professional_report = professional_executor.run("add 1 and 2")

    savage_executor = AutonomousExecutor(
        planner, dispatcher, personality=_manager("savage")
    )
    savage_report = savage_executor.run("add 1 and 2")

    assert professional_report.stop_reason == savage_report.stop_reason == "completed"
    assert [s.result for s in professional_report.steps] == [
        s.result for s in savage_report.steps
    ]
    assert [s.tool_name for s in professional_report.steps] == [
        s.tool_name for s in savage_report.steps
    ]
    assert [s.summary for s in professional_report.steps] == [
        s.summary for s in savage_report.steps
    ]

    # The *only* thing allowed to differ: wording of current_action.
    professional_actions = [e.current_action for e in professional_report.events]
    savage_actions = [e.current_action for e in savage_report.events]
    assert professional_actions != savage_actions
    assert [e.status for e in professional_report.events] == [
        e.status for e in savage_report.events
    ]


def _manager(personality: str):
    from src.personality import PersonalityManager

    return PersonalityManager(personality=personality, emoji_mode="minimal")


# ---------------------------------------------------------------------
# Structural guarantee: the personality package cannot reach an LLM
# call even if someone tried — it doesn't import anything that could.
# ---------------------------------------------------------------------


def test_personality_package_never_imports_llm_or_agent_modules():
    """
    Static guarantee, not just a behavioral one: nothing under
    `src/personality/` imports `src.llm` or `src.agent` — so it is
    *structurally* incapable of touching a prompt or a model call, not
    just observed not to in these tests.
    """

    forbidden_prefixes = ("src.llm", "src.agent")

    for path in PERSONALITY_PACKAGE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name
                    assert not module.startswith(forbidden_prefixes), (
                        f"{path.name} imports {module}"
                    )
                continue
            else:
                continue

            assert not module.startswith(forbidden_prefixes), (
                f"{path.name} imports {module}"
            )
