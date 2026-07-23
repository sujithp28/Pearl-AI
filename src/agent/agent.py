from src.agent.dispatcher import ToolDispatcher
from src.agent.tool_selector import ToolSelector
from src.tools.registry import ToolRegistry


class PearlAgent:
    """
    Main Pearl agent.
    """

    def __init__(self, registry: ToolRegistry):
        self.dispatcher = ToolDispatcher(registry)
        self.selector = ToolSelector()

    def run(self, prompt: str):
        """
        Execute a user request.
        """

        tool_call = self.selector.select(prompt)

        if tool_call is None:
            return "I don't know how to handle that request yet."

        return self.dispatcher.execute(
            tool_call.tool_name,
            *tool_call.args,
            **tool_call.kwargs,
        )