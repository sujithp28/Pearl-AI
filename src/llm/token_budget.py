"""
Token budget utilities for Pearl.

Prevents context overflow by estimating token counts and truncating
workspace context before it reaches the LLM.  The estimate is rough
(1 token ≈ 4 UTF-8 chars for mixed code/English — the same heuristic
Anthropic and OpenAI use in their official tokenizer guides) and errs
on the side of over-counting, which is the conservative direction: a
little extra truncation is harmless, an overflow crash is not.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 4
_TRUNCATION_NOTICE = "\n[...context truncated to fit token budget...]"


def estimate_tokens(text: str) -> int:
    """Rough token count estimate from UTF-8 character count."""
    return max(1, len(text) // _CHARS_PER_TOKEN)


def truncate_to_tokens(
    text: str,
    max_tokens: int,
    label: str = "context",
) -> str:
    """
    Return `text` truncated so its estimated token count fits within
    `max_tokens`.  Returns the original string unchanged when it already
    fits.  Logs a warning when truncation occurs so callers know context
    was dropped.
    """
    if max_tokens <= 0 or estimate_tokens(text) <= max_tokens:
        return text

    max_chars = max_tokens * _CHARS_PER_TOKEN - len(_TRUNCATION_NOTICE)
    truncated = text[: max(0, max_chars)] + _TRUNCATION_NOTICE

    logger.warning(
        "Token budget: %s truncated from ~%d to ~%d tokens.",
        label,
        estimate_tokens(text),
        max_tokens,
    )

    return truncated
