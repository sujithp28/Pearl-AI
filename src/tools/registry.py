"""
Tool registry.

Responsible for registering, validating, discovering,
and retrieving tools available to Pearl.
"""

from __future__ import annotations

import logging
from typing import Any

from src.tools.models import Tool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """
    Stores every tool available to Pearl.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, func) -> None:
        """
        Register a decorated tool.

        Example:
            registry.register(read_file)
        """

        if not hasattr(func, "_tool_name"):
            raise TypeError(
                f"{func.__name__} is not decorated with @tool."
            )

        tool = Tool(
            name=func._tool_name,
            description=func._tool_description,
            function=func,
            parameters=func._tool_parameters,
            returns=func._tool_returns,
        )

        if tool.name in self._tools:
            raise ValueError(
                f"Tool '{tool.name}' already registered."
            )

        self._tools[tool.name] = tool

        logger.info("Registered tool '%s'", tool.name)

    def unregister(self, tool_name: str) -> None:
        """
        Remove a tool from the registry.
        """

        if tool_name not in self._tools:
            raise KeyError(tool_name)

        del self._tools[tool_name]

        logger.info("Unregistered tool '%s'", tool_name)

    def get_tool(self, tool_name: str) -> Tool:
        """
        Return a Tool object.
        """

        try:
            return self._tools[tool_name]
        except KeyError as exc:
            raise ValueError(
                f"Unknown tool: {tool_name}"
            ) from exc

    def has_tool(self, tool_name: str) -> bool:
        """
        Return True if the tool exists.
        """

        return tool_name in self._tools

    def list_tools(self) -> list[str]:
        """
        Return all registered tool names.
        """

        return sorted(self._tools.keys())

    def get_tools(self) -> list[dict[str, Any]]:
        """
        Return tool metadata for the LLM.
        """

        return [
            tool.to_dict()
            for tool in self._tools.values()
        ]

    def describe_tool(self, tool_name: str) -> dict[str, Any]:
        """
        Return metadata for one tool.
        """

        return self.get_tool(tool_name).to_dict()

    def clear(self) -> None:
        """
        Remove every registered tool.
        """

        self._tools.clear()

        logger.info("Registry cleared.")

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, tool_name: str) -> bool:
        return tool_name in self._tools

    def __iter__(self):
        return iter(self._tools.values())