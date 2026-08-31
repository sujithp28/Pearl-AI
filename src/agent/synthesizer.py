"""
Final-answer synthesizer for Pearl's autonomous loop.

After tool execution completes, Synthesizer makes one LLM call that turns
the collected tool results into a coherent, grounded Markdown answer.

Responsibility boundary:
  Planner  — decides which tools to call
  Executor — runs those tools and collects results
  Synthesizer — turns results into the final human-readable answer

The synthesizer never sees the raw plan and never executes tools.
It receives only (user_prompt, ExecutionReport) and returns a string.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.agent.executor import ExecutionReport
    from src.llm.client import LLMClient

logger = logging.getLogger(__name__)

_PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompts" / "synthesis.txt"

# Per-result character cap — keeps total synthesis prompt inside 2048-token budget.
_MAX_RESULT_CHARS = 1200
# Max number of tool results to include (most recent succeeded steps first).
_MAX_RESULTS = 6


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…(truncated)"


def _format_results(report: "ExecutionReport") -> str:
    """
    Build a compact evidence block from succeeded tool steps.
    Excludes 'none' steps (conversational no-ops).
    """
    succeeded = [
        s for s in report.steps
        if s.succeeded and s.tool_name != "none"
    ]

    if not succeeded:
        return "(No tools were used — respond conversationally.)"

    # Most recent results last (execution order), capped to _MAX_RESULTS.
    capped = succeeded[-_MAX_RESULTS:]
    parts: list[str] = []
    for step in capped:
        result_text = str(step.result) if step.result is not None else step.summary
        parts.append(
            f"Tool: {step.tool_name}\n"
            f"Result: {_truncate(result_text, _MAX_RESULT_CHARS)}"
        )

    return "\n\n---\n\n".join(parts)


class Synthesizer:
    """
    Calls the LLM once to turn collected tool results into a final answer.

    Uses the chat LLM client (not the planning client) because this is
    prose generation, not JSON planning.
    """

    def __init__(self, client: "LLMClient") -> None:
        self.client = client
        self._prompt_template = _PROMPT_FILE.read_text(encoding="utf-8")

    def synthesize(self, user_prompt: str, report: "ExecutionReport") -> str:
        """
        Return a Markdown answer grounded in report's tool results.

        Returns an empty string on failure so callers can fall back
        gracefully — never raises.
        """
        tool_results = _format_results(report)

        prompt = self._prompt_template.format(
            user_prompt=user_prompt,
            tool_results=tool_results,
        )

        try:
            answer = self.client.generate(prompt)
            return answer.strip()
        except Exception as exc:
            logger.warning("Synthesis LLM call failed: %s", exc, exc_info=True)
            return ""
