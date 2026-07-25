"""
Planning engine for Pearl.

Breaks a complex user request into an ordered sequence of tool
calls and executes them one after another via the ToolDispatcher.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.agent.dispatcher import ToolDispatcher
from src.llm.client import LLMClient
from src.llm.parser import ToolCall, ToolParser
from src.llm.validation import validate_tool_call
from src.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Resolved relative to this file (not the process's cwd): Pearl's
# workspace root is wherever the *target* project lives, which is
# never guaranteed to be Pearl's own repo, so these prompt templates
# must not depend on cwd to be found.
_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


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

    PROMPT_FILE = str(_PROMPTS_DIR / "planning.txt")
    REPLAN_PROMPT_FILE = str(_PROMPTS_DIR / "replanning.txt")

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

    @staticmethod
    def _workspace_root() -> str:
        """
        The directory every planned path must stay inside — the same
        root `file_tools._ensure_within_workspace` enforces against,
        read the same way (the process's cwd, which the MCP server is
        launched with set to the user's project).

        Resolved per call rather than cached at import: the enforcing
        side reads `Path.cwd()` per call too, so caching here could
        silently tell the model one boundary while a different one is
        actually enforced.
        """

        return str(Path.cwd().resolve())

    def build_prompt(self, user_prompt: str, workspace_context: str = "") -> str:
        """
        Build the planning prompt from the external prompt template.

        `workspace_context` is an opaque, already-formatted block of
        text (e.g. from `WorkspaceMemory.generate_context()`)
        prepended as extra context when non-empty. The Planner
        doesn't know or care what produced it — this keeps Planner
        decoupled from any specific context source.
        """

        prompt_template = self.client.load_prompt(self.PROMPT_FILE)

        tools = json.dumps(
            self.registry.get_tools(),
            indent=4,
        )

        prompt = prompt_template.format(
            tools=tools,
            user_prompt=user_prompt,
            workspace_root=self._workspace_root(),
        )

        if workspace_context:
            prompt = (
                "Current workspace context (recent activity in this "
                "session):\n\n"
                f"{workspace_context}\n\n"
                f"{prompt}"
            )

        return prompt

    def plan(self, user_prompt: str, workspace_context: str = "") -> list[ToolCall]:
        """
        Ask the LLM to break `user_prompt` into an ordered list of
        tool calls.

        `workspace_context`: see `build_prompt`.
        """

        logger.info("Planning steps for request: %s", user_prompt)

        prompt = self.build_prompt(user_prompt, workspace_context)

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

    def build_replan_prompt(
        self,
        user_prompt: str,
        completed: list[dict[str, Any]],
        failed: dict[str, Any],
    ) -> str:
        """
        Build the replanning prompt from the external prompt
        template, given what's already completed and the step that
        just failed.
        """

        prompt_template = self.client.load_prompt(self.REPLAN_PROMPT_FILE)

        tools = json.dumps(
            self.registry.get_tools(),
            indent=4,
        )

        return prompt_template.format(
            tools=tools,
            user_prompt=user_prompt,
            completed_steps=json.dumps(completed, indent=4, default=str),
            failed_step=json.dumps(failed, indent=4, default=str),
            workspace_root=self._workspace_root(),
        )

    def replan(
        self,
        user_prompt: str,
        completed: list[dict[str, Any]],
        failed: dict[str, Any],
    ) -> list[ToolCall]:
        """
        Ask the LLM for a revised remaining plan after `failed`
        failed, given the steps already completed successfully.

        Reuses the same LLM client, parser, and validation as
        `plan()` — only the prompt differs.
        """

        logger.info(
            "Replanning after '%s' failed: %s",
            failed.get("tool"),
            failed.get("error"),
        )

        prompt = self.build_replan_prompt(user_prompt, completed, failed)

        payload = self.client.generate_json(prompt)

        response = json.dumps(payload)

        steps = self.parser.parse_plan(response)

        for step in steps:
            validate_tool_call(step, self.registry)

        logger.info(
            "Replanned %d step(s): %s",
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
                logger.error("Step '%s' failed: %s", step.tool_name, exc)

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
