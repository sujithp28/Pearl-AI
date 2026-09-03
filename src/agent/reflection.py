"""
LLM-based Reflection Engine for Pearl V2.

Reflection runs AFTER verification and determines whether the task
actually completed.  It produces structured output that the executor
uses to decide whether to stop, retry, or replan.

Loop position::

    PLAN → EXECUTE → VERIFY → REFLECT → REPLAN/RETRY → VERIFY → REFLECT → DONE

The engine never bypasses validation or approval.  It only answers the
question "did the task succeed?" from observable evidence.

Max iterations are bounded (default: Settings.REFLECTION_MAX_ITERATIONS).

Structured output::

    {
        "status": "complete" | "retry" | "replan" | "blocked",
        "confidence": 0.85,
        "reason": "All tests pass. login.py was modified as requested.",
        "missing_requirements": ["update docstring"],
        "recommended_action": "Task complete — no further action needed."
    }
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

from src.config.settings import Settings

logger = logging.getLogger(__name__)

ReflectionStatus = Literal["complete", "retry", "replan", "blocked"]


@dataclass(slots=True)
class ReflectionResult:
    """Structured output from one reflection pass."""

    status: ReflectionStatus
    confidence: float
    reason: str
    missing_requirements: list[str]
    recommended_action: str
    raw: dict[str, Any]  # preserve LLM output for debugging

    @property
    def is_done(self) -> bool:
        return self.status == "complete" and self.confidence >= 0.7

    @property
    def should_replan(self) -> bool:
        return self.status == "replan"

    @property
    def is_blocked(self) -> bool:
        return self.status == "blocked"


_FALLBACK_COMPLETE = ReflectionResult(
    status="complete",
    confidence=0.5,
    reason="Reflection unavailable — defaulting to tool-success outcome.",
    missing_requirements=[],
    recommended_action="Review manually.",
    raw={},
)

_FALLBACK_FAILED = ReflectionResult(
    status="blocked",
    confidence=0.0,
    reason="Reflection unavailable and execution did not complete.",
    missing_requirements=[],
    recommended_action="Retry from scratch.",
    raw={},
)

_PROMPT_TEMPLATE = """\
You are the Reflection Engine for Pearl, an autonomous coding agent.

Your job: determine whether the autonomous run actually completed the user's request.

## Original request
{request}

## Execution steps ({step_count} total, {succeeded} succeeded, {failed} failed)
{steps_summary}

## Verification result
{verification}

## Instructions
Answer ONLY with valid JSON matching this exact schema:
{{
  "status": "complete" | "retry" | "replan" | "blocked",
  "confidence": <float 0.0-1.0>,
  "reason": "<one-sentence explanation>",
  "missing_requirements": ["<item>", ...],
  "recommended_action": "<what should happen next>"
}}

Rules:
- "complete": task is done, tests pass (if applicable), files changed correctly
- "retry": a transient error occurred; the same plan should be retried
- "replan": the plan was wrong or incomplete; a new plan is needed
- "blocked": the task cannot be completed (missing info, impossible requirement)
- confidence reflects how certain you are
- missing_requirements lists specific things still needed (empty if complete)

Do NOT add explanation outside the JSON block.
"""


class ReflectionEngine:
    """
    Produces structured reflection on task completion.

    Parameters
    ----------
    llm_client:
        Any object with a ``generate(prompt, max_new_tokens, temperature)``
        method.  Typically ``ModelRouter().chat_client()``.
    max_iterations:
        Maximum reflection+replan cycles before giving up.  Defaults to
        ``Settings.REFLECTION_MAX_ITERATIONS``.
    """

    def __init__(
        self,
        llm_client: object,  # LLMClient — duck-typed to avoid circular import
        max_iterations: int | None = None,
    ) -> None:
        self._client = llm_client
        self._max_iter = (
            max_iterations
            if max_iterations is not None
            else Settings.REFLECTION_MAX_ITERATIONS
        )
        self._iteration = 0

    @property
    def iterations_remaining(self) -> int:
        return max(0, self._max_iter - self._iteration)

    @property
    def exhausted(self) -> bool:
        return self._iteration >= self._max_iter

    def reset(self) -> None:
        self._iteration = 0

    def reflect(
        self,
        request: str,
        steps: list[Any],          # list[ExecutionStep] — avoid import cycle
        verification: dict[str, Any] | None = None,
    ) -> ReflectionResult:
        """
        Run one reflection pass.

        Parameters
        ----------
        request:
            Original user request.
        steps:
            ExecutionStep objects from the executor.
        verification:
            VerificationResult dict (from session._run_verification).
            None when verification was not run.
        """
        self._iteration += 1

        # Determine fallback based on observable success before any LLM call.
        succeeded = [s for s in steps if getattr(s, "succeeded", False)]
        all_succeeded = len(succeeded) == len(steps) and steps

        prompt = self._build_prompt(request, steps, verification)

        try:
            raw_response: str = self._client.generate(  # type: ignore[attr-defined]
                prompt,
                max_new_tokens=300,
                temperature=0.1,
            )
            result = self._parse(raw_response)
            logger.info(
                "ReflectionEngine [iter=%d]: status=%s confidence=%.2f",
                self._iteration,
                result.status,
                result.confidence,
            )
            return result
        except Exception as exc:
            logger.warning(
                "ReflectionEngine LLM call failed (iter=%d): %s — using heuristic fallback",
                self._iteration,
                exc,
            )
            return _FALLBACK_COMPLETE if all_succeeded else _FALLBACK_FAILED

    # ── Private ──────────────────────────────────────────────────────────────

    def _build_prompt(
        self,
        request: str,
        steps: list[Any],
        verification: dict[str, Any] | None,
    ) -> str:
        succeeded = sum(1 for s in steps if getattr(s, "succeeded", False))
        failed = len(steps) - succeeded

        steps_summary_parts: list[str] = []
        for i, s in enumerate(steps[-10:], 1):  # cap at last 10 for brevity
            tool = getattr(s, "tool_name", "?")
            ok = "✓" if getattr(s, "succeeded", False) else "✗"
            summary = getattr(s, "summary", "")[:200]
            steps_summary_parts.append(f"{i}. {ok} {tool}: {summary}")
        steps_summary = "\n".join(steps_summary_parts) or "(no steps)"

        if verification:
            ver_lines = [
                f"Status: {verification.get('status', 'unknown')}",
                f"Tests run: {verification.get('tests_run', 0)}, "
                f"passed: {verification.get('tests_passed', 0)}, "
                f"failed: {verification.get('tests_failed', 0)}",
            ]
            if verification.get("unexpected_files"):
                ver_lines.append(f"Unexpected files: {verification['unexpected_files']}")
            if verification.get("evidence"):
                ver_lines.append(f"Evidence: {'; '.join(list(verification['evidence'])[:3])}")
            ver_text = "\n".join(ver_lines)
        else:
            ver_text = "(verification not run)"

        return _PROMPT_TEMPLATE.format(
            request=request[:500],
            step_count=len(steps),
            succeeded=succeeded,
            failed=failed,
            steps_summary=steps_summary,
            verification=ver_text,
        )

    def _parse(self, raw: str) -> ReflectionResult:
        """Extract structured JSON from LLM output."""
        # Strip markdown code fences if present
        text = raw.strip()
        for fence in ("```json", "```"):
            if text.startswith(fence):
                text = text[len(fence):]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        # Find JSON object boundaries
        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError("No JSON object found in reflection output")

        data = json.loads(text[start:end])

        status = data.get("status", "blocked")
        if status not in ("complete", "retry", "replan", "blocked"):
            status = "blocked"

        return ReflectionResult(
            status=status,
            confidence=float(data.get("confidence", 0.0)),
            reason=str(data.get("reason", "")),
            missing_requirements=list(data.get("missing_requirements", [])),
            recommended_action=str(data.get("recommended_action", "")),
            raw=data,
        )
