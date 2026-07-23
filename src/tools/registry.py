from src.tools.models import Tool


class ToolRegistry:

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, func):
        tool = Tool(
            name=func._tool_name,
            description=func._tool_description,
            function=func,
        )

        self._tools[tool.name] = tool

    def get(self, name: str):
        tool = self._tools.get(name)
        return tool.function if tool else None

    def get_tool(self, name: str):
        return self._tools.get(name)

    def list(self):
        return sorted(self._tools.keys())