"""
Tests for the `pearl/personality` MCP method — the timeline-label
lookup the VS Code extension's plan-preview flow (`handleUserMessage`,
which has no `AutonomousExecutor`/`pearl/progress` stream behind it)
uses instead.
"""

from __future__ import annotations

from src.mcp.protocol import JsonRpcRequest
from src.mcp.server import MCPServer
from src.personality import EventKind, PersonalityManager
from src.tools.registry import ToolRegistry


def build_server(personality: PersonalityManager | None = None) -> MCPServer:
    return MCPServer(ToolRegistry(), personality=personality)


def test_personality_labels_covers_every_timeline_stage():
    server = build_server(PersonalityManager(personality="cheeky", emoji_mode="none"))

    response = server.handle_request(JsonRpcRequest(method="pearl/personality", id=1))

    assert response.error is None
    assert set(response.result["labels"].keys()) == {
        "planning",
        "plan_ready",
        "waiting_approval",
        "running_tool",
        "completed",
    }


def test_personality_labels_reflect_the_configured_personality():
    server = build_server(
        PersonalityManager(personality="professional", emoji_mode="none")
    )

    response = server.handle_request(JsonRpcRequest(method="pearl/personality", id=1))

    assert response.result["labels"]["completed"] == "Task completed successfully."


def test_personality_labels_match_the_manager_directly():
    manager = PersonalityManager(personality="savage", emoji_mode="minimal")
    server = build_server(manager)

    response = server.handle_request(JsonRpcRequest(method="pearl/personality", id=1))

    assert response.result["labels"]["planning"] == manager.format(EventKind.PLANNING)
    assert response.result["labels"]["plan_ready"] == manager.format(
        EventKind.PLAN_READY
    )
    assert response.result["labels"]["waiting_approval"] == manager.format(
        EventKind.APPROVAL
    )
    assert response.result["labels"]["running_tool"] == manager.format(
        EventKind.EXECUTING
    )
    assert response.result["labels"]["completed"] == manager.format(EventKind.COMPLETED)


def test_personality_labels_uses_default_manager_when_none_given():
    server = build_server()

    response = server.handle_request(JsonRpcRequest(method="pearl/personality", id=1))

    assert response.error is None
    assert all(response.result["labels"].values())


def test_initialize_advertises_pearl_personality_capability():
    server = build_server()

    response = server.handle_request(JsonRpcRequest(method="initialize", id=1))

    assert response.result["capabilities"]["experimental"]["pearlPersonality"] == {}
