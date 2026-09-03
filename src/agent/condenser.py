"""
Conversation condenser for Pearl.

Prevents long sessions from exceeding the active model's context window
by compressing the middle portion of ``Memory.conversation`` into a single
structured summary turn, while preserving:

- The head (first N turns): original task, initial instructions.
- The tail (last N turns): most recent active context.
- The middle: replaced by one LLM-generated summary turn.

Trigger logic is TOKEN-AWARE — not turn-count-only:

    condense when:
        conversation_tokens > n_ctx * CONDENSER_PRESSURE_THRESHOLD
        OR turn_count > CONDENSER_MAX_TURNS
        OR a ContextLengthError occurred (emergency path)

The summary is structured so the next planning step can act on it:

    TASK / DECISIONS / FILES / CHANGES / TESTS / ERRORS / PENDING /
    IMPORTANT CONTEXT

Usage::

    from src.agent.condenser import Condenser
    from src.llm.client import LLMClient

    condenser = Condenser(llm_client)
    result = condenser.maybe_condense(memory, n_ctx=8192)
    if result and result.condensed:
        logger.info("Condensed %d → %d turns", result.turns_before, result.turns_after)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.config.settings import Settings
from src.llm.token_budget import estimate_tokens
from src.memory.memory import ConversationTurn, Memory

logger = logging.getLogger(__name__)

# Separator used in the prompt to mark each turn clearly.
_TURN_SEP = "\n---\n"

# Max tokens the LLM may generate for the summary.  Short enough for
# the 1.5B model; long enough to capture all structured sections.
_SUMMARY_MAX_TOKENS = 450


@dataclass(slots=True)
class CondensationResult:
    """Outcome of one condensation attempt."""

    condensed: bool
    turns_before: int
    turns_after: int
    tokens_before: int
    tokens_after: int
    reason: str = ""  # why condensation was or was not triggered


class CannotCondenseError(RuntimeError):
    """
    Raised when condensation is requested but the conversation is too
    short to compress further (head + tail already cover all turns).

    Callers should surface a clean failure to the user rather than
    retrying and looping forever.
    """


class Condenser:
    """
    Token-aware conversation condenser.

    Parameters
    ----------
    llm_client:
        Client used to generate the summary.  Should be the cheap/chat
        model (``ModelRouter.chat_client()``), not the planning model.
    keep_head:
        Number of turns to preserve from the start of the conversation.
        Defaults to ``Settings.CONDENSER_KEEP_HEAD``.
    keep_tail:
        Number of turns to preserve from the end of the conversation.
        Defaults to ``Settings.CONDENSER_KEEP_TAIL``.
    """

    def __init__(
        self,
        llm_client: object,  # LLMClient — typed as object to avoid circular import
        keep_head: int | None = None,
        keep_tail: int | None = None,
    ) -> None:
        self._client = llm_client
        self._keep_head = keep_head if keep_head is not None else Settings.CONDENSER_KEEP_HEAD
        self._keep_tail = keep_tail if keep_tail is not None else Settings.CONDENSER_KEEP_TAIL

    # ------------------------------------------------------------------ public

    def should_condense(self, memory: Memory, n_ctx: int) -> tuple[bool, str]:
        """
        Return ``(True, reason)`` when condensation is warranted.

        Checks two independent triggers:
        - Token pressure: conversation tokens exceed ``n_ctx * threshold``.
        - Turn count: total turns exceed ``Settings.CONDENSER_MAX_TURNS``.
        """
        turns = memory.conversation
        n_turns = len(turns)
        tokens = self._conversation_tokens(turns)
        pressure = tokens / max(1, n_ctx)
        threshold = Settings.CONDENSER_PRESSURE_THRESHOLD

        if pressure > threshold:
            return True, (
                f"token pressure {pressure:.2%} > threshold {threshold:.2%} "
                f"({tokens} tokens in conversation, n_ctx={n_ctx})"
            )

        if n_turns > Settings.CONDENSER_MAX_TURNS:
            return True, (
                f"turn count {n_turns} > CONDENSER_MAX_TURNS "
                f"{Settings.CONDENSER_MAX_TURNS}"
            )

        return False, (
            f"no condensation needed "
            f"(pressure={pressure:.2%}, turns={n_turns})"
        )

    def can_condense(self, memory: Memory) -> bool:
        """
        Return True if there are enough turns to compress something.

        Condensation requires at least head + tail + 1 middle turn.
        """
        required = self._keep_head + self._keep_tail + 1
        return len(memory.conversation) > required

    def maybe_condense(self, memory: Memory, n_ctx: int) -> CondensationResult:
        """
        Check triggers and condense if warranted.

        Returns a ``CondensationResult`` whether or not condensation ran
        (``condensed=False`` when below threshold).
        """
        should, reason = self.should_condense(memory, n_ctx)
        tokens_before = self._conversation_tokens(memory.conversation)

        if not should:
            return CondensationResult(
                condensed=False,
                turns_before=len(memory.conversation),
                turns_after=len(memory.conversation),
                tokens_before=tokens_before,
                tokens_after=tokens_before,
                reason=reason,
            )

        logger.info("Condenser triggered: %s", reason)
        return self.condense(memory, reason=reason)

    def condense(self, memory: Memory, reason: str = "emergency") -> CondensationResult:
        """
        Compress the middle of the conversation, modifying *memory* in place.

        Raises
        ------
        CannotCondenseError
            When the conversation is too short to compress (head + tail
            already span all turns).  The caller must surface a clear
            failure rather than retrying.
        """
        turns = memory.conversation
        n = len(turns)
        tokens_before = self._conversation_tokens(turns)

        if not self.can_condense(memory):
            raise CannotCondenseError(
                f"Cannot condense: only {n} turns, need > "
                f"{self._keep_head + self._keep_tail} to compress anything. "
                "The context window is exhausted and the conversation cannot "
                "be reduced further."
            )

        head = turns[: self._keep_head]
        tail = turns[n - self._keep_tail :]
        middle = turns[self._keep_head : n - self._keep_tail]

        logger.info(
            "Condensing: head=%d, middle=%d, tail=%d turns",
            len(head),
            len(middle),
            len(tail),
        )

        summary_content = self._summarise(middle)
        summary_turn = ConversationTurn(role="summary", content=summary_content)

        memory.conversation = head + [summary_turn] + tail

        tokens_after = self._conversation_tokens(memory.conversation)

        logger.info(
            "Condensed %d → %d turns, ~%d → ~%d tokens",
            n,
            len(memory.conversation),
            tokens_before,
            tokens_after,
        )

        return CondensationResult(
            condensed=True,
            turns_before=n,
            turns_after=len(memory.conversation),
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            reason=reason,
        )

    # ----------------------------------------------------------------- private

    @staticmethod
    def _conversation_tokens(turns: list[ConversationTurn]) -> int:
        """Estimate total tokens in the conversation turns."""
        return sum(estimate_tokens(t.content) for t in turns)

    def _summarise(self, middle: list[ConversationTurn]) -> str:
        """
        Call the LLM to produce a structured summary of *middle* turns.

        The structured format ensures the agent can act on the summary
        without re-reading the full history:

            TASK / DECISIONS / FILES / CHANGES / TESTS / ERRORS /
            PENDING / IMPORTANT CONTEXT
        """
        formatted = _TURN_SEP.join(
            f"[{t.role.upper()}]: {t.content}" for t in middle
        )

        prompt = (
            "You are summarizing a conversation between a user and Pearl, "
            "an AI coding assistant. The turns below are from the MIDDLE of "
            "a long session. The head and tail are preserved separately.\n\n"
            "Produce a structured, actionable summary in EXACTLY this format "
            "(keep every section heading, write 'none' if a section is empty):\n\n"
            "TASK:\n"
            "[The task(s) being worked on]\n\n"
            "DECISIONS:\n"
            "[Key decisions made]\n\n"
            "FILES:\n"
            "[Files read, created, or modified]\n\n"
            "CHANGES:\n"
            "[Specific code changes applied]\n\n"
            "TESTS:\n"
            "[Tests run and results]\n\n"
            "ERRORS:\n"
            "[Errors encountered and resolution status]\n\n"
            "PENDING:\n"
            "[Work not yet completed]\n\n"
            "IMPORTANT CONTEXT:\n"
            "[Any other facts the agent must know]\n\n"
            "---\n"
            "CONVERSATION TO SUMMARIZE:\n\n"
            f"{formatted}\n\n"
            "---\n"
            "Write the structured summary now. Be specific: include file paths, "
            "function names, error messages, and decisions. Do not write generic "
            "prose."
        )

        try:
            # LLMClient.generate is typed on the concrete class but we accept
            # the object duck-typed so tests can inject a simple mock without
            # importing the full LLMClient chain.
            summary = self._client.generate(  # type: ignore[attr-defined]
                prompt,
                max_new_tokens=_SUMMARY_MAX_TOKENS,
                temperature=0.1,  # deterministic — we want facts, not creativity
            )
            return f"[Context Summary — {len(middle)} turns condensed]\n\n{summary.strip()}"
        except Exception:
            # Summarisation failure must never crash the session.
            # Fall back to a lightweight extraction of key facts.
            logger.warning(
                "Condenser LLM call failed; using fallback summary.", exc_info=True
            )
            return self._fallback_summary(middle)

    @staticmethod
    def _fallback_summary(middle: list[ConversationTurn]) -> str:
        """
        Emergency fallback when the LLM summarisation call fails.

        Produces a minimal summary from the raw turns without a model call:
        first 200 chars of each turn so the agent can see what happened.
        """
        lines = [f"[Context Summary — {len(middle)} turns condensed (fallback)]\n"]
        for t in middle:
            preview = t.content[:200].replace("\n", " ")
            lines.append(f"- [{t.role}]: {preview}{'…' if len(t.content) > 200 else ''}")
        return "\n".join(lines)
