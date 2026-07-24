"""
MCP (Model Context Protocol) package.

Exposes Pearl's tool registry, dispatcher, planner, and memory over
the MCP JSON-RPC protocol.
"""

from .protocol import (
    JsonRpcError,
    JsonRpcRequest,
    JsonRpcResponse,
    MCPProtocolError,
    parse_request,
    tool_to_mcp_schema,
)
from .server import MCPServer

__all__ = [
    "MCPServer",
    "JsonRpcRequest",
    "JsonRpcResponse",
    "JsonRpcError",
    "MCPProtocolError",
    "parse_request",
    "tool_to_mcp_schema",
]
