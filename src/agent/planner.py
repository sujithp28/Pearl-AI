"""
Planning engine for Pearl.

Breaks a complex user request into an ordered sequence of tool
calls and executes them one after another via the ToolDispatcher.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from src.agent.dispatcher import ToolDispatcher
from src.llm.client import LLMClient
from src.llm.parser import ToolCall, ToolParser
from src.llm.validation import validate_tool_call
from src.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class StepResult:
    """
    Outcome of executing a single plan step.
    """

    tool_name: str
    kwargs: dict[str, Any]
    result: Any = None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        """
        Return whether the step completed without error.
        """

        return self.error is None


class Planner:
    """
    Breaks a complex user request into multiple tool calls and
    executes them sequentially.
    """

    PROMPT_FILE = "src/prompts/planning.txt"

    def __init__(
        self,
        registry: ToolRegistry,
        dispatcher: ToolDispatcher,
        client: LLMClient | None = None,
    ) -> None:
        self.registry = registry
        self.dispatcher = dispatcher
        self.client = client or LLMClient()
        self.parser = ToolParser()

    def build_prompt(self, user_prompt: str) -> str:
        """
        Build the planning prompt from the external prompt template.
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

    def plan(self, user_prompt: str) -> list[ToolCall]:
        """
        Ask the LLM to break `user_prompt` into an ordered list of
        tool calls.
        """

        logger.info("Planning steps for request: %s", user_prompt)

        prompt = self.build_prompt(user_prompt)

        payload = self.client.generate_json(prompt)

        response = json.dumps(payload)

        steps = self.parser.parse_plan(response)

        for step in steps:
            validate_tool_call(step, self.registry)

        logger.info(
            "Planned %d step(s): %s",
            len(steps),
            [step.tool_name for step in steps],
        )

        return steps

    def run(self, user_prompt: str) -> list[StepResult]:
        """
        Plan and sequentially execute every step for `user_prompt`.

        Execution stops at the first step that raises an error;
        results for steps executed so far (including the failed
        one) are returned.
        """

        steps = self.plan(user_prompt)

        results: list[StepResult] = []

        for step in steps:
            if step.tool_name == "none":
                results.append(
                    StepResult(
                        tool_name=step.tool_name,
                        kwargs=step.kwargs,
                    )
                )
                continue

            logger.info("Executing step: %s", step.tool_name)

            try:
                result = self.dispatcher.execute(
                    step.tool_name,
                    *step.args,
                    **step.kwargs,
                )

            except Exception as exc:
                logger.error(
                    "Step '%s' failed: %s", step.tool_name, exc
                )

                results.append(
                    StepResult(
                        tool_name=step.tool_name,
                        kwargs=step.kwargs,
                        error=str(exc),
                    )
                )

                break

            results.append(
                StepResult(
                    tool_name=step.tool_name,
                    kwargs=step.kwargs,
                    result=result,
                )
            )

        return results
