"""
Context budget calculator for Pearl's planning pipeline.

Single source of truth for how many tokens can be spent on each
component of a planning prompt given the model's actual context window.

Priority order (CLAUDE.md §5):
  1. Base planning template + user request   (fixed, always fits)
  2. Tool schemas (filtered subset)           (variable; filter reduces this)
  3. Workspace / repository context          (fills remaining budget)
  4. Response headroom                        (reserved for model output)
"""
from __future__ import annotations

_CHARS_PER_TOKEN = 4  # 4 UTF-8 chars ≈ 1 BPE token (conservative for code/JSON)
_SAFETY_MARGIN_PCT = 0.05  # keep 5% slack so a slightly-off estimate never overflows


class ContextBudget:
    """
    Tracks token budget for one planning prompt.

    Usage::

        budget = ContextBudget(n_ctx=8192, response_tokens=1024)

        # Build base prompt (template + filtered tools + user request)
        base = build_base_prompt(...)

        # How many tokens remain for workspace context?
        allowance = budget.context_token_allowance(base)

        # Truncate and prepend
        ctx = truncate_to_tokens(semantic_ctx, allowance, label="workspace_context")
    """

    def __init__(self, n_ctx: int, response_tokens: int = 1024) -> None:
        self.n_ctx = n_ctx
        self.response_tokens = max(0, response_tokens)

    @property
    def usable(self) -> int:
        """Tokens available on the input (prompt) side."""
        return max(0, self.n_ctx - self.response_tokens)

    def context_token_allowance(self, base_prompt: str) -> int:
        """
        How many tokens may be spent on workspace context, given that
        `base_prompt` (template + tools JSON + user request — everything
        except the workspace context block) has already been assembled.

        A 5% safety margin is subtracted so a slightly-off char→token
        estimate never pushes the total past ``n_ctx``.
        """
        base_tokens = len(base_prompt) // _CHARS_PER_TOKEN
        margin = max(50, int(self.n_ctx * _SAFETY_MARGIN_PCT))
        return max(0, self.usable - base_tokens - margin)
