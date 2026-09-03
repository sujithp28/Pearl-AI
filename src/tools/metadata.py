"""
Tool metadata decorator.

This decorator attaches metadata to functions so they can be
registered automatically by the ToolRegistry.

V2 adds a ``risk_level`` parameter for tiered approval:

  "safe"       — read-only, can execute without approval in any mode
  "staged"     — write, subject to PatchManager approval gate
  "dangerous"  — destructive/irreversible, always requires confirmation

Default: "staged" (fail-safe — an unclassified tool is treated as
         requiring approval rather than running freely).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

RiskLevel = Literal["safe", "staged", "dangerous"]


def tool(
    description: str,
    parameters: dict[str, str] | None = None,
    returns: str = "Any",
    risk_level: RiskLevel = "staged",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """
    Decorator used to register metadata on a tool function.

    Example:
        @tool(
            description="Read a UTF-8 text file.",
            parameters={"path": "str"},
            returns="str",
            risk_level="safe",
        )
        def read_file(path: str) -> str:
            ...
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        func._tool_name = func.__name__
        func._tool_description = description
        func._tool_parameters = parameters or {}
        func._tool_returns = returns
        func._tool_risk_level = risk_level
        return func

    return decorator
