"""
Tool metadata decorator.

This decorator attaches metadata to functions so they can be
registered automatically by the ToolRegistry.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def tool(
    description: str,
    parameters: dict[str, str] | None = None,
    returns: str = "Any",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Decorator used to register metadata on a tool function.

    Example:
        @tool(
            description="Read a UTF-8 text file.",
            parameters={"path": "str"},
            returns="str",
        )
        def read_file(path: str) -> str:
            ...
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        func._tool_name = func.__name__
        func._tool_description = description
        func._tool_parameters = parameters or {}
        func._tool_returns = returns
        return func

    return decorator
