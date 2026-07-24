import json

import pytest

from src.mcp.protocol import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    PARSE_ERROR,
    JsonRpcError,
    JsonRpcResponse,
    MCPProtocolError,
    parse_request,
    tool_to_mcp_schema,
)
from src.tools.metadata import tool
from src.tools.models import Tool


@tool(
    description="Read a file.",
    parameters={"path": "str", "limit": "int"},
    returns="str",
)
def read_file(path: str, limit: int) -> str:
    return path


# ---------------------------------------------------------------------
# parse_request
# ---------------------------------------------------------------------


def test_parse_request_valid_call():
    request = parse_request(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            }
        )
    )

    assert request.method == "tools/list"
    assert request.id == 1
    assert request.params == {}
    assert not request.is_notification


def test_parse_request_notification_has_no_id():
    request = parse_request(
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
    )

    assert request.id is None
    assert request.is_notification


def test_parse_request_missing_params_defaults_to_empty_dict():
    request = parse_request(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    )

    assert request.params == {}


def test_parse_request_rejects_invalid_json():
    with pytest.raises(MCPProtocolError) as exc_info:
        parse_request("not json")

    assert exc_info.value.code == PARSE_ERROR


def test_parse_request_rejects_non_object_payload():
    with pytest.raises(MCPProtocolError) as exc_info:
        parse_request(json.dumps([1, 2, 3]))

    assert exc_info.value.code == INVALID_REQUEST


def test_parse_request_rejects_wrong_jsonrpc_version():
    with pytest.raises(MCPProtocolError) as exc_info:
        parse_request(json.dumps({"jsonrpc": "1.0", "id": 1, "method": "initialize"}))

    assert exc_info.value.code == INVALID_REQUEST


def test_parse_request_rejects_missing_method():
    with pytest.raises(MCPProtocolError) as exc_info:
        parse_request(json.dumps({"jsonrpc": "2.0", "id": 1}))

    assert exc_info.value.code == INVALID_REQUEST


def test_parse_request_rejects_non_object_params():
    with pytest.raises(MCPProtocolError) as exc_info:
        parse_request(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": [1, 2],
                }
            )
        )

    assert exc_info.value.code == INVALID_PARAMS


# ---------------------------------------------------------------------
# JsonRpcResponse
# ---------------------------------------------------------------------


def test_response_to_dict_with_result():
    response = JsonRpcResponse(id=1, result={"ok": True})

    assert response.to_dict() == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"ok": True},
    }


def test_response_to_dict_with_error():
    response = JsonRpcResponse(
        id=1, error=JsonRpcError(code=-32601, message="not found")
    )

    payload = response.to_dict()

    assert payload["error"] == {"code": -32601, "message": "not found"}
    assert "result" not in payload


def test_response_to_json_round_trips():
    response = JsonRpcResponse(id=2, result="hello")

    assert json.loads(response.to_json()) == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": "hello",
    }


# ---------------------------------------------------------------------
# tool_to_mcp_schema
# ---------------------------------------------------------------------


def test_tool_to_mcp_schema_maps_types_and_metadata():
    registry_tool = Tool(
        name="read_file",
        description="Read a file.",
        function=read_file,
        parameters={"path": "str", "limit": "int"},
        returns="str",
    )

    schema = tool_to_mcp_schema(registry_tool)

    assert schema["name"] == "read_file"
    assert schema["description"] == "Read a file."
    assert schema["inputSchema"]["type"] == "object"
    assert schema["inputSchema"]["properties"] == {
        "path": {"type": "string"},
        "limit": {"type": "integer"},
    }
    assert sorted(schema["inputSchema"]["required"]) == ["limit", "path"]


def test_tool_to_mcp_schema_defaults_unknown_types_to_string():
    registry_tool = Tool(
        name="ls",
        description="List a directory.",
        function=read_file,
        parameters={"path": "list[str]", "show_hidden": "bool"},
        returns="list[str]",
    )

    schema = tool_to_mcp_schema(registry_tool)

    assert schema["inputSchema"]["properties"]["path"] == {"type": "array"}
    assert schema["inputSchema"]["properties"]["show_hidden"] == {"type": "boolean"}


def test_tool_to_mcp_schema_handles_no_parameters():
    registry_tool = Tool(
        name="pwd",
        description="Return the working directory.",
        function=read_file,
        parameters={},
        returns="str",
    )

    schema = tool_to_mcp_schema(registry_tool)

    assert schema["inputSchema"]["properties"] == {}
    assert schema["inputSchema"]["required"] == []
