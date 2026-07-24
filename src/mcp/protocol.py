"""
MCP (Model Context Protocol) JSON-RPC message types.

A minimal, dependency-free JSON-RPC 2.0 layer for the MCP methods
Pearl's server implements (`initialize`, `tools/list`, `tools/call`,
plus a small Pearl-specific extension for planning).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.tools.models import Tool

JSONRPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2024-11-05"

# Standard JSON-RPC 2.0 error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

_JSON_SCHEMA_TYPES = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "dict": "object",
    "list": "array",
}


class MCPProtocolError(Exception):
    """
    Raised for malformed JSON-RPC input or invalid parameters.
    """

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(slots=True)
class JsonRpcRequest:
    """
    A parsed JSON-RPC request (or notification, if `id` is None).
    """

    method: str
    id: Any = None
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def is_notification(self) -> bool:
        """
        Return whether this request expects no response.
        """

        return self.id is None


@dataclass(slots=True)
class JsonRpcError:
    """
    A JSON-RPC error object.
    """

    code: int
    message: str
    data: Any = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }

        if self.data is not None:
            payload["data"] = self.data

        return payload


@dataclass(slots=True)
class JsonRpcResponse:
    """
    A JSON-RPC response: either a result or an error, never both.
    """

    id: Any
    result: Any = None
    error: JsonRpcError | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jsonrpc": JSONRPC_VERSION,
            "id": self.id,
        }

        if self.error is not None:
            payload["error"] = self.error.to_dict()
        else:
            payload["result"] = self.result

        return payload

    def to_json(self) -> str:
        """
        Serialize this response as a single JSON line.
        """

        return json.dumps(self.to_dict())


def parse_request(raw: str) -> JsonRpcRequest:
    """
    Parse a single JSON-RPC request line.

    Raises
    ------
    MCPProtocolError
        If `raw` is not valid JSON-RPC 2.0.
    """

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MCPProtocolError(PARSE_ERROR, "Invalid JSON.") from exc

    if not isinstance(payload, dict):
        raise MCPProtocolError(
            INVALID_REQUEST, "Request must be a JSON object."
        )

    if payload.get("jsonrpc") != JSONRPC_VERSION:
        raise MCPProtocolError(
            INVALID_REQUEST, "Missing or invalid 'jsonrpc' version."
        )

    method = payload.get("method")

    if not isinstance(method, str) or not method:
        raise MCPProtocolError(
            INVALID_REQUEST, "Missing or invalid 'method'."
        )

    params = payload.get("params")

    if params is None:
        params = {}

    if not isinstance(params, dict):
        raise MCPProtocolError(INVALID_PARAMS, "'params' must be an object.")

    return JsonRpcRequest(
        method=method,
        id=payload.get("id"),
        params=params,
    )


def _json_schema_type(type_hint: str) -> dict[str, Any]:
    """
    Map one of Pearl's simple parameter type strings to a JSON
    Schema type fragment.
    """

    base = type_hint.strip().split("[")[0].split("|")[0].strip()

    return {"type": _JSON_SCHEMA_TYPES.get(base, "string")}


def tool_to_mcp_schema(tool: "Tool") -> dict[str, Any]:
    """
    Convert a Pearl `Tool` into an MCP tool descriptor.

    This reads the tool's existing metadata; it does not redefine
    or duplicate the tool itself.
    """

    properties = {
        name: _json_schema_type(type_hint)
        for name, type_hint in tool.parameters.items()
    }

    return {
        "name": tool.name,
        "description": tool.description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": sorted(properties.keys()),
        },
    }
