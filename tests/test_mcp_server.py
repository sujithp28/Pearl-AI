import io
import json

import pytest

from src.agent.dispatcher import ToolDispatcher
from src.agent.planner import Planner
from src.mcp.protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    JsonRpcRequest,
)
from src.mcp.server import MCPServer
from src.memory import Memory
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
    return registry


def build_server(with_planner: bool = False) -> MCPServer:
    registry = build_registry()
    dispatcher = ToolDispatcher(registry)
    memory = Memory()
    planner = Planner(registry, dispatcher) if with_planner else None

    return MCPServer(
        registry, dispatcher=dispatcher, planner=planner, memory=memory
    )


# ---------------------------------------------------------------------
# initialize
# ---------------------------------------------------------------------


def test_initialize_returns_protocol_and_server_info():
    server = build_server()

    response = server.handle_request(
        JsonRpcRequest(method="initialize", id=1)
    )

    assert response.error is None
    assert response.result["serverInfo"]["name"] == "pearl-mcp"
    assert "protocolVersion" in response.result
    assert response.result["capabilities"]["tools"] == {}


# ---------------------------------------------------------------------
# tools/list -- reuses the existing ToolRegistry, no duplication
# ---------------------------------------------------------------------


def test_tools_list_reflects_the_registry_exactly():
    registry = build_registry()
    server = MCPServer(registry)

    response = server.handle_request(
        JsonRpcRequest(method="tools/list", id=1)
    )

    mcp_names = {tool_["name"] for tool_ in response.result["tools"]}
    registry_names = set(registry.list_tools())

    assert mcp_names == registry_names == {"add", "boom"}


def test_tools_list_descriptions_match_registry_metadata():
    registry = build_registry()
    server = MCPServer(registry)

    response = server.handle_request(
        JsonRpcRequest(method="tools/list", id=1)
    )

    by_name = {t["name"]: t for t in response.result["tools"]}

    assert by_name["add"]["description"] == "Add two numbers."
    assert by_name["add"]["inputSchema"]["properties"] == {
        "a": {"type": "integer"},
        "b": {"type": "integer"},
    }


# ---------------------------------------------------------------------
# tools/call
# ---------------------------------------------------------------------


def test_tools_call_executes_via_dispatcher_and_records_memory():
    server = build_server()

    response = server.handle_request(
        JsonRpcRequest(
            method="tools/call",
            id=1,
            params={"name": "add", "arguments": {"a": 1, "b": 2}},
        )
    )

    assert response.error is None
    assert response.result["isError"] is False
    assert response.result["content"][0]["text"] == "3"

    executions = server.memory.execution_history
    assert len(executions) == 1
    assert executions[0].tool_name == "add"
    assert executions[0].result == 3


def test_tools_call_unknown_tool_returns_is_error_content():
    server = build_server()

    response = server.handle_request(
        JsonRpcRequest(
            method="tools/call",
            id=1,
            params={"name": "does_not_exist", "arguments": {}},
        )
    )

    assert response.error is None
    assert response.result["isError"] is True
    assert not server.memory.execution_history[0].succeeded


def test_tools_call_tool_failure_returns_is_error_content():
    server = build_server()

    response = server.handle_request(
        JsonRpcRequest(
            method="tools/call", id=1, params={"name": "boom", "arguments": {}}
        )
    )

    assert response.result["isError"] is True
    assert "kaboom" in response.result["content"][0]["text"]


def test_tools_call_missing_name_is_a_protocol_error():
    server = build_server()

    response = server.handle_request(
        JsonRpcRequest(method="tools/call", id=1, params={})
    )

    assert response.result is None
    assert response.error.code == INVALID_PARAMS


def test_tools_call_non_object_arguments_is_a_protocol_error():
    server = build_server()

    response = server.handle_request(
        JsonRpcRequest(
            method="tools/call",
            id=1,
            params={"name": "add", "arguments": [1, 2]},
        )
    )

    assert response.error.code == INVALID_PARAMS


# ---------------------------------------------------------------------
# Unknown methods / notifications
# ---------------------------------------------------------------------


def test_unknown_method_returns_method_not_found():
    server = build_server()

    response = server.handle_request(
        JsonRpcRequest(method="not/a/real/method", id=1)
    )

    assert response.error.code == METHOD_NOT_FOUND


def test_unknown_notification_returns_none():
    server = build_server()

    response = server.handle_request(
        JsonRpcRequest(method="notifications/unknown")
    )

    assert response is None


def test_internal_error_is_reported_without_crashing():
    server = build_server()
    server.dispatcher = None  # force an AttributeError inside the handler

    response = server.handle_request(
        JsonRpcRequest(
            method="tools/call",
            id=1,
            params={"name": "add", "arguments": {"a": 1, "b": 2}},
        )
    )

    assert response.error.code == INTERNAL_ERROR


# ---------------------------------------------------------------------
# pearl/plan -- reuses the existing Planner
# ---------------------------------------------------------------------


def test_plan_run_without_planner_is_a_protocol_error():
    server = build_server(with_planner=False)

    response = server.handle_request(
        JsonRpcRequest(
            method="pearl/plan", id=1, params={"prompt": "do something"}
        )
    )

    assert response.error.code == INVALID_PARAMS


def test_plan_run_executes_steps_and_updates_memory(monkeypatch):
    server = build_server(with_planner=True)

    monkeypatch.setattr(
        server.planner.client,
        "generate_json",
        lambda prompt: {
            "steps": [
                {"tool": "add", "arguments": {"a": 1, "b": 2}},
                {"tool": "add", "arguments": {"a": 3, "b": 4}},
            ]
        },
    )

    response = server.handle_request(
        JsonRpcRequest(method="pearl/plan", id=1, params={"prompt": "add twice"})
    )

    steps = response.result["steps"]
    assert [s["result"] for s in steps] == [3, 7]

    assert len(server.memory.tasks) == 1
    assert server.memory.tasks[0].status == "completed"


def test_plan_run_marks_task_failed_on_step_error(monkeypatch):
    server = build_server(with_planner=True)

    monkeypatch.setattr(
        server.planner.client,
        "generate_json",
        lambda prompt: {"steps": [{"tool": "boom", "arguments": {}}]},
    )

    response = server.handle_request(
        JsonRpcRequest(
            method="pearl/plan", id=1, params={"prompt": "do something bad"}
        )
    )

    assert response.result["steps"][0]["error"] is not None
    assert server.memory.tasks[0].status == "failed"


def test_plan_run_missing_prompt_is_a_protocol_error():
    server = build_server(with_planner=True)

    response = server.handle_request(
        JsonRpcRequest(method="pearl/plan", id=1, params={})
    )

    assert response.error.code == INVALID_PARAMS


# ---------------------------------------------------------------------
# shutdown
# ---------------------------------------------------------------------


def test_shutdown_returns_null_result():
    server = build_server()

    response = server.handle_request(JsonRpcRequest(method="shutdown", id=1))

    assert response.error is None
    assert response.result is None


# ---------------------------------------------------------------------
# stdio transport (integration)
# ---------------------------------------------------------------------


def _lines(*messages: dict) -> str:
    return "\n".join(json.dumps(m) for m in messages) + "\n"


def test_run_stdio_processes_multiple_requests_and_stops_on_exit():
    server = build_server()

    input_stream = io.StringIO(
        _lines(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "add", "arguments": {"a": 2, "b": 5}},
            },
            {"jsonrpc": "2.0", "method": "exit"},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/list"},
        )
    )
    output_stream = io.StringIO()

    server.run_stdio(input_stream, output_stream)

    responses = [
        json.loads(line)
        for line in output_stream.getvalue().splitlines()
        if line.strip()
    ]

    assert [r["id"] for r in responses] == [1, 2, 3]
    assert responses[2]["result"]["content"][0]["text"] == "7"


def test_run_stdio_skips_blank_lines():
    server = build_server()

    input_stream = io.StringIO(
        "\n\n" + _lines({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    )
    output_stream = io.StringIO()

    server.run_stdio(input_stream, output_stream)

    responses = [
        json.loads(line)
        for line in output_stream.getvalue().splitlines()
        if line.strip()
    ]

    assert len(responses) == 1


def test_run_stdio_reports_parse_errors_and_continues():
    server = build_server()

    input_stream = io.StringIO(
        "not valid json\n"
        + _lines({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    )
    output_stream = io.StringIO()

    server.run_stdio(input_stream, output_stream)

    responses = [
        json.loads(line)
        for line in output_stream.getvalue().splitlines()
        if line.strip()
    ]

    assert responses[0]["error"]["code"] == -32700
    assert responses[1]["id"] == 1


def test_notification_gets_no_response_over_stdio():
    server = build_server()

    input_stream = io.StringIO(
        _lines({"jsonrpc": "2.0", "method": "notifications/initialized"})
    )
    output_stream = io.StringIO()

    server.run_stdio(input_stream, output_stream)

    assert output_stream.getvalue() == ""
