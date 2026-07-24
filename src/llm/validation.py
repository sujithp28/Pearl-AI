"""
Shared validation for LLM-produced tool calls.
"""

from __future__ import annotations

from src.llm.parser import ToolCall
from src.tools.registry import ToolRegistry


def validate_tool_call(tool_call: ToolCall, registry: ToolRegistry) -> None:
    """
    Validate a tool call's name and arguments against the registry.

    The special "none" tool name (no suitable tool) is always valid.

    Raises
    ------
    ValueError
        If the tool is not registered, or if it receives an argument
        it does not declare.
    """

    if tool_call.tool_name == "none":
        return

    if not registry.has_tool(tool_call.tool_name):
        raise ValueError(f"Unknown tool: {tool_call.tool_name}")

    tool = registry.get_tool(tool_call.tool_name)
    allowed = set(tool.parameters.keys())
    unexpected = set(tool_call.kwargs.keys()) - allowed

    if unexpected:
        raise ValueError(
            f"Tool '{tool_call.tool_name}' received unexpected "
            f"arguments: {sorted(unexpected)}"
        )
