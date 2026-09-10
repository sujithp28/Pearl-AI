"""Tests for src/llm/context_budget.py."""

from __future__ import annotations

from src.llm.context_budget import ContextBudget


class TestContextBudget:
    def test_usable_subtracts_response_tokens(self) -> None:
        budget = ContextBudget(n_ctx=8192, response_tokens=1024)
        assert budget.usable == 7168

    def test_usable_clamps_to_zero(self) -> None:
        budget = ContextBudget(n_ctx=512, response_tokens=1024)
        assert budget.usable == 0

    def test_allowance_decreases_with_larger_base(self) -> None:
        budget = ContextBudget(n_ctx=8192, response_tokens=1024)
        short_base = "x" * 4000  # ~1 000 tokens
        long_base = "x" * 20000  # ~5 000 tokens
        assert budget.context_token_allowance(
            short_base
        ) > budget.context_token_allowance(long_base)

    def test_allowance_never_negative(self) -> None:
        budget = ContextBudget(n_ctx=8192, response_tokens=1024)
        # base prompt already exceeds usable window
        huge_base = "x" * 100_000
        assert budget.context_token_allowance(huge_base) == 0

    def test_allowance_typical_case(self) -> None:
        # n_ctx=8192, response=1024, usable=7168
        # base_prompt ~17259 chars = ~4314 tokens (actual Pearl baseline)
        budget = ContextBudget(n_ctx=8192, response_tokens=1024)
        base_prompt = "x" * 17259
        allowance = budget.context_token_allowance(base_prompt)
        # usable=7168, base_tokens=4314, margin≈410 → expect ~2444
        assert 2000 <= allowance <= 3000

    def test_filtered_tools_give_more_allowance(self) -> None:
        # With filtered tools: base ~6864 chars vs ~17259 chars with all tools
        budget = ContextBudget(n_ctx=8192, response_tokens=1024)
        base_all_tools = "x" * 17259  # all 48 tools
        base_filtered = "x" * 6864  # ~12 filtered tools

        allowance_all = budget.context_token_allowance(base_all_tools)
        allowance_filtered = budget.context_token_allowance(base_filtered)
        # Filtered should give significantly more context budget
        assert allowance_filtered > allowance_all + 1000
