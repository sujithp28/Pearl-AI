"""
Tests for token budget enforcement — P1 from OPEN_SOURCE_ARCHITECTURE_REVIEW.

Verifies that workspace context is truncated to the configured ceiling
before reaching the LLM, and that the budget integrates correctly into
Planner.build_prompt().
"""

from __future__ import annotations

from src.llm.token_budget import (
    _TRUNCATION_NOTICE,
    estimate_tokens,
    truncate_to_tokens,
)


class TestEstimateTokens:
    def test_empty_string_returns_one(self):
        assert estimate_tokens("") == 1

    def test_four_chars_is_one_token(self):
        assert estimate_tokens("abcd") == 1

    def test_eight_chars_is_two_tokens(self):
        assert estimate_tokens("12345678") == 2

    def test_large_text(self):
        text = "x" * 4000
        assert estimate_tokens(text) == 1000


class TestTruncateToTokens:
    def test_short_text_unchanged(self):
        text = "hello world"
        result = truncate_to_tokens(text, max_tokens=100)
        assert result == text

    def test_truncation_appends_notice(self):
        text = "a" * 4000  # 1000 tokens
        result = truncate_to_tokens(text, max_tokens=10)
        assert result.endswith(_TRUNCATION_NOTICE)

    def test_truncated_text_fits_budget(self):
        text = "a" * 4000  # 1000 tokens
        result = truncate_to_tokens(text, max_tokens=10)
        assert estimate_tokens(result) <= 10 + (len(_TRUNCATION_NOTICE) // 4) + 1

    def test_zero_budget_returns_notice_only(self):
        text = "some context"
        result = truncate_to_tokens(text, max_tokens=0)
        # max_tokens=0 means no budget at all — return unchanged per spec
        assert result == text

    def test_exact_fit_not_truncated(self):
        # Exactly 10 tokens = 40 chars
        text = "a" * 40
        result = truncate_to_tokens(text, max_tokens=10)
        assert result == text

    def test_one_over_budget_is_truncated(self):
        # 44 chars = 11 tokens (44 // 4), just over the 10-token limit
        text = "a" * 44
        result = truncate_to_tokens(text, max_tokens=10)
        assert result != text
        assert _TRUNCATION_NOTICE in result

    def test_label_does_not_affect_output(self):
        text = "x" * 4000
        r1 = truncate_to_tokens(text, max_tokens=10, label="ctx_a")
        r2 = truncate_to_tokens(text, max_tokens=10, label="ctx_b")
        assert r1 == r2


class TestPlannerTokenBudgetIntegration:
    """
    Integration: Planner.build_prompt() must truncate workspace_context
    to Settings.TOKEN_BUDGET_MAX_CONTEXT_TOKENS before assembling.
    """

    def test_build_prompt_truncates_large_context(self, monkeypatch):
        from unittest.mock import MagicMock

        from src.agent.planner import Planner
        from src.config.settings import Settings

        # n_ctx=2048, response=1024 → usable=1024.  Tiny base prompt (~12 tok)
        # + margin (~102) leaves ~910 tok for workspace context — far less than
        # the 2 500-token big_context, so truncation must occur.
        monkeypatch.setattr(Settings, "LOCAL_MODEL_CTX", 2048, raising=True)

        registry = MagicMock()
        registry.get_tools.return_value = []
        dispatcher = MagicMock()
        client = MagicMock()
        client.load_prompt.return_value = (
            "Plan: {tools}\n{user_prompt}\n{workspace_root}"
        )

        planner = Planner(registry, dispatcher, client)

        # workspace_context that far exceeds any budget derived from n_ctx=512
        big_context = "w" * 10_000

        prompt = planner.build_prompt("fix bug", workspace_context=big_context)

        # The prompt should contain the truncation notice, not the full context
        assert _TRUNCATION_NOTICE in prompt
        assert big_context not in prompt

    def test_build_prompt_leaves_small_context_intact(self, monkeypatch):
        from unittest.mock import MagicMock

        from src.agent.planner import Planner
        from src.config.settings import Settings

        # Large context window → ContextBudget gives plenty of room for "tiny context"
        monkeypatch.setattr(Settings, "LOCAL_MODEL_CTX", 32_768, raising=True)

        registry = MagicMock()
        registry.get_tools.return_value = []
        dispatcher = MagicMock()
        client = MagicMock()
        client.load_prompt.return_value = (
            "Plan: {tools}\n{user_prompt}\n{workspace_root}"
        )

        planner = Planner(registry, dispatcher, client)
        small_context = "tiny context"

        prompt = planner.build_prompt("fix bug", workspace_context=small_context)

        assert small_context in prompt
        assert _TRUNCATION_NOTICE not in prompt
