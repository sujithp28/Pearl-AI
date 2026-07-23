from typing import Any

from src.tools.models import Tool
from src.tools.registry import ToolRegistry


class ToolDispatcher:
    """
    Dispatches tool execution requests.
    """

    def __init__(self, registry: ToolRegistry):
        self._registry = registry

    def execute(
        self,
        tool_name: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        Execute a registered tool.
        """
        tool = self._registry.get_tool(tool_name)

        if tool is None:
            raise ValueError(f"Unknown tool: {tool_name}")

        return tool.function(*args, **kwargs)

    def has_tool(self, tool_name: str) -> bool:
        """
        Check whether a tool exists.
        """
        return self._registry.get_tool(tool_name) is not None

    def list_tools(self) -> list[str]:
        """
        List all available tools.
        """
        return self._registry.list()

    def describe_tool(self, tool_name: str) -> Tool:
        """
        Return metadata for a tool.
        """
        tool = self._registry.get_tool(tool_name)

        if tool is None:
            raise ValueError(f"Unknown tool: {tool_name}")

        return tool