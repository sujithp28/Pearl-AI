"""
Shared validation for LLM-produced tool calls.
"""

from __future__ import annotations

import inspect

from src.llm.parser import ToolCall
from src.tools.registry import ToolRegistry

_POSITIONAL_KINDS = frozenset(
    (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    )
)


def _required_params(func) -> frozenset[str]:
    """Return the names of parameters that have no default value."""
    sig = inspect.signature(func)
    return frozenset(
        name
        for name, param in sig.parameters.items()
        if param.kind in _POSITIONAL_KINDS
        and param.default is inspect.Parameter.empty
    )


def validate_tool_call(tool_call: ToolCall, registry: ToolRegistry) -> None:
    """
    Validate a tool call's name and arguments against the registry.

    The special "none" tool name (no suitable tool) is always valid.

    Raises
    ------
    ValueError
        If the tool is not registered, if it receives an argument it
        does not declare, or if it is missing a required argument.
    """

    if tool_call.tool_name == "none":
        return

    if not registry.has_tool(tool_call.tool_name):
        raise ValueError(f"Unknown tool: {tool_call.tool_name}")

    tool = registry.get_tool(tool_call.tool_name)
    allowed = set(tool.parameters.keys())
    provided = set(tool_call.kwargs.keys())

    unexpected = provided - allowed
    if unexpected:
        raise ValueError(
            f"Tool '{tool_call.tool_name}' received unexpected "
            f"arguments: {sorted(unexpected)}"
        )

    required = _required_params(tool.function)
    missing = required - provided
    if missing:
        raise ValueError(
            f"Tool '{tool_call.tool_name}' missing required "
            f"argument(s): {sorted(missing)}"
        )
