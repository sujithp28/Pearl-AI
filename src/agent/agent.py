"""
Pearl Agent.

Main entry point for the AI Coding Agent.
"""

from __future__ import annotations

import logging
from typing import Any

from src.agent.dispatcher import ToolDispatcher
from src.llm.client import LLMClient
from src.llm.tool_selector import LLMToolSelector
from src.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class PearlAgent:
    """
    Main Pearl AI Coding Agent.

    Responsibilities
    ----------------
    - Receive user requests.
    - Ask the LLM which tool should be executed.
    - Execute the selected tool.
    - Return the tool result.

    Future versions will extend this workflow with:
    - Memory
    - Planning
    - Multi-tool execution
    - Reflection
    """

    def __init__(
        self,
        registry: ToolRegistry,
    ) -> None:

        self.registry = registry

        self.dispatcher = ToolDispatcher(registry)

        self.selector = LLMToolSelector(registry)

        self.llm = LLMClient()

        logger.info("Pearl Agent initialized.")

    def execute_tool(
        self,
        tool_name: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        Execute a tool directly.
        """

        logger.info("Executing tool: %s", tool_name)

        return self.dispatcher.execute(
            tool_name,
            *args,
            **kwargs,
        )

    def run(
        self,
        prompt: str,
    ) -> Any:
        """
        Execute a complete user request.

        Workflow

            User
              │
              ▼
        LLM Tool Selector
              │
              ▼
          ToolCall
              │
              ▼
         Tool Dispatcher
              │
              ▼
        Tool Execution
              │
              ▼
            Result
        """

        logger.info("User Prompt: %s", prompt)

        tool_call = self.selector.select(prompt)

        logger.info(
            "Selected Tool: %s",
            tool_call.tool_name,
        )

        # Future fallback when no tool is appropriate.
        if tool_call.tool_name == "none":
            logger.info("No suitable tool selected.")
            return "No suitable tool found for this request."

        result = self.dispatcher.execute(
            tool_call.tool_name,
            *tool_call.args,
            **tool_call.kwargs,
        )

        logger.info("Tool executed successfully.")

        return result

    def chat(
        self,
        prompt: str,
    ) -> str:
        """
        Chat directly with the language model.

        This bypasses tool execution and is intended for
        conversational use.

        Future versions will integrate reasoning,
        memory, planning, and tool execution into
        a unified conversation pipeline.
        """

        logger.info("Chat request received.")

        return self.llm.generate(prompt)

    def available_tools(self) -> list[dict[str, Any]]:
        """
        Return metadata describing all registered tools.
        """

        return self.registry.get_tools()