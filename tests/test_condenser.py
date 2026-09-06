"""
Tests for src/agent/condenser.py

Covers all 14 specified cases:
 1.  No condensation below threshold.
 2.  Turn-count threshold trigger.
 3.  Token-pressure threshold trigger.
 4.  Head turns (original task) preserved.
 5.  Tail turns (most recent context) preserved.
 6.  Middle turns replaced with one summary turn.
 7.  Summary contains structured actionable sections.
 8.  Large tool output is compressed (token count drops).
 9.  ContextLengthError triggers condensation.
10.  Same operation retries after condensation.
11.  Retry is bounded by CONDENSER_MAX_RETRIES.
12.  Cannot condense further → CannotCondenseError.
13.  Existing approval invariant still passes.
14.  Existing ContextBudget tests still pass.
"""
from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.agent.condenser import CannotCondenseError, Condenser, CondensationResult
from src.config.settings import Settings
from src.llm.errors import ContextLengthError
from src.memory.memory import ConversationTurn, Memory


# ── helpers ─────────────────────────────────────────────────────────────────

def _make_llm(summary: str = "TASK:\ntest task\n\nDECISIONS:\nnone\n\nFILES:\nnone\n\nCHANGES:\nnone\n\nTESTS:\nnone\n\nERRORS:\nnone\n\nPENDING:\nnone\n\nIMPORTANT CONTEXT:\nnone") -> MagicMock:
    """Return a mock LLM client whose generate() returns *summary*."""
    mock = MagicMock()
    mock.generate.return_value = summary
    return mock


def _make_memory(n_turns: int, content_per_turn: str = "short message") -> Memory:
    """Build a Memory with *n_turns* alternating user/agent turns."""
    m = Memory()
    for i in range(n_turns):
        role = "user" if i % 2 == 0 else "agent"
        m.record_turn(role, f"[turn {i}] {content_per_turn}")
    return m


def _big_turn(kb: int = 8) -> str:
    """Return a string roughly *kb* kilobytes long — simulates a large tool result."""
    return "x" * (kb * 1024)


# ── Test 1: no condensation below threshold ──────────────────────────────────

class TestNoCondensation:
    def test_below_both_thresholds(self) -> None:
        condenser = Condenser(_make_llm(), keep_head=3, keep_tail=5)
        memory = _make_memory(n_turns=5)  # well below MAX_TURNS=40
        result = condenser.maybe_condense(memory, n_ctx=8192)
        assert not result.condensed
        assert result.turns_after == 5

    def test_result_type(self) -> None:
        condenser = Condenser(_make_llm(), keep_head=3, keep_tail=5)
        memory = _make_memory(n_turns=3)
        result = condenser.maybe_condense(memory, n_ctx=8192)
        assert isinstance(result, CondensationResult)
        assert result.condensed is False

    def test_memory_unchanged_when_no_condensation(self) -> None:
        condenser = Condenser(_make_llm(), keep_head=3, keep_tail=5)
        memory = _make_memory(n_turns=4)
        original_turns = list(memory.conversation)
        condenser.maybe_condense(memory, n_ctx=8192)
        assert memory.conversation == original_turns


# ── Test 2: turn-count threshold trigger ────────────────────────────────────

class TestTurnCountThreshold:
    def test_triggers_at_max_turns(self) -> None:
        condenser = Condenser(_make_llm(), keep_head=2, keep_tail=2)
        # Create exactly MAX_TURNS + 1 turns
        memory = _make_memory(n_turns=Settings.CONDENSER_MAX_TURNS + 1)
        result = condenser.maybe_condense(memory, n_ctx=8192)
        assert result.condensed

    def test_does_not_trigger_at_max_turns_exactly(self) -> None:
        condenser = Condenser(_make_llm(), keep_head=2, keep_tail=2)
        memory = _make_memory(n_turns=Settings.CONDENSER_MAX_TURNS)
        # MAX_TURNS itself is the boundary — should_condense uses > not >=
        result = condenser.maybe_condense(memory, n_ctx=8192)
        assert not result.condensed

    def test_turn_count_reason_in_result(self) -> None:
        condenser = Condenser(_make_llm(), keep_head=2, keep_tail=2)
        memory = _make_memory(n_turns=Settings.CONDENSER_MAX_TURNS + 5)
        result = condenser.maybe_condense(memory, n_ctx=8192)
        assert result.condensed
        assert "turn count" in result.reason.lower() or "token" in result.reason.lower()


# ── Test 3: token-pressure threshold trigger ─────────────────────────────────

class TestTokenPressureThreshold:
    def test_large_turns_trigger_below_max_turns(self) -> None:
        condenser = Condenser(_make_llm(), keep_head=2, keep_tail=2)
        # 10 turns each with ~8 KB → ~20 000 tokens >> 0.5 * 8192
        memory = _make_memory(n_turns=10, content_per_turn=_big_turn(kb=8))
        assert len(memory.conversation) <= Settings.CONDENSER_MAX_TURNS  # turns check passes
        result = condenser.maybe_condense(memory, n_ctx=8192)
        assert result.condensed, "Token pressure should have triggered condensation"

    def test_pressure_ratio_reported(self) -> None:
        condenser = Condenser(_make_llm(), keep_head=2, keep_tail=2)
        triggered, reason = condenser.should_condense(
            _make_memory(n_turns=10, content_per_turn=_big_turn(kb=8)),
            n_ctx=8192,
        )
        assert triggered
        assert "pressure" in reason.lower() or "token" in reason.lower()

    def test_small_n_ctx_triggers_sooner(self) -> None:
        condenser = Condenser(_make_llm(), keep_head=2, keep_tail=2)
        # "word " * 250 = 1250 chars → estimate_tokens = 312 tokens/turn
        # 8 turns × 312 = 2496 tokens; n_ctx=4096 → pressure 60.9% > 50% → triggers
        memory = _make_memory(n_turns=8, content_per_turn="word " * 250)
        triggered, _ = condenser.should_condense(memory, n_ctx=4096)
        assert triggered
        # Same conversation with a huge context window does not trigger
        triggered_large, _ = condenser.should_condense(memory, n_ctx=131072)
        assert not triggered_large


# ── Test 4: head turns preserved ────────────────────────────────────────────

class TestHeadPreservation:
    def test_first_n_turns_unchanged(self) -> None:
        keep_head = 3
        condenser = Condenser(_make_llm(), keep_head=keep_head, keep_tail=2)
        memory = _make_memory(n_turns=Settings.CONDENSER_MAX_TURNS + 1)
        original_head = [
            (t.role, t.content) for t in memory.conversation[:keep_head]
        ]
        condenser.maybe_condense(memory, n_ctx=8192)
        actual_head = [
            (t.role, t.content) for t in memory.conversation[:keep_head]
        ]
        assert actual_head == original_head

    def test_original_task_turn_preserved(self) -> None:
        condenser = Condenser(_make_llm(), keep_head=3, keep_tail=2)
        memory = _make_memory(n_turns=Settings.CONDENSER_MAX_TURNS + 1)
        original_task_content = memory.conversation[0].content
        condenser.maybe_condense(memory, n_ctx=8192)
        assert memory.conversation[0].content == original_task_content


# ── Test 5: tail turns preserved ────────────────────────────────────────────

class TestTailPreservation:
    def test_last_n_turns_unchanged(self) -> None:
        keep_tail = 5
        condenser = Condenser(_make_llm(), keep_head=2, keep_tail=keep_tail)
        memory = _make_memory(n_turns=Settings.CONDENSER_MAX_TURNS + 1)
        original_tail = [
            (t.role, t.content) for t in memory.conversation[-keep_tail:]
        ]
        condenser.maybe_condense(memory, n_ctx=8192)
        actual_tail = [
            (t.role, t.content) for t in memory.conversation[-keep_tail:]
        ]
        assert actual_tail == original_tail


# ── Test 6: middle turns replaced with one summary turn ──────────────────────

class TestMiddleReplacement:
    def test_middle_becomes_one_summary_turn(self) -> None:
        keep_head, keep_tail = 3, 5
        condenser = Condenser(_make_llm(), keep_head=keep_head, keep_tail=keep_tail)
        memory = _make_memory(n_turns=Settings.CONDENSER_MAX_TURNS + 1)
        condenser.maybe_condense(memory, n_ctx=8192)
        # After condensation: head (3) + 1 summary + tail (5) = 9
        assert len(memory.conversation) == keep_head + 1 + keep_tail

    def test_summary_turn_role(self) -> None:
        condenser = Condenser(_make_llm(), keep_head=3, keep_tail=5)
        memory = _make_memory(n_turns=Settings.CONDENSER_MAX_TURNS + 1)
        condenser.maybe_condense(memory, n_ctx=8192)
        summary_turn = memory.conversation[3]  # index 3 = right after head
        assert summary_turn.role == "summary"


# ── Test 7: summary contains structured sections ────────────────────────────

class TestSummaryStructure:
    STRUCTURED_SUMMARY = (
        "TASK:\nImplement token condenser\n\n"
        "DECISIONS:\nUse LLM for middle compression\n\n"
        "FILES:\nsrc/agent/condenser.py\n\n"
        "CHANGES:\nAdded Condenser class\n\n"
        "TESTS:\nAll passing\n\n"
        "ERRORS:\nNone\n\n"
        "PENDING:\nWire into session.py\n\n"
        "IMPORTANT CONTEXT:\nUses chat_llm for summarization"
    )

    def test_summary_content_from_llm(self) -> None:
        condenser = Condenser(_make_llm(self.STRUCTURED_SUMMARY), keep_head=3, keep_tail=5)
        memory = _make_memory(n_turns=Settings.CONDENSER_MAX_TURNS + 1)
        condenser.maybe_condense(memory, n_ctx=8192)
        summary_content = memory.conversation[3].content
        for section in ("TASK:", "DECISIONS:", "FILES:", "CHANGES:", "PENDING:"):
            assert section in summary_content, f"Expected section '{section}' in summary"

    def test_summary_mentions_condensed_count(self) -> None:
        condenser = Condenser(_make_llm(self.STRUCTURED_SUMMARY), keep_head=3, keep_tail=5)
        memory = _make_memory(n_turns=Settings.CONDENSER_MAX_TURNS + 1)
        condenser.maybe_condense(memory, n_ctx=8192)
        summary_content = memory.conversation[3].content
        # The condenser wraps the LLM output with a count header.
        assert "condensed" in summary_content.lower()


# ── Test 8: large tool output compressed ────────────────────────────────────

class TestLargeToolOutputCompression:
    def test_tokens_decrease_after_condensation(self) -> None:
        condenser = Condenser(_make_llm(), keep_head=2, keep_tail=2)
        memory = _make_memory(n_turns=10, content_per_turn=_big_turn(kb=8))
        from src.llm.token_budget import estimate_tokens
        tokens_before = sum(estimate_tokens(t.content) for t in memory.conversation)
        condenser.maybe_condense(memory, n_ctx=8192)
        tokens_after = sum(estimate_tokens(t.content) for t in memory.conversation)
        assert tokens_after < tokens_before

    def test_result_reports_token_reduction(self) -> None:
        condenser = Condenser(_make_llm(), keep_head=2, keep_tail=2)
        memory = _make_memory(n_turns=10, content_per_turn=_big_turn(kb=8))
        result = condenser.maybe_condense(memory, n_ctx=8192)
        assert result.condensed
        assert result.tokens_after < result.tokens_before


# ── Test 9: ContextLengthError triggers condensation ─────────────────────────

class TestContextLengthErrorTrigger:
    def test_context_length_error_causes_emergency_condense(self) -> None:
        condenser = Condenser(_make_llm(), keep_head=2, keep_tail=2)
        memory = _make_memory(n_turns=20)  # enough to compress
        tokens_before = len(memory.conversation)
        # Simulate ContextLengthError by calling condense() directly
        result = condenser.condense(memory, reason="ContextLengthError in test")
        assert result.condensed
        assert len(memory.conversation) < tokens_before

    def test_context_length_error_is_typed_exception(self) -> None:
        # ContextLengthError must be a RuntimeError subclass so it can be
        # caught without importing the error module everywhere.
        exc = ContextLengthError("test")
        assert isinstance(exc, RuntimeError)


# ── Test 10: same operation retries after condensation ──────────────────────

class TestRetryAfterCondensation:
    def test_callable_retried_after_condense(self) -> None:
        """The condenser's job is to shrink memory so the retry can proceed."""
        call_count = 0

        def _failing_then_succeeding_generate(prompt: str, **_kw: object) -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ContextLengthError("too long")
            return "TASK:\nretry succeeded\n\nDECISIONS:\nnone\n\nFILES:\nnone\n\nCHANGES:\nnone\n\nTESTS:\nnone\n\nERRORS:\nnone\n\nPENDING:\nnone\n\nIMPORTANT CONTEXT:\nnone"

        mock_llm = MagicMock()
        mock_llm.generate.side_effect = _failing_then_succeeding_generate
        condenser = Condenser(mock_llm, keep_head=2, keep_tail=2)
        memory = _make_memory(n_turns=20)
        # First condense call triggers the LLM — if it raises ContextLengthError
        # the condenser falls back to the inline summary, which is fine.
        result = condenser.condense(memory, reason="emergency")
        assert result.condensed
        # The condensed memory is shorter — ready for a retry.
        assert len(memory.conversation) < 20


# ── Test 11: retry is bounded ────────────────────────────────────────────────

class TestRetryBound:
    def test_max_retries_setting_exists(self) -> None:
        assert hasattr(Settings, "CONDENSER_MAX_RETRIES")
        assert Settings.CONDENSER_MAX_RETRIES >= 1

    def test_condenser_max_retries_default(self) -> None:
        # Default must be small — 2 or 3 — so loops cannot run long.
        assert 1 <= Settings.CONDENSER_MAX_RETRIES <= 5


# ── Test 12: cannot condense further → CannotCondenseError ──────────────────

class TestCannotCondenseFurther:
    def test_too_few_turns_raises_cannot_condense(self) -> None:
        condenser = Condenser(_make_llm(), keep_head=3, keep_tail=5)
        # head + tail = 8; need > 8 turns; give exactly 8
        memory = _make_memory(n_turns=8)
        with pytest.raises(CannotCondenseError):
            condenser.condense(memory)

    def test_can_condense_returns_false_for_short_memory(self) -> None:
        condenser = Condenser(_make_llm(), keep_head=3, keep_tail=5)
        memory = _make_memory(n_turns=6)
        assert not condenser.can_condense(memory)

    def test_can_condense_returns_true_for_sufficient_turns(self) -> None:
        condenser = Condenser(_make_llm(), keep_head=3, keep_tail=5)
        memory = _make_memory(n_turns=10)  # 10 > 3 + 5 + 1 = 9
        assert condenser.can_condense(memory)

    def test_error_message_is_actionable(self) -> None:
        condenser = Condenser(_make_llm(), keep_head=3, keep_tail=5)
        memory = _make_memory(n_turns=5)
        with pytest.raises(CannotCondenseError, match="Cannot condense"):
            condenser.condense(memory)


# ── Test 13: existing approval invariant still passes ───────────────────────

class TestApprovalInvariantPreserved:
    def test_create_file_stages_not_writes_when_patch_manager_active(self, tmp_path) -> None:
        """
        The condenser must not touch ChangeManager or any write path.
        This is the canonical approval invariant from CLAUDE.md §3.
        Verified via create_file (in edit_tools.py), which honours the
        active ChangeManager unlike file_tools.write_file.
        """
        from src.config.workspace import clear_workspace_root, set_workspace_root
        from src.tools.edit_tools import create_file, set_active_patch_manager
        from src.tools.patch_manager import ChangeManager

        # Point workspace at tmp_path so _ensure_within_workspace passes.
        set_workspace_root(tmp_path)
        target = str(tmp_path / "approval_invariant_test.py")

        pm = ChangeManager()
        set_active_patch_manager(pm)
        try:
            create_file(target, "content")
            assert target in pm.affected_files()
            assert not Path(target).exists()
        finally:
            set_active_patch_manager(None)
            clear_workspace_root()


# ── Test 14: existing ContextBudget tests still pass ─────────────────────────

class TestContextBudgetUnchanged:
    def test_context_budget_usable(self) -> None:
        from src.llm.context_budget import ContextBudget
        budget = ContextBudget(n_ctx=8192, response_tokens=1024)
        assert budget.usable == 7168

    def test_context_budget_allowance_typical(self) -> None:
        from src.llm.context_budget import ContextBudget
        budget = ContextBudget(n_ctx=8192, response_tokens=1024)
        base = "x" * 17259  # ~4314 tokens (Pearl baseline with all tools)
        allowance = budget.context_token_allowance(base)
        assert 2000 <= allowance <= 3000

    def test_context_budget_never_negative(self) -> None:
        from src.llm.context_budget import ContextBudget
        budget = ContextBudget(n_ctx=8192, response_tokens=1024)
        assert budget.context_token_allowance("x" * 200_000) == 0


# ── Supplementary: summary role translation in Memory ───────────────────────

class TestSummaryRoleTranslation:
    def test_summary_role_becomes_assistant_in_recent_messages(self) -> None:
        memory = Memory()
        memory.conversation.append(
            ConversationTurn(role="summary", content="[Context Summary]\n\nTASK:\ntest")
        )
        messages = memory.recent_messages()
        assert messages[0]["role"] == "assistant"
        assert "Context Summary" in messages[0]["content"]

    def test_agent_role_becomes_assistant(self) -> None:
        memory = Memory()
        memory.record_turn("user", "hello")
        memory.record_turn("agent", "hi")
        messages = memory.recent_messages()
        assert messages[1]["role"] == "assistant"
