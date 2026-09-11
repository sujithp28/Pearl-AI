"""
ContextEngine must actually retrieve repository context.

This was silently dead. `_build_repo_context` called
`service.get_context_for_task(task)` — a method RepositoryService has
never had. The AttributeError was caught by a broad `except Exception`
that logged at DEBUG and returned "", so every model call went out with
no repository context at all and nothing in the logs said so. The unit
tests passed because they mocked that same non-existent method.

These tests use a real RepositoryService over a real temporary
repository and a real SemanticContextBuilder. Nothing about retrieval is
mocked, so a regression to a method that does not exist fails here.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.agent.condenser import CondensationResult
from src.agent.context_engine import ContextEngine
from src.memory.memory import Memory
from src.repository.context import SemanticContextBuilder
from src.repository.service import RepositoryService


@pytest.fixture
def repo(tmp_path):
    """A small real repository with a distinctive symbol to retrieve."""
    (tmp_path / "auth.py").write_text(
        "def authenticate_user(username, password):\n"
        '    """Check a user\'s credentials."""\n'
        "    return username == password\n",
        encoding="utf-8",
    )
    (tmp_path / "unrelated.py").write_text(
        "def render_invoice_pdf(invoice):\n    return b''\n",
        encoding="utf-8",
    )
    RepositoryService.clear_cache()
    return tmp_path


def _engine(repo, **kwargs) -> ContextEngine:
    condenser = MagicMock()
    condenser.maybe_condense.return_value = CondensationResult(
        condensed=False,
        turns_before=0,
        turns_after=0,
        tokens_before=0,
        tokens_after=0,
        reason="",
    )
    return ContextEngine(
        memory=Memory(),
        condenser=condenser,
        n_ctx=8192,
        context_service=RepositoryService.get_or_build(repo),
        **kwargs,
    )


def test_repository_context_reaches_the_assembled_block(repo):
    ctx = _engine(repo).build(task="fix authenticate_user")

    assert ctx.context_block, "the engine returned no repository context at all"
    assert "authenticate_user" in ctx.context_block


def test_the_engine_calls_a_method_the_builder_really_has(repo):
    """
    The shape of the original defect: a call to a method that does not
    exist, swallowed by a broad except. Assert against the real class's
    surface rather than a MagicMock that answers to anything.
    """
    assert hasattr(SemanticContextBuilder, "build")
    assert not hasattr(RepositoryService, "get_context_for_task")


def test_retrieval_is_scoped_to_the_task(repo):
    ctx = _engine(repo).build(task="fix authenticate_user")

    assert "authenticate_user" in ctx.context_block
    assert "render_invoice_pdf" not in ctx.context_block


def test_an_unrelated_task_does_not_invent_context(repo):
    ctx = _engine(repo).build(task="zzzznothinglikethisexists")

    assert "authenticate_user" not in ctx.context_block


def test_the_engine_defaults_to_a_real_builder_when_none_is_given(repo):
    """
    Production wires the session's builder in, but the default must be
    a working one rather than None — otherwise forgetting to pass it
    silently disables retrieval again.
    """
    engine = _engine(repo)

    assert isinstance(engine._context_builder, SemanticContextBuilder)


def test_retrieval_failure_still_produces_a_usable_context(repo):
    """
    A broken index must degrade to no repository context, never to a
    failed turn.
    """
    builder = MagicMock(spec=SemanticContextBuilder)
    builder.build.side_effect = RuntimeError("index corrupt")

    ctx = _engine(repo, context_builder=builder).build(task="fix authenticate_user")

    assert ctx.context_block == ""
    assert ctx.tokens_budget > 0
