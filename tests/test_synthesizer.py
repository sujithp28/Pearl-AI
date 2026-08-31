"""
Regression tests for Milestone 1 — Synthesizer and repository-context wiring.

Tests 1-8 are unit tests (no LLM, no filesystem).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.agent.executor import ExecutionReport, ExecutionStep
from src.agent.synthesizer import Synthesizer, _format_results, _truncate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _step(tool: str, result=None, error=None, succeeded=True) -> ExecutionStep:
    s = ExecutionStep(iteration=1, tool_name=tool, kwargs={})
    s.result = result
    s.error = error
    s.summary = f"{tool} ok" if succeeded else f"{tool} failed: {error}"
    if error:
        s.error = error
    return s


def _mock_client(response: str = "Synthesized answer.") -> MagicMock:
    client = MagicMock()
    client.generate.return_value = response
    return client


def _report(*steps: ExecutionStep, stop_reason="completed") -> ExecutionReport:
    r = ExecutionReport(steps=list(steps), stop_reason=stop_reason)
    return r


# ---------------------------------------------------------------------------
# 1. Synthesis receives tool results
# ---------------------------------------------------------------------------

class TestSynthesizerReceivesResults:
    def test_tool_results_passed_to_llm(self):
        """The LLM prompt must contain the tool result text."""
        client = _mock_client("Answer.")
        syn = Synthesizer(client)
        step = _step("read_file", result="def main(): pass")
        report = _report(step)
        syn.synthesize("What does main do?", report)
        prompt = client.generate.call_args[0][0]
        assert "def main(): pass" in prompt

    def test_user_prompt_included_in_synthesis(self):
        """The original user request must appear in the synthesis prompt."""
        client = _mock_client("Answer.")
        syn = Synthesizer(client)
        report = _report(_step("search_code", result="found: main"))
        syn.synthesize("Find the main function", report)
        prompt = client.generate.call_args[0][0]
        assert "Find the main function" in prompt

    def test_synthesis_returns_llm_response(self):
        """Synthesizer must return the stripped LLM response."""
        client = _mock_client("  The answer is 42.  ")
        syn = Synthesizer(client)
        report = _report(_step("read_file", result="x = 42"))
        result = syn.synthesize("What is x?", report)
        assert result == "The answer is 42."


# ---------------------------------------------------------------------------
# 2. Conversational (no tool) path
# ---------------------------------------------------------------------------

class TestConversationalPath:
    def test_none_steps_excluded_from_results(self):
        """'none' tool steps must not appear in the evidence block."""
        client = _mock_client("Hello!")
        syn = Synthesizer(client)
        report = _report(_step("none"))
        syn.synthesize("hello", report)
        prompt = client.generate.call_args[0][0]
        assert "No tools were needed" in prompt or "conversationally" in prompt

    def test_empty_succeeded_steps_signals_conversational(self):
        """When all steps failed, _format_results signals no-tools path."""
        failed = _step("read_file", error="file not found", succeeded=False)
        failed.error = "file not found"
        report = _report(failed)
        text = _format_results(report)
        assert "No tools were used" in text or "No tools were needed" in text


# ---------------------------------------------------------------------------
# 3. Synthesis failure is handled gracefully
# ---------------------------------------------------------------------------

class TestSynthesisFailureHandling:
    def test_llm_exception_returns_empty_string(self):
        """Synthesis failure must return '' and not raise."""
        client = MagicMock()
        client.generate.side_effect = RuntimeError("LLM timeout")
        syn = Synthesizer(client)
        report = _report(_step("read_file", result="content"))
        result = syn.synthesize("explain this", report)
        assert result == ""

    def test_llm_exception_does_not_propagate(self):
        """Synthesis must never raise to the caller."""
        client = MagicMock()
        client.generate.side_effect = Exception("network error")
        syn = Synthesizer(client)
        report = _report(_step("search_code", result="found"))
        # Must not raise:
        syn.synthesize("find something", report)


# ---------------------------------------------------------------------------
# 4. Raw tool output is not the final answer (synthesis LLM is called)
# ---------------------------------------------------------------------------

class TestSynthesisIsCalledNotBypassed:
    def test_llm_called_when_tools_succeeded(self):
        """The synthesis LLM must be called for every run with tool results."""
        client = _mock_client("answer")
        syn = Synthesizer(client)
        report = _report(
            _step("list_directory", result="src/ tests/"),
            _step("read_file", result="# Pearl"),
        )
        syn.synthesize("explain repo", report)
        client.generate.assert_called_once()

    def test_result_truncation_keeps_evidence_bounded(self):
        """Very long tool results must be truncated to stay within token budget."""
        long_result = "x" * 5000
        text = _truncate(long_result, 1200)
        assert len(text) <= 1215  # 1200 chars + "…(truncated)" suffix
        assert text.endswith("…(truncated)")


# ---------------------------------------------------------------------------
# 5. Repository context reaches Planner (wiring check)
# ---------------------------------------------------------------------------

class TestRepositoryContextWiring:
    def test_executor_built_with_context_builder(self):
        """AutonomousExecutor must receive context_builder and context_service."""
        from src.agent.executor import AutonomousExecutor
        mock_builder = MagicMock()
        mock_service = MagicMock()
        mock_planner = MagicMock()
        mock_dispatcher = MagicMock()

        executor = AutonomousExecutor(
            mock_planner,
            mock_dispatcher,
            context_builder=mock_builder,
            context_service=mock_service,
        )
        assert executor._context_builder is mock_builder
        assert executor._context_service is mock_service

    def test_build_workspace_context_calls_builder(self):
        """_build_workspace_context must call builder.build when both are set."""
        from src.agent.executor import AutonomousExecutor
        mock_builder = MagicMock()
        mock_builder.build.return_value = "## Context"
        mock_service = MagicMock()
        mock_planner = MagicMock()
        mock_dispatcher = MagicMock()

        executor = AutonomousExecutor(
            mock_planner,
            mock_dispatcher,
            context_builder=mock_builder,
            context_service=mock_service,
        )
        result = executor._build_workspace_context("find ModelRouter")
        mock_builder.build.assert_called_once_with("find ModelRouter", mock_service)
        assert result == "## Context"

    def test_build_workspace_context_returns_empty_without_builder(self):
        """Without context_builder, _build_workspace_context returns ''."""
        from src.agent.executor import AutonomousExecutor
        executor = AutonomousExecutor(MagicMock(), MagicMock())
        assert executor._build_workspace_context("any prompt") == ""

    def test_build_workspace_context_fails_open(self):
        """A builder that raises must not block planning — return '' instead."""
        from src.agent.executor import AutonomousExecutor
        mock_builder = MagicMock()
        mock_builder.build.side_effect = RuntimeError("index not ready")
        mock_service = MagicMock()

        executor = AutonomousExecutor(
            MagicMock(), MagicMock(),
            context_builder=mock_builder,
            context_service=mock_service,
        )
        result = executor._build_workspace_context("prompt")
        assert result == ""
