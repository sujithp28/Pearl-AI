import logging
from typing import Any

from src.tools.models import Tool
from src.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class ToolNotFoundError(ValueError):
    """
    Raised when a requested tool is not registered.
    """


class ToolExecutionError(RuntimeError):
    """
    Raised when a registered tool raises during execution.

    Wraps the original exception with the tool name for context.
    """

    def __init__(self, tool_name: str, original: Exception) -> None:
        self.tool_name = tool_name
        self.original = original
        super().__init__(f"Tool '{tool_name}' failed: {original}")


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
        logger.info("Dispatching tool: %s", tool_name)

        try:
            tool = self._registry.get_tool(tool_name)
        except ValueError as exc:
            logger.error("Tool not found: %s", tool_name)
            raise ToolNotFoundError(f"Unknown tool: {tool_name}") from exc

        try:
            result = tool.function(*args, **kwargs)
        except Exception as exc:
            logger.exception("Tool '%s' raised during execution", tool_name)
            raise ToolExecutionError(tool_name, exc) from exc

        logger.info("Tool '%s' executed successfully", tool_name)

        return result

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
        try:
            return self._registry.get_tool(tool_name)
        except ValueError as exc:
            logger.error("Tool not found: %s", tool_name)
            raise ToolNotFoundError(f"Unknown tool: {tool_name}") from exc
