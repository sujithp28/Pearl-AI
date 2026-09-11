"""
Context Engine for Pearl V2.

Central context pipeline — every model call must pass through here.

Assembles context from:
  1. Base system prompt
  2. Tool definitions (filtered by ToolFilter)
  3. Original user task
  4. Conversation history (condensed if needed)
  5. Repository map (token-bounded, ranked)
  6. Execution results from current run

Dual token budgeting:
  - conversation_budget: tokens for history
  - repository_budget: tokens for repo context
  - Total must stay under n_ctx - response_headroom

Usage::

    engine = ContextEngine(
        llm_client=chat_llm,
        memory=session.memory,
        context_service=session._context_service,
        condenser=session._condenser,
    )

    ctx = engine.build(
        task=user_prompt,
        tool_descriptions=filtered_tools_json,
        execution_results=None,
    )
    # ctx.system_prompt, ctx.history, ctx.context_block
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.agent.condenser import CondensationResult, Condenser
from src.config.settings import Settings
from src.llm.context_budget import ContextBudget
from src.llm.token_budget import estimate_tokens, truncate_to_tokens
from src.memory.memory import Memory

logger = logging.getLogger(__name__)

# Fraction of total context budget to reserve for conversation history.
_HISTORY_BUDGET_FRACTION = 0.3
# Fraction for repository context.
_REPO_BUDGET_FRACTION = 0.4
# The remainder (0.3) goes to tools, base prompt, etc.


@dataclass(slots=True)
class EngineContext:
    """Assembled context ready for a model call."""

    system_prompt: str
    history: list[dict[str, str]]  # [{role, content}, ...]
    context_block: str  # workspace/repo context injected into user message
    tokens_used: int
    tokens_budget: int
    was_condensed: bool
    condensation_result: CondensationResult | None = None


class ContextEngine:
    """
    Assembles and budgets context for every model call.

    Parameters
    ----------
    memory:
        Pearl's conversation Memory.
    condenser:
        Condenser instance (created per session).
    n_ctx:
        Model context window size.
    response_tokens:
        Reserved tokens for model response.
    context_service:
        RepositoryService for semantic context (optional).
    context_builder:
        SemanticContextBuilder doing the ranking/retrieval. Defaults to a
        fresh one; injectable for tests.
    workspace_memory:
        Optional session signals passed through to the builder's ranking.
    """

    def __init__(
        self,
        memory: Memory,
        condenser: Condenser,
        n_ctx: int | None = None,
        response_tokens: int = Settings.MAX_NEW_TOKENS,
        context_service: Any = None,  # RepositoryService — optional
        context_builder: Any = None,  # SemanticContextBuilder — optional
        workspace_memory: Any = None,  # WorkspaceMemory — optional
    ) -> None:
        self._memory = memory
        self._condenser = condenser
        self._n_ctx = n_ctx or Settings.LOCAL_MODEL_CTX
        self._response_tokens = response_tokens
        self._context_service = context_service
        self._workspace_memory = workspace_memory

        if context_builder is None:
            from src.repository.context import SemanticContextBuilder

            context_builder = SemanticContextBuilder()

        self._context_builder = context_builder

        self._budget = ContextBudget(
            n_ctx=self._n_ctx,
            response_tokens=self._response_tokens,
        )

    def build(
        self,
        task: str,
        system_prompt: str = "",
        tool_descriptions: list[dict[str, Any]] | None = None,
        execution_results: list[Any] | None = None,
        history_limit: int | None = None,
    ) -> EngineContext:
        """
        Build the complete context for one model call.

        Proactively condenses history if token pressure is high.
        Budgets conversation and repository context separately.
        Never exceeds n_ctx.

        Parameters
        ----------
        task:
            The current user request (injected into context block).
        system_prompt:
            Base system prompt.
        tool_descriptions:
            Tool metadata list (already filtered by ToolFilter).
        execution_results:
            Recent execution step summaries to include.
        history_limit:
            Override for how many turns to retrieve.
        """
        # 1. Proactive condensation
        condense_result: CondensationResult | None = None
        was_condensed = False

        condensed = self._condenser.maybe_condense(self._memory, n_ctx=self._n_ctx)
        if condensed.condensed:
            was_condensed = True
            condense_result = condensed
            logger.info(
                "ContextEngine: condensed %d→%d turns, ~%d→~%d tokens",
                condensed.turns_before,
                condensed.turns_after,
                condensed.tokens_before,
                condensed.tokens_after,
            )

        # 2. Compute token budgets
        base_tokens = estimate_tokens(system_prompt)
        tool_tokens = estimate_tokens(str(tool_descriptions or []))
        task_tokens = estimate_tokens(task)
        overhead = base_tokens + tool_tokens + task_tokens + 200  # margin

        remaining = max(0, self._budget.usable - overhead)
        history_budget = int(remaining * _HISTORY_BUDGET_FRACTION)
        repo_budget = int(remaining * _REPO_BUDGET_FRACTION)

        # 3. Conversation history (token-capped, current request always kept)
        limit = history_limit or Settings.CHAT_HISTORY_TURNS
        raw_history = self._memory.recent_messages(limit=limit)
        history = self._cap_history(raw_history, history_budget)

        # 4. Repository context (token-bounded)
        #
        # History is allowed to overrun its share when the newest message
        # alone exceeds it — that message is the user's current request and
        # is never dropped. Repository context is Pearl's own retrieval, so
        # it is what gives way: charge the overrun against the repo budget
        # rather than letting the two shares together blow past n_ctx.
        history_tokens = sum(estimate_tokens(m["content"]) for m in history)
        overrun = max(0, history_tokens - history_budget)
        repo_budget = max(0, repo_budget - overrun)

        repo_context = self._build_repo_context(task, repo_budget)

        # 5. Execution results block
        exec_block = self._format_exec_results(execution_results)

        # 6. Assemble context block (injected as part of user message)
        parts: list[str] = []
        if repo_context:
            parts.append(repo_context)
        if exec_block:
            parts.append(exec_block)
        context_block = "\n\n".join(parts)

        total_used = (
            base_tokens
            + tool_tokens
            + task_tokens
            + estimate_tokens(context_block)
            + history_tokens
        )

        logger.debug(
            "ContextEngine.build: budget=%d used=%d (base=%d tools=%d task=%d repo=%d hist=%d)",
            self._budget.usable,
            total_used,
            base_tokens,
            tool_tokens,
            task_tokens,
            estimate_tokens(repo_context),
            history_tokens,
        )

        return EngineContext(
            system_prompt=system_prompt,
            history=history,
            context_block=context_block,
            tokens_used=total_used,
            tokens_budget=self._budget.usable,
            was_condensed=was_condensed,
            condensation_result=condense_result,
        )

    # ── Private ──────────────────────────────────────────────────────────────

    def _cap_history(
        self,
        messages: list[dict[str, str]],
        budget: int,
    ) -> list[dict[str, str]]:
        """
        Trim history from the oldest end until it fits within `budget`
        tokens, always keeping the newest message.

        The newest message is what the user just asked. Dropping it
        because it happens to be larger than the history slice of the
        budget would leave Pearl planning against an empty prompt — the
        one input the whole turn is about. When it does not fit, it is
        kept whole and every older message is dropped; the caller
        reclaims the space from the repository budget instead (see
        `build`), which is context Pearl chose, not something the user
        typed.
        """
        if not messages:
            return []

        newest = messages[-1]
        result: list[dict[str, str]] = [newest]
        total = estimate_tokens(newest["content"])

        for msg in reversed(messages[:-1]):
            tokens = estimate_tokens(msg["content"])
            if total + tokens > budget:
                break
            result.insert(0, msg)
            total += tokens

        return result

    def _build_repo_context(self, task: str, budget: int) -> str:
        """
        Fetch semantically relevant repository context up to `budget` tokens.

        Retrieval itself belongs to `SemanticContextBuilder` — the one
        ranking implementation in the codebase. This method only budgets
        what comes back, so there is a single place that decides which
        files are relevant and a single place that decides how much of
        them fits.
        """
        if self._context_service is None or budget <= 0:
            return ""

        try:
            raw = self._context_builder.build(
                task,
                self._context_service,
                self._workspace_memory,
            )
        except Exception as exc:
            logger.debug("ContextEngine: repo context fetch failed: %s", exc)
            return ""

        if not raw:
            return ""

        return truncate_to_tokens(raw, budget, label="repository_context")

    def _format_exec_results(
        self,
        results: list[Any] | None,
    ) -> str:
        """Format recent execution steps as a compact block."""
        if not results:
            return ""
        parts: list[str] = ["[Recent execution]"]
        for step in results[-5:]:  # cap at last 5
            tool = getattr(step, "tool_name", "?")
            ok = "✓" if getattr(step, "succeeded", False) else "✗"
            summary = getattr(step, "summary", "")[:200]
            parts.append(f"  {ok} {tool}: {summary}")
        return "\n".join(parts)
