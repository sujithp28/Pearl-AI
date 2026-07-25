"""
Integration tests for the `pearl/runAutonomous` / `pearl/approvePatches`
/ `pearl/rejectPatches` MCP methods added in Phase 23.

These exercise `MCPServer.handle_request` directly (no subprocess),
matching the existing convention in `test_mcp_server.py`, but focus
specifically on the autonomous/patch-preview workflow: the complete
happy path, multi-file batches, rejection, cancellation while
awaiting approval, and recovery after a mid-plan replan — end to end,
through the real `AutonomousExecutor`/`PatchManager`/`Planner`, with
only the LLM boundary (`Planner.client.generate_json`) stubbed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agent.dispatcher import ToolDispatcher
from src.agent.planner import Planner
from src.mcp.protocol import INVALID_PARAMS, JsonRpcRequest
from src.mcp.server import MCPServer
from src.memory import Memory
from src.tools.edit_tools import create_file, replace_in_file
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


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(add)
    registry.register(boom)
    registry.register(create_file)
    registry.register(replace_in_file)
    return registry


def build_server() -> MCPServer:
    registry = build_registry()
    dispatcher = ToolDispatcher(registry)
    planner = Planner(registry, dispatcher)

    return MCPServer(
        registry,
        dispatcher=dispatcher,
        planner=planner,
        memory=Memory(),
    )


def _plan_of(*steps):
    # Accepts (and ignores) cancel_check: the executor always passes
    # it, bound to its own is_cancelled.
    return lambda prompt, cancel_check=None: {"steps": list(steps)}


def _plan_sequence(*plans):
    calls = {"n": 0}

    def _fn(prompt, cancel_check=None):
        index = min(calls["n"], len(plans) - 1)
        calls["n"] += 1
        return {"steps": plans[index]}

    return _fn


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    # Fake the workspace root for file/edit tools (via `Path.cwd()`,
    # which `_ensure_within_workspace` consults) WITHOUT changing the
    # real process cwd — Planner needs the real cwd (repo root) to
    # load its prompt templates by their relative path.
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    return tmp_path


# ---------------------------------------------------------------------
# Capability advertisement
# ---------------------------------------------------------------------


def test_initialize_reports_autonomous_capability_when_planner_present():
    server = build_server()

    response = server.handle_request(JsonRpcRequest(method="initialize", id=1))

    assert response.result["capabilities"]["experimental"]["pearlAutonomous"] == {}


# ---------------------------------------------------------------------
# Complete autonomous workflow: no patches, straight to completion
# ---------------------------------------------------------------------


def test_run_autonomous_completes_directly_when_no_edits_are_staged(monkeypatch):
    server = build_server()

    monkeypatch.setattr(
        server.planner.client,
        "generate_json",
        _plan_of({"tool": "add", "arguments": {"a": 1, "b": 2}}),
    )

    response = server.handle_request(
        JsonRpcRequest(method="pearl/runAutonomous", id=1, params={"prompt": "add 1+2"})
    )

    assert response.error is None
    assert response.result["stopReason"] == "completed"
    assert response.result["patches"] == []
    assert response.result["steps"] == [
        {
            "tool": "add",
            "arguments": {"a": 1, "b": 2},
            "succeeded": True,
            "summary": "'add' succeeded: 3",
        }
    ]


# ---------------------------------------------------------------------
# Single-file patch: pause, approve, resume
# ---------------------------------------------------------------------


def test_run_autonomous_pauses_with_a_single_file_patch(monkeypatch, workspace):
    server = build_server()
    target = str(workspace / "a.py")

    monkeypatch.setattr(
        server.planner.client,
        "generate_json",
        _plan_of(
            {
                "tool": "create_file",
                "arguments": {"path": target, "content": "print(1)\n"},
            }
        ),
    )

    response = server.handle_request(
        JsonRpcRequest(
            method="pearl/runAutonomous", id=1, params={"prompt": "create a.py"}
        )
    )

    assert response.result["stopReason"] == "awaiting_approval"
    assert len(response.result["patches"]) == 1
    patch = response.result["patches"][0]
    assert patch["path"] == target
    assert patch["isNewFile"] is True
    assert "+print(1)" in patch["diff"]
    assert not (workspace / "a.py").exists()


def test_approve_patches_writes_the_file_and_completes(monkeypatch, workspace):
    server = build_server()
    target = str(workspace / "a.py")

    monkeypatch.setattr(
        server.planner.client,
        "generate_json",
        _plan_of(
            {
                "tool": "create_file",
                "arguments": {"path": target, "content": "print(1)\n"},
            }
        ),
    )

    server.handle_request(
        JsonRpcRequest(
            method="pearl/runAutonomous", id=1, params={"prompt": "create a.py"}
        )
    )

    response = server.handle_request(
        JsonRpcRequest(method="pearl/approvePatches", id=2, params={})
    )

    assert response.error is None
    assert response.result["stopReason"] == "completed"
    assert response.result["patches"] == []
    assert (workspace / "a.py").read_text() == "print(1)\n"


def test_reject_patches_discards_and_writes_nothing(monkeypatch, workspace):
    server = build_server()
    target = str(workspace / "a.py")

    monkeypatch.setattr(
        server.planner.client,
        "generate_json",
        _plan_of(
            {
                "tool": "create_file",
                "arguments": {"path": target, "content": "print(1)\n"},
            }
        ),
    )

    server.handle_request(
        JsonRpcRequest(
            method="pearl/runAutonomous", id=1, params={"prompt": "create a.py"}
        )
    )

    response = server.handle_request(
        JsonRpcRequest(method="pearl/rejectPatches", id=2, params={})
    )

    assert response.error is None
    assert response.result["stopReason"] == "rejected"
    assert not (workspace / "a.py").exists()


# ---------------------------------------------------------------------
# Multi-file patch: single approval batch
# ---------------------------------------------------------------------


def test_run_autonomous_batches_multiple_files_into_one_approval(
    monkeypatch, workspace
):
    server = build_server()
    a, b, c = (str(workspace / name) for name in ("main.py", "models.py", "routes.py"))

    monkeypatch.setattr(
        server.planner.client,
        "generate_json",
        _plan_of(
            {"tool": "create_file", "arguments": {"path": a, "content": "1\n"}},
            {"tool": "create_file", "arguments": {"path": b, "content": "2\n"}},
            {"tool": "create_file", "arguments": {"path": c, "content": "3\n"}},
        ),
    )

    response = server.handle_request(
        JsonRpcRequest(
            method="pearl/runAutonomous",
            id=1,
            params={"prompt": "build three files"},
        )
    )

    assert response.result["stopReason"] == "awaiting_approval"
    assert {p["path"] for p in response.result["patches"]} == {a, b, c}

    approve_response = server.handle_request(
        JsonRpcRequest(method="pearl/approvePatches", id=2, params={})
    )

    assert approve_response.result["stopReason"] == "completed"
    assert (workspace / "main.py").read_text() == "1\n"
    assert (workspace / "models.py").read_text() == "2\n"
    assert (workspace / "routes.py").read_text() == "3\n"


# ---------------------------------------------------------------------
# Multiple approval rounds within one run
# ---------------------------------------------------------------------


def test_multiple_approval_rounds_in_one_run(monkeypatch, workspace):
    server = build_server()
    a = str(workspace / "a.py")
    b = str(workspace / "b.py")

    monkeypatch.setattr(
        server.planner.client,
        "generate_json",
        _plan_sequence(
            [{"tool": "create_file", "arguments": {"path": a, "content": "1\n"}}],
        ),
    )

    first = server.handle_request(
        JsonRpcRequest(
            method="pearl/runAutonomous", id=1, params={"prompt": "create a.py"}
        )
    )
    assert first.result["stopReason"] == "awaiting_approval"

    # A true "second approval round within one run" is covered by
    # the executor's own tests (test_executor.py). This test instead
    # confirms the MCP layer correctly rejects an overlapping
    # runAutonomous call and lets a fresh one start only after the
    # prior one is fully resolved.
    second_attempt = server.handle_request(
        JsonRpcRequest(
            method="pearl/runAutonomous", id=2, params={"prompt": "create b.py"}
        )
    )
    assert second_attempt.error is not None
    assert second_attempt.error.code == INVALID_PARAMS

    resolved = server.handle_request(
        JsonRpcRequest(method="pearl/approvePatches", id=3, params={})
    )
    assert resolved.result["stopReason"] == "completed"

    monkeypatch.setattr(
        server.planner.client,
        "generate_json",
        _plan_of({"tool": "create_file", "arguments": {"path": b, "content": "2\n"}}),
    )

    second = server.handle_request(
        JsonRpcRequest(
            method="pearl/runAutonomous", id=4, params={"prompt": "create b.py"}
        )
    )
    assert second.result["stopReason"] == "awaiting_approval"

    final = server.handle_request(
        JsonRpcRequest(method="pearl/approvePatches", id=5, params={})
    )
    assert final.result["stopReason"] == "completed"
    assert (workspace / "a.py").read_text() == "1\n"
    assert (workspace / "b.py").read_text() == "2\n"


# ---------------------------------------------------------------------
# Cancellation during approval
# ---------------------------------------------------------------------


def test_cancel_while_awaiting_approval_then_approve_finalizes_cancelled(
    monkeypatch, workspace
):
    server = build_server()
    target = str(workspace / "a.py")

    monkeypatch.setattr(
        server.planner.client,
        "generate_json",
        _plan_of(
            {
                "tool": "create_file",
                "arguments": {"path": target, "content": "1\n"},
            }
        ),
    )

    server.handle_request(
        JsonRpcRequest(
            method="pearl/runAutonomous", id=1, params={"prompt": "create a.py"}
        )
    )

    # Cancellation is requested out-of-band (e.g. a VS Code "Cancel"
    # action) directly on the executor MCPServer is holding.
    server._autonomous_executor.cancel()

    response = server.handle_request(
        JsonRpcRequest(method="pearl/approvePatches", id=2, params={})
    )

    assert response.result["stopReason"] == "cancelled"
    assert not (workspace / "a.py").exists()


def test_cancel_while_awaiting_approval_then_reject_also_finalizes_cancelled(
    monkeypatch, workspace
):
    server = build_server()
    target = str(workspace / "a.py")

    monkeypatch.setattr(
        server.planner.client,
        "generate_json",
        _plan_of(
            {
                "tool": "create_file",
                "arguments": {"path": target, "content": "1\n"},
            }
        ),
    )

    server.handle_request(
        JsonRpcRequest(
            method="pearl/runAutonomous", id=1, params={"prompt": "create a.py"}
        )
    )

    server._autonomous_executor.cancel()

    response = server.handle_request(
        JsonRpcRequest(method="pearl/rejectPatches", id=2, params={})
    )

    assert response.result["stopReason"] == "cancelled"


# ---------------------------------------------------------------------
# Recovery after replanning
# ---------------------------------------------------------------------


def test_recovers_after_a_replan_and_still_pauses_for_the_resulting_patch(
    monkeypatch, workspace
):
    server = build_server()
    target = str(workspace / "a.py")

    monkeypatch.setattr(
        server.planner.client,
        "generate_json",
        _plan_sequence(
            [{"tool": "boom", "arguments": {}}],
            [
                {
                    "tool": "create_file",
                    "arguments": {"path": target, "content": "recovered\n"},
                }
            ],
        ),
    )

    response = server.handle_request(
        JsonRpcRequest(
            method="pearl/runAutonomous",
            id=1,
            params={"prompt": "try, fail, recover"},
        )
    )

    assert response.result["stopReason"] == "awaiting_approval"
    assert [s["tool"] for s in response.result["steps"]] == ["boom", "create_file"]
    assert not response.result["steps"][0]["succeeded"]
    assert response.result["steps"][1]["succeeded"]
    assert len(response.result["patches"]) == 1

    final = server.handle_request(
        JsonRpcRequest(method="pearl/approvePatches", id=2, params={})
    )

    assert final.result["stopReason"] == "completed"
    assert (workspace / "a.py").read_text() == "recovered\n"


# ---------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------


def test_run_autonomous_without_planner_is_a_protocol_error():
    registry = build_registry()
    server = MCPServer(registry, dispatcher=ToolDispatcher(registry))

    response = server.handle_request(
        JsonRpcRequest(method="pearl/runAutonomous", id=1, params={"prompt": "do it"})
    )

    assert response.error.code == INVALID_PARAMS


def test_run_autonomous_missing_prompt_is_a_protocol_error():
    server = build_server()

    response = server.handle_request(
        JsonRpcRequest(method="pearl/runAutonomous", id=1, params={})
    )

    assert response.error.code == INVALID_PARAMS


def test_approve_patches_without_a_pending_run_is_a_protocol_error():
    server = build_server()

    response = server.handle_request(
        JsonRpcRequest(method="pearl/approvePatches", id=1, params={})
    )

    assert response.error.code == INVALID_PARAMS


def test_reject_patches_without_a_pending_run_is_a_protocol_error():
    server = build_server()

    response = server.handle_request(
        JsonRpcRequest(method="pearl/rejectPatches", id=1, params={})
    )

    assert response.error.code == INVALID_PARAMS


def test_run_autonomous_rejects_overlapping_runs(monkeypatch, workspace):
    server = build_server()
    target = str(workspace / "a.py")

    monkeypatch.setattr(
        server.planner.client,
        "generate_json",
        _plan_of(
            {
                "tool": "create_file",
                "arguments": {"path": target, "content": "1\n"},
            }
        ),
    )

    server.handle_request(
        JsonRpcRequest(
            method="pearl/runAutonomous", id=1, params={"prompt": "create a.py"}
        )
    )

    response = server.handle_request(
        JsonRpcRequest(
            method="pearl/runAutonomous", id=2, params={"prompt": "create b.py"}
        )
    )

    assert response.error.code == INVALID_PARAMS


# ---------------------------------------------------------------------
# Wire-shape check: exact JSON keys the VS Code client expects
# (src/mcp/patchClient.ts's isExecutionReportResult/isPatchFileSummary)
# ---------------------------------------------------------------------


def test_response_shape_matches_the_vscode_client_contract(monkeypatch, workspace):
    server = build_server()
    target = str(workspace / "a.py")

    monkeypatch.setattr(
        server.planner.client,
        "generate_json",
        _plan_of(
            {
                "tool": "create_file",
                "arguments": {"path": target, "content": "1\n"},
            }
        ),
    )

    response = server.handle_request(
        JsonRpcRequest(
            method="pearl/runAutonomous", id=1, params={"prompt": "create a.py"}
        )
    )

    result = response.result
    assert set(result.keys()) == {
        "stopReason",
        "steps",
        "patches",
        "commands",
        "replansUsed",
    }
    assert isinstance(result["replansUsed"], int)
    assert isinstance(result["stopReason"], str)
    assert isinstance(result["steps"], list)
    assert isinstance(result["patches"], list)
    assert isinstance(result["commands"], list)

    patch = result["patches"][0]
    assert set(patch.keys()) == {"path", "diff", "isNewFile"}
    assert isinstance(patch["path"], str)
    assert isinstance(patch["diff"], str)
    assert isinstance(patch["isNewFile"], bool)
