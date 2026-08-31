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

    def test_all_none_steps_signals_conversational(self):
        """When all steps are 'none', _format_results signals conversational."""
        report = _report(_step("none"))
        text = _format_results(report)
        assert "No tools were used" in text or "No tools were needed" in text or "conversationally" in text


class TestFailedStepsInEvidence:
    def test_failed_step_labelled_in_evidence(self):
        """Failed steps must appear in the evidence block with [FAILED] label."""
        failed = _step("write_file", error="permission denied", succeeded=False)
        failed.error = "permission denied"
        report = _report(failed)
        text = _format_results(report)
        assert "[FAILED]" in text
        assert "permission denied" in text

    def test_mixed_success_and_failure_both_included(self):
        """Both succeeded and failed steps appear in the evidence block."""
        ok = _step("read_file", result="content here")
        fail = _step("write_file", error="disk full", succeeded=False)
        fail.error = "disk full"
        report = _report(ok, fail)
        text = _format_results(report)
        assert "content here" in text
        assert "[FAILED]" in text
        assert "disk full" in text

    def test_only_failed_steps_not_conversational(self):
        """When only failed steps exist, we still show them (not the no-tools message)."""
        failed = _step("read_file", error="file not found", succeeded=False)
        failed.error = "file not found"
        report = _report(failed)
        text = _format_results(report)
        assert "[FAILED]" in text
        assert "No tools were used" not in text and "No tools were needed" not in text


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


# ---------------------------------------------------------------------------
# 6. Verification data reaches synthesis (G2)
# ---------------------------------------------------------------------------

class TestVerificationPassedToSynthesis:
    def test_verification_status_included_in_prompt(self):
        """When verification is passed, its status must appear in the synthesis prompt."""
        client = _mock_client("Done.")
        syn = Synthesizer(client)
        report = _report(_step("write_file", result="written"))
        verification = {"status": "SUCCESS", "detail": "All tests passed."}

        syn.synthesize("fix the bug", report, verification=verification)

        prompt = client.generate.call_args[0][0]
        assert "SUCCESS" in prompt
        assert "All tests passed" in prompt

    def test_no_verification_omits_section(self):
        """When verification is None, the prompt must not contain a verification header."""
        client = _mock_client("Done.")
        syn = Synthesizer(client)
        report = _report(_step("read_file", result="content"))

        syn.synthesize("explain code", report, verification=None)

        prompt = client.generate.call_args[0][0]
        # The verification_section placeholder should be empty — no stray text.
        assert "Verification result:" not in prompt

    def test_verification_failure_included(self):
        """A FAILED verification must appear in the prompt so the model can report it."""
        client = _mock_client("The tests failed.")
        syn = Synthesizer(client)
        report = _report(_step("write_file", result="written"))
        verification = {
            "status": "FAILED",
            "detail": "2 tests failed: test_auth, test_login",
        }

        syn.synthesize("implement login", report, verification=verification)

        prompt = client.generate.call_args[0][0]
        assert "FAILED" in prompt
        assert "test_auth" in prompt
