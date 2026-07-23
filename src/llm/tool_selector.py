"""
LLM Tool Selector.

Uses the language model to decide which tool should be executed.
"""

from __future__ import annotations

import json
import logging

from src.llm.client import LLMClient
from src.llm.parser import ToolCall, ToolParser
from src.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class LLMToolSelector:
    """
    Uses the LLM to choose the correct tool.
    """

    PROMPT_FILE = "src/prompts/tool_selection.txt"

    def __init__(
        self,
        registry: ToolRegistry,
    ) -> None:
        self.registry = registry
        self.client = LLMClient()
        self.parser = ToolParser()

    def build_prompt(
        self,
        user_prompt: str,
    ) -> str:
        """
        Build the prompt for the LLM using the external prompt template.
        """

        prompt_template = self.client.load_prompt(
            self.PROMPT_FILE
        )

        tools = json.dumps(
            self.registry.get_tools(),
            indent=4,
        )

        return prompt_template.format(
            tools=tools,
            user_prompt=user_prompt,
        )

    def select(
        self,
        user_prompt: str,
    ) -> ToolCall:
        """
        Select the most appropriate tool using the language model.
        """

        logger.info("Selecting tool for request: %s", user_prompt)

        prompt = self.build_prompt(user_prompt)

        # generate_json() returns a Python dictionary
        payload = self.client.generate_json(prompt)

        logger.debug("Tool payload: %s", payload)

        # ToolParser.parse() currently expects a JSON string.
        # Convert the dictionary back to JSON until the parser
        # is updated to accept dictionaries directly.
        response = json.dumps(payload)

        tool_call = self.parser.parse(response)

        self._validate_arguments(tool_call)

        logger.info("Selected tool: %s", tool_call.tool_name)

        return tool_call

    def _validate_arguments(self, tool_call: ToolCall) -> None:
        """
        Validate a tool call's arguments against the tool's declared
        parameters before it reaches execution.
        """

        if tool_call.tool_name == "none":
            return

        if not self.registry.has_tool(tool_call.tool_name):
            raise ValueError(
                f"LLM selected an unknown tool: {tool_call.tool_name}"
            )

        tool = self.registry.get_tool(tool_call.tool_name)
        allowed = set(tool.parameters.keys())
        unexpected = set(tool_call.kwargs.keys()) - allowed

        if unexpected:
            raise ValueError(
                f"Tool '{tool_call.tool_name}' received unexpected "
                f"arguments: {sorted(unexpected)}"
            )