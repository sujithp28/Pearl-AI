"""
Planning engine for Pearl.

Breaks a complex user request into an ordered sequence of tool
calls and executes them one after another via the ToolDispatcher.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from src.agent.confidence import score_plan
from src.agent.dependency_graph import topological_sort
from src.agent.dispatcher import ToolDispatcher
from src.agent.plan_validator import validate_plan
from src.agent.tool_filter import relevant_tools
from src.config.settings import Settings
from src.config.workspace import get_workspace_root
from src.llm.client import LLMClient
from src.llm.context_budget import ContextBudget
from src.llm.parser import ToolCall, ToolParser
from src.llm.token_budget import truncate_to_tokens
from src.llm.validation import validate_tool_call
from src.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Resolved relative to this file (not the process's cwd): Pearl's
# workspace root is wherever the *target* project lives, which is
# never guaranteed to be Pearl's own repo, so these prompt templates
# must not depend on cwd to be found.
_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


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
        self.last_confidence_score: float | None = None

    @staticmethod
    def _workspace_root() -> str:
        """Return the workspace root the LLM should see in the planning prompt.

        Reads from the thread-local set by session.run_autonomous_stream() so
        both the prompt text and _ensure_within_workspace() agree on the same
        boundary when running in an autonomous thread.  Falls back to cwd when
        no root is set (CLI / test contexts that never call set_workspace_root).
        """
        return str(get_workspace_root())

    def _generate_json(
        self, prompt: str, cancel_check: Callable[[], bool] | None
    ) -> dict[str, Any]:
        """
        Call `self.client.generate_json`, forwarding `cancel_check`
        only when actually given.

        Kept to the old `generate_json(prompt)` call shape whenever
        cancellation isn't in use, rather than always passing
        `cancel_check=None` — the executor is the only caller that
        supplies a real one; everyone else (including every test
        double that stubs `generate_json` with a plain
        `lambda prompt: ...`) keeps working unmodified.
        """

        if cancel_check is None:
            return self.client.generate_json(prompt)

        return self.client.generate_json(prompt, cancel_check=cancel_check)

    def build_prompt(self, user_prompt: str, workspace_context: str = "") -> str:
        """
        Build the planning prompt from the external prompt template.

        Tools are filtered to those relevant for `user_prompt` to reduce
        token usage and improve tool-selection accuracy on small models.
        Workspace context is trimmed to the actual remaining budget after
        the base prompt (template + filtered tools + user request) is built.

        `workspace_context` is an opaque, already-formatted block of text
        prepended when non-empty. The Planner doesn't care what produced it.
        """

        prompt_template = self.client.load_prompt(self.PROMPT_FILE)

        filtered = relevant_tools(user_prompt, self.registry.get_tools())
        tools_json = json.dumps(filtered, indent=4)

        logger.debug(
            "Tool filter: %d/%d tools selected for prompt",
            len(filtered),
            len(self.registry.get_tools()),
        )

        prompt = prompt_template.format(
            tools=tools_json,
            user_prompt=user_prompt,
            workspace_root=self._workspace_root(),
        )

        if workspace_context:
            budget = ContextBudget(
                n_ctx=Settings.LOCAL_MODEL_CTX,
                response_tokens=Settings.MAX_NEW_TOKENS,
            )
            allowance = budget.context_token_allowance(prompt)
            workspace_context = truncate_to_tokens(
                workspace_context,
                allowance,
                label="workspace_context",
            )
            if workspace_context:
                prompt = (
                    "Current workspace context (recent activity in this "
                    "session):\n\n"
                    f"{workspace_context}\n\n"
                    f"{prompt}"
                )

        return prompt

    def plan(
        self,
        user_prompt: str,
        workspace_context: str = "",
        cancel_check: Callable[[], bool] | None = None,
    ) -> list[ToolCall]:
        """
        Ask the LLM to break `user_prompt` into an ordered list of
        tool calls.

        `workspace_context`: see `build_prompt`. `cancel_check`: see
        `LLMClient.generate`.
        """

        logger.info("Planning steps for request: %s", user_prompt)

        prompt = self.build_prompt(user_prompt, workspace_context)

        payload = self._generate_json(prompt, cancel_check)

        response = json.dumps(payload)

        steps = self.parser.parse_plan(response)

        # Sort before validating so that read-before-write and similar
        # ordering checks operate on execution order, not declaration order.
        steps = topological_sort(steps)

        validate_plan(steps)

        for step in steps:
            validate_tool_call(step, self.registry)

        confidence = score_plan(steps)
        self.last_confidence_score = confidence.score

        logger.info(
            "Planned %d step(s): %s (confidence=%.2f)",
            len(steps),
            [step.tool_name for step in steps],
            confidence.score,
        )

        return steps

    def build_replan_prompt(
        self,
        user_prompt: str,
        completed: list[dict[str, Any]],
        failed: dict[str, Any],
    ) -> str:
        """
        Build the replanning prompt, filtering tools to the same subset
        used in the original plan to keep the token budget consistent.
        """

        prompt_template = self.client.load_prompt(self.REPLAN_PROMPT_FILE)

        filtered = relevant_tools(user_prompt, self.registry.get_tools())
        tools_json = json.dumps(filtered, indent=4)

        recovery_hint = failed.get("suggestion", "") if isinstance(failed, dict) else ""

        return prompt_template.format(
            tools=tools_json,
            user_prompt=user_prompt,
            completed_steps=json.dumps(completed, indent=4, default=str),
            failed_step=json.dumps(failed, indent=4, default=str),
            workspace_root=self._workspace_root(),
            recovery_hint=recovery_hint,
        )

    def replan(
        self,
        user_prompt: str,
        completed: list[dict[str, Any]],
        failed: dict[str, Any],
        cancel_check: Callable[[], bool] | None = None,
        replans_used: int = 0,
    ) -> list[ToolCall]:
        """
        Ask the LLM for a revised remaining plan after `failed`
        failed, given the steps already completed successfully.

        Reuses the same LLM client, parser, and validation as
        `plan()` — only the prompt differs. `cancel_check`: see
        `LLMClient.generate`.
        """

        logger.info(
            "Replanning after '%s' failed: %s",
            failed.get("tool"),
            failed.get("error"),
        )

        prompt = self.build_replan_prompt(user_prompt, completed, failed)

        payload = self._generate_json(prompt, cancel_check)

        response = json.dumps(payload)

        steps = self.parser.parse_plan(response)

        steps = topological_sort(steps)

        validate_plan(steps)

        for step in steps:
            validate_tool_call(step, self.registry)

        confidence = score_plan(steps, replans_used=replans_used)
        self.last_confidence_score = confidence.score

        logger.info(
            "Replanned %d step(s): %s (confidence=%.2f, replans_used=%d)",
            len(steps),
            [step.tool_name for step in steps],
            confidence.score,
            replans_used,
        )

        return steps

    # `run()` used to live here: it dispatched each planned step
    # directly, sequentially, with no replanning. Removed rather than
    # kept — dispatching this way happens completely outside
    # AutonomousExecutor, so no PatchManager is ever active and
    # every write tool falls through to writing straight to disk,
    # bypassing the approval gate entirely. That's not a style
    # preference; it's confirmed live: calling
    # `dispatcher.execute("create_file", ...)` outside an executor run
    # writes the file immediately with zero review. Its only callers —
    # `PearlAgent.plan_and_run()` and the `pearl/plan` MCP method —
    # are removed for the same reason. Use `AutonomousExecutor.run()`
    # (via `PearlAgent.run_autonomous()` or the MCP server's
    # `pearl/runAutonomous`), which is the only path that stages
    # writes behind approval.
