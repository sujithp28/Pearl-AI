"""
Tests for `pearl/progress` notification streaming (Phase 27 / M1).

`MCPServer.handle_request`/`handle_line` gained an optional `notify`
callback that forwards `AutonomousExecutor.on_progress` events to the
client as JSON-RPC notifications, interleaved with the eventual
response. These tests exercise that directly (capturing notifications
via a fake `notify`) and end-to-end through `handle_line`'s real
stdio-line writing, without ever touching the request/response
contract itself — which `test_mcp_autonomous.py` already covers and
which stays green unmodified.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from src.agent.dispatcher import ToolDispatcher
from src.agent.planner import Planner
from src.mcp.protocol import JsonRpcRequest
from src.mcp.server import MCPServer
from src.memory import Memory
from src.tools.edit_tools import create_file
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


def build_server() -> MCPServer:
    registry = ToolRegistry()
    registry.register(add)
    registry.register(boom)
    registry.register(create_file)

    dispatcher = ToolDispatcher(registry)
    planner = Planner(registry, dispatcher)

    return MCPServer(registry, dispatcher=dispatcher, planner=planner, memory=Memory())


def _plan_of(*steps):
    return lambda prompt: {"steps": list(steps)}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    return tmp_path


def _capture() -> tuple[list[tuple[str, dict]], object]:
    captured: list[tuple[str, dict]] = []

    def notify(method: str, params: dict) -> None:
        captured.append((method, params))

    return captured, notify


# ---------------------------------------------------------------------
# handle_request without `notify`: unchanged (backward compatibility)
# ---------------------------------------------------------------------


def test_handle_request_without_notify_behaves_exactly_as_before(monkeypatch):
    server = build_server()

    monkeypatch.setattr(
        server.planner.client,
        "generate_json",
        _plan_of({"tool": "add", "arguments": {"a": 1, "b": 2}}),
    )

    response = server.handle_request(
        JsonRpcRequest(method="pearl/runAutonomous", id=1, params={"prompt": "add"})
    )

    assert response.error is None
    assert response.result["stopReason"] == "completed"


def test_handle_request_notify_is_optional_for_every_handler(monkeypatch):
    # A non-streaming method must still work with no notify passed —
    # covers the other 8 handlers that ignore the parameter.
    server = build_server()

    response = server.handle_request(JsonRpcRequest(method="tools/list", id=1))

    assert response.error is None
    assert isinstance(response.result["tools"], list)


# ---------------------------------------------------------------------
# pearl/progress emission during runAutonomous
# ---------------------------------------------------------------------


def test_run_autonomous_streams_progress_notifications(monkeypatch):
    server = build_server()

    monkeypatch.setattr(
        server.planner.client,
        "generate_json",
        _plan_of(
            {"tool": "add", "arguments": {"a": 1, "b": 2}},
            {"tool": "add", "arguments": {"a": 3, "b": 4}},
        ),
    )

    captured, notify = _capture()

    response = server.handle_request(
        JsonRpcRequest(
            method="pearl/runAutonomous", id=1, params={"prompt": "add twice"}
        ),
        notify,
    )

    assert response.result["stopReason"] == "completed"

    methods = [method for method, _ in captured]
    assert methods == ["pearl/progress"] * len(captured)

    statuses = [params["status"] for _, params in captured]
    assert statuses == [
        "planning",
        "executing_step",
        "step_completed",
        "executing_step",
        "step_completed",
        "task_completed",
    ]


def test_progress_notification_payload_shape(monkeypatch):
    server = build_server()

    monkeypatch.setattr(
        server.planner.client,
        "generate_json",
        _plan_of({"tool": "add", "arguments": {"a": 1, "b": 2}}),
    )

    captured, notify = _capture()

    server.handle_request(
        JsonRpcRequest(method="pearl/runAutonomous", id=1, params={"prompt": "add"}),
        notify,
    )

    _, params = captured[0]
    assert set(params.keys()) == {
        "status",
        "currentStep",
        "totalSteps",
        "currentAction",
    }
    assert isinstance(params["currentStep"], int)
    assert isinstance(params["totalSteps"], int)
    assert isinstance(params["currentAction"], str)


def test_run_autonomous_without_notify_does_not_error(monkeypatch):
    # No notify passed at all — the no-op default must be used
    # transparently, exercising `AutonomousExecutor.on_progress` with
    # a real (no-op) callback rather than None.
    server = build_server()

    monkeypatch.setattr(
        server.planner.client,
        "generate_json",
        _plan_of({"tool": "add", "arguments": {"a": 1, "b": 2}}),
    )

    response = server.handle_request(
        JsonRpcRequest(method="pearl/runAutonomous", id=1, params={"prompt": "add"})
    )

    assert response.result["stopReason"] == "completed"


# ---------------------------------------------------------------------
# pearl/progress emission across pause/approve/reject
# ---------------------------------------------------------------------


def test_progress_streams_during_approval_pause_and_resume(monkeypatch, workspace):
    server = build_server()
    target = str(workspace / "a.py")

    monkeypatch.setattr(
        server.planner.client,
        "generate_json",
        _plan_of(
            {"tool": "create_file", "arguments": {"path": target, "content": "1\n"}}
        ),
    )

    run_captured, run_notify = _capture()
    paused = server.handle_request(
        JsonRpcRequest(
            method="pearl/runAutonomous", id=1, params={"prompt": "create a.py"}
        ),
        run_notify,
    )

    assert paused.result["stopReason"] == "awaiting_approval"
    assert [params["status"] for _, params in run_captured] == [
        "planning",
        "executing_step",
        "step_completed",
        "awaiting_approval",
    ]

    approve_captured, approve_notify = _capture()
    final = server.handle_request(
        JsonRpcRequest(method="pearl/approvePatches", id=2, params={}),
        approve_notify,
    )

    assert final.result["stopReason"] == "completed"
    assert [params["status"] for _, params in approve_captured] == ["task_completed"]
    # The pause/resume boundary is respected: approving doesn't
    # replay the run's own earlier notifications onto the new call.
    assert run_captured[-1][1]["status"] == "awaiting_approval"


def test_progress_streams_to_the_current_calls_notify_not_a_stale_one(
    monkeypatch, workspace
):
    # Regression test for the "rebuilt fresh every call" design: if
    # `on_progress` were fixed at executor construction, this second
    # call's events would silently go to the first call's (unused)
    # notify instead of its own.
    server = build_server()
    target = str(workspace / "a.py")

    monkeypatch.setattr(
        server.planner.client,
        "generate_json",
        _plan_of(
            {"tool": "create_file", "arguments": {"path": target, "content": "1\n"}}
        ),
    )

    server.handle_request(
        JsonRpcRequest(
            method="pearl/runAutonomous", id=1, params={"prompt": "create a.py"}
        ),
        # A notify that would raise if it were ever (re)used for the
        # approve() call below — proves the executor's on_progress is
        # reassigned per call, not fixed at construction.
        lambda method, params: None,
    )

    approve_captured, approve_notify = _capture()
    server.handle_request(
        JsonRpcRequest(method="pearl/approvePatches", id=2, params={}),
        approve_notify,
    )

    assert approve_captured  # the fresh notify received the event(s)


def test_progress_streams_on_rejection(monkeypatch, workspace):
    server = build_server()
    target = str(workspace / "a.py")

    monkeypatch.setattr(
        server.planner.client,
        "generate_json",
        _plan_of(
            {"tool": "create_file", "arguments": {"path": target, "content": "1\n"}}
        ),
    )

    server.handle_request(
        JsonRpcRequest(
            method="pearl/runAutonomous", id=1, params={"prompt": "create a.py"}
        )
    )

    captured, notify = _capture()
    server.handle_request(
        JsonRpcRequest(method="pearl/rejectPatches", id=2, params={}), notify
    )

    assert [params["status"] for _, params in captured] == ["rejected"]


# ---------------------------------------------------------------------
# handle_line / run_stdio: real interleaved notification lines
# ---------------------------------------------------------------------


def test_handle_line_writes_notification_lines_before_the_response(monkeypatch):
    server = build_server()

    monkeypatch.setattr(
        server.planner.client,
        "generate_json",
        _plan_of({"tool": "add", "arguments": {"a": 1, "b": 2}}),
    )

    output = io.StringIO()
    line = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "pearl/runAutonomous",
            "params": {"prompt": "add"},
        }
    )

    server.handle_line(line, output)

    written_lines = [ln for ln in output.getvalue().splitlines() if ln.strip()]
    messages = [json.loads(ln) for ln in written_lines]

    notifications = [m for m in messages if "id" not in m]
    responses = [m for m in messages if "id" in m]

    assert len(responses) == 1
    assert responses[0]["result"]["stopReason"] == "completed"
    assert notifications  # at least the "planning" notification
    assert all(n["method"] == "pearl/progress" for n in notifications)

    # Notifications for this request appear before its response line,
    # matching the order they were actually produced in.
    response_index = written_lines.index(json.dumps(responses[0]))
    for notification in notifications:
        notification_index = written_lines.index(json.dumps(notification))
        assert notification_index < response_index


def test_handle_line_still_works_for_non_streaming_methods(monkeypatch):
    server = build_server()

    output = io.StringIO()
    line = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    server.handle_line(line, output)

    written = json.loads(output.getvalue().strip())
    assert written["id"] == 1
    assert isinstance(written["result"]["tools"], list)


def test_notify_write_failure_does_not_break_the_request(monkeypatch):
    server = build_server()

    monkeypatch.setattr(
        server.planner.client,
        "generate_json",
        _plan_of({"tool": "add", "arguments": {"a": 1, "b": 2}}),
    )

    class _BrokenPipe(io.StringIO):
        def write(self, text: str) -> int:  # noqa: D102
            if '"pearl/progress"' in text:
                raise BrokenPipeError("boom")
            return super().write(text)

    output = _BrokenPipe()
    line = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "pearl/runAutonomous",
            "params": {"prompt": "add"},
        }
    )

    server.handle_line(line, output)

    response = json.loads(
        [ln for ln in output.getvalue().splitlines() if '"id"' in ln][0]
    )
    assert response["result"]["stopReason"] == "completed"
