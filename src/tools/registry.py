from typing import Callable


class ToolRegistry:
    def __init__(self):
        self.tools: dict[str, Callable] = {}

    def register(self, name: str, func: Callable):
        if name in self.tools:
            raise ValueError(f"Tool '{name}' already exists.")

        self.tools[name] = func

    def get(self, name: str):
        return self.tools.get(name)

    def list(self):
        return list(self.tools.keys())


registry = ToolRegistry()