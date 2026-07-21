from src.tools.models import Tool


class ToolRegistry:
    """
    Registry for all available tools.
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(
        self,
        name: str,
        function,
        description: str = "",
    ) -> None:
        self._tools[name] = Tool(
            name=name,
            description=description,
            function=function,
        )

    def get(self, name: str):
        tool = self._tools.get(name)
        return tool.function if tool else None

    def get_tool(self, name: str):
        return self._tools.get(name)

    def list(self):
        return sorted(self._tools.keys())