"""
Tests for src/agent/context_engine.py — ContextEngine

Coverage:
- build() returns EngineContext with correct fields
- history is capped to the budget (oldest trimmed first)
- repository context truncated to budget
- execution results formatted correctly
- proactive condensation triggered when memory is large
- context_block combines repo + exec results
- tokens_used doesn't exceed budget
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from src.agent.condenser import CondensationResult
from src.agent.context_engine import ContextEngine, EngineContext
from src.repository.context import SemanticContextBuilder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_memory(messages: list[tuple[str, str]]) -> MagicMock:
    """Create a mock Memory with the given (role, content) pairs."""
    memory = MagicMock()
    memory.recent_messages.return_value = [
        {"role": r, "content": c} for r, c in messages
    ]
    return memory


def _make_condenser(condensed: bool = False) -> MagicMock:
    condenser = MagicMock()
    condenser.maybe_condense.return_value = CondensationResult(
        condensed=condensed,
        turns_before=10,
        turns_after=3 if condensed else 10,
        tokens_before=8000,
        tokens_after=3000 if condensed else 8000,
        reason="Condensed." if condensed else "",
    )
    return condenser


def _make_engine(
    messages: list[tuple[str, str]] | None = None,
    condensed: bool = False,
    context_service=None,
    n_ctx: int = 8192,
    context_builder=None,
) -> ContextEngine:
    memory = _make_memory(messages or [])
    condenser = _make_condenser(condensed=condensed)
    return ContextEngine(
        memory=memory,
        condenser=condenser,
        n_ctx=n_ctx,
        context_service=context_service,
        context_builder=context_builder,
    )


def _builder_returning(value=None, error: Exception | None = None):
    """
    A stand-in SemanticContextBuilder.

    Retrieval goes through the builder's ``build(query, service,
    workspace_memory)``, so that is what these tests mock. Mocking the
    service instead would pass against a method the real
    RepositoryService does not have.
    """

    builder = MagicMock(spec=SemanticContextBuilder)
    if error is not None:
        builder.build.side_effect = error
    else:
        builder.build.return_value = value
    return builder


# ---------------------------------------------------------------------------
# Basic build()
# ---------------------------------------------------------------------------


class TestBuild:
    def test_returns_engine_context(self) -> None:
        engine = _make_engine()
        ctx = engine.build(task="fix the bug")
        assert isinstance(ctx, EngineContext)

    def test_system_prompt_passed_through(self) -> None:
        engine = _make_engine()
        ctx = engine.build(task="fix the bug", system_prompt="You are Pearl.")
        assert ctx.system_prompt == "You are Pearl."

    def test_tokens_used_positive(self) -> None:
        engine = _make_engine(messages=[("user", "hello"), ("assistant", "hi")])
        ctx = engine.build(task="do something")
        assert ctx.tokens_used > 0

    def test_tokens_used_below_budget(self) -> None:
        engine = _make_engine(messages=[("user", "x" * 100)])
        ctx = engine.build(task="task")
        assert ctx.tokens_used <= ctx.tokens_budget

    def test_history_included(self) -> None:
        engine = _make_engine(messages=[("user", "hello"), ("assistant", "world")])
        ctx = engine.build(task="continue")
        assert len(ctx.history) == 2

    def test_was_condensed_false_by_default(self) -> None:
        engine = _make_engine()
        ctx = engine.build(task="task")
        assert ctx.was_condensed is False

    def test_was_condensed_true_when_condenser_fires(self) -> None:
        engine = _make_engine(condensed=True)
        ctx = engine.build(task="task")
        assert ctx.was_condensed is True
        assert ctx.condensation_result is not None


# ---------------------------------------------------------------------------
# History capping
# ---------------------------------------------------------------------------


class TestHistoryCapping:
    def test_large_history_capped_to_budget(self) -> None:
        # 100 messages each with 50 words — should hit the budget
        messages = [("user", "word " * 50)] * 100
        engine = _make_engine(messages=messages, n_ctx=4096)
        ctx = engine.build(task="task")
        # Not all 100 should fit
        assert len(ctx.history) < 100

    def test_oldest_messages_dropped(self) -> None:
        messages = [("user", f"message {i}") for i in range(20)]
        engine = _make_engine(messages=messages, n_ctx=4096)
        ctx = engine.build(task="task")
        if len(ctx.history) < 20:
            # Most recent should be kept
            assert ctx.history[-1]["content"] == "message 19"

    def test_small_history_fits_completely(self) -> None:
        messages = [("user", "hi"), ("assistant", "hello")]
        engine = _make_engine(messages=messages, n_ctx=8192)
        ctx = engine.build(task="task")
        assert len(ctx.history) == 2


# ---------------------------------------------------------------------------
# Repository context
# ---------------------------------------------------------------------------


class TestRepoContext:
    def test_repo_context_included_when_service_available(self) -> None:
        builder = _builder_returning("def foo(): pass")
        service = MagicMock()
        engine = _make_engine(
            context_service=service, n_ctx=8192, context_builder=builder
        )
        ctx = engine.build(task="fix foo")
        assert "def foo(): pass" in ctx.context_block
        builder.build.assert_called_once()
        assert builder.build.call_args.args[0] == "fix foo"
        assert builder.build.call_args.args[1] is service

    def test_repo_context_empty_when_no_service(self) -> None:
        engine = _make_engine(context_service=None)
        ctx = engine.build(task="fix foo")
        assert ctx.context_block == "" or "def foo" not in ctx.context_block

    def test_repo_context_error_handled_gracefully(self) -> None:
        builder = _builder_returning(error=RuntimeError("index unavailable"))
        engine = _make_engine(context_service=MagicMock(), context_builder=builder)
        ctx = engine.build(task="task")  # should not raise
        assert isinstance(ctx, EngineContext)

    def test_repo_context_truncated_to_budget(self) -> None:
        large_context = "x " * 10_000  # very large
        engine = _make_engine(
            context_service=MagicMock(),
            n_ctx=4096,
            context_builder=_builder_returning(large_context),
        )
        ctx = engine.build(task="task")
        # The context_block should be truncated, not the full 10k words
        tokens_in_block = len(ctx.context_block) // 4
        assert tokens_in_block < 10_000


# ---------------------------------------------------------------------------
# Execution results
# ---------------------------------------------------------------------------


class TestExecutionResults:
    def test_exec_results_in_context_block(self) -> None:
        engine = _make_engine()
        step = SimpleNamespace(tool_name="read_file", succeeded=True, summary="Read ok")
        ctx = engine.build(task="task", execution_results=[step])
        assert "read_file" in ctx.context_block

    def test_failed_step_marked(self) -> None:
        engine = _make_engine()
        step = SimpleNamespace(
            tool_name="write_file", succeeded=False, summary="Permission denied"
        )
        ctx = engine.build(task="task", execution_results=[step])
        assert "write_file" in ctx.context_block

    def test_no_exec_results_empty_block(self) -> None:
        service = MagicMock()
        service.get_context_for_task.return_value = ""
        engine = _make_engine(context_service=service)
        ctx = engine.build(task="task", execution_results=None)
        assert ctx.context_block == ""

    def test_at_most_five_steps_included(self) -> None:
        engine = _make_engine()
        steps = [
            SimpleNamespace(tool_name=f"tool_{i}", succeeded=True, summary=f"step {i}")
            for i in range(10)
        ]
        ctx = engine.build(task="task", execution_results=steps)
        # Only last 5 should appear
        assert "tool_9" in ctx.context_block
        assert "tool_0" not in ctx.context_block


# ---------------------------------------------------------------------------
# History overflow — the current request is never dropped
# ---------------------------------------------------------------------------


class TestHistoryOverflow:
    """
    _cap_history trims from the oldest end to fit a budget. It used to
    trim from the newest end too: the loop walked backwards and broke
    on the first message that did not fit, so a current request larger
    than the history slice of the budget returned an EMPTY history and
    Pearl planned against nothing at all. The one message that can
    never be dropped is the one the user just typed.
    """

    def test_newest_message_is_kept_when_it_alone_exceeds_the_budget(self) -> None:
        huge = "x" * 100_000
        messages = [("user", "older turn"), ("user", huge)]
        engine = _make_engine(messages=messages, n_ctx=2048)

        ctx = engine.build(task=huge)

        assert ctx.history, "history must never come back empty"
        assert ctx.history[-1]["content"] == huge

    def test_older_messages_are_dropped_before_the_newest_one(self) -> None:
        huge = "y" * 100_000
        messages = [("user", "a" * 5_000), ("user", "b" * 5_000), ("user", huge)]
        engine = _make_engine(messages=messages, n_ctx=2048)

        ctx = engine.build(task=huge)

        assert len(ctx.history) == 1
        assert ctx.history[0]["content"] == huge

    def test_repository_context_yields_to_an_oversized_request(self) -> None:
        """
        History overrunning its share must be charged somewhere, or the
        two together blow past n_ctx. Repository context is Pearl's own
        retrieval, so it is what gives way — not the user's words.
        """
        huge = "z" * 100_000
        builder = _builder_returning("REPO CONTEXT " * 500)
        engine = _make_engine(
            messages=[("user", huge)],
            n_ctx=2048,
            context_service=MagicMock(),
            context_builder=builder,
        )

        ctx = engine.build(task=huge)

        assert ctx.history[-1]["content"] == huge
        assert ctx.context_block == ""

    def test_repository_context_survives_a_normal_sized_request(self) -> None:
        builder = _builder_returning("REPO CONTEXT")
        engine = _make_engine(
            messages=[("user", "small question")],
            n_ctx=8192,
            context_service=MagicMock(),
            context_builder=builder,
        )

        ctx = engine.build(task="small question")

        assert "REPO CONTEXT" in ctx.context_block

    def test_empty_history_stays_empty(self) -> None:
        engine = _make_engine(messages=[], n_ctx=8192)
        assert engine.build(task="task").history == []
