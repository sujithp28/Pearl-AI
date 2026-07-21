from typing import Any

from src.tools.registry import ToolRegistry


class ToolDispatcher:
    """
    Executes registered tools by name.
    """

    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    def execute(
        self,
        tool_name: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        Execute a registered tool.

        Args:
            tool_name: Name of the registered tool.
            *args: Positional arguments.
            **kwargs: Keyword arguments.

        Returns:
            Result returned by the tool.

        Raises:
            ValueError:
                If the tool does not exist.
        """

        tool = self.registry.get(tool_name)

        if tool is None:
            raise ValueError(
                f"Tool '{tool_name}' is not registered."
            )

        return tool(*args, **kwargs)