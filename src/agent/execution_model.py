"""
The execution data model: what an autonomous run reports, and the pure
functions that shape it.

Split out of `executor.py`, which had grown to 1 751 lines mixing the
result types with the loop that produces them. A reader looking for what
an `ExecutionReport` contains had to scroll past the orchestration to
find it, and a change to the report shape sat in the same file as the
approval lifecycle.

Everything here is pure: dataclasses, type aliases, and functions that
derive text or a verdict from values passed in. Nothing touches disk,
the network, or executor state. That is the line to hold — if something
added here needs executor state, it belongs in `executor.py` instead.

`executor.py` re-exports all of it, so
`from src.agent.executor import ExecutionReport` keeps working.

This does NOT reduce import cost today, and it is worth knowing why
before someone assumes it did. `src/agent/__init__.py` eagerly imports
`PearlAgent`, which imports the executor, so importing any module under
`src.agent` still loads the whole agent stack. Making that lazy (PEP 562
`__getattr__`) would make this split pay off at import time as well, but
it changes the package's public loading behaviour and is its own change
with its own risk.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from src.agent.conversational import AmbiguousRequestError
from src.llm.parser import ToolCall

if TYPE_CHECKING:
    from src.agent.reflection import ReflectionResult as LLMReflectionResult


StopReason = Literal[
    "completed",
    "max_iterations",
    "fatal_error",
    "cancelled",
    "awaiting_approval",
    "rejected",
]

ReflectionOutcome = Literal["COMPLETE", "PARTIAL", "FAILED"]

ProgressStatus = Literal[
    "planning",
    "executing_step",
    "step_completed",
    "step_failed",
    "step_retrying",
    "replanning",
    "task_completed",
    # Distinct from task_completed so a client can tell success from
    # failure by status alone. Previously every terminal path emitted
    # task_completed and only the personality wording differed, so a
    # failed run rendered a green "Done" beside its own error.
    "task_failed",
    "cancelled",
    "awaiting_approval",
    "rejected",
    "checkpoint_created",
]

_SUMMARY_TRUNCATE = 200


@dataclass(slots=True)
class ProgressEvent:
    """
    A single, UI-independent progress notification emitted while an
    autonomous run is in flight.

    `total_steps` reflects the currently known plan length, which can
    change after a replan — it's a best-current-estimate, not a fixed
    upfront total.
    """

    status: ProgressStatus
    current_step: int
    total_steps: int
    current_action: str


def _summarize(
    tool_name: str,
    succeeded: bool,
    result: Any = None,
    error: str | None = None,
) -> str:
    """
    Produce a short, human-readable summary of one step's outcome.
    """

    if succeeded:
        text = str(result)

        if len(text) > _SUMMARY_TRUNCATE:
            text = text[:_SUMMARY_TRUNCATE] + "..."

        return f"'{tool_name}' succeeded: {text}"

    return f"'{tool_name}' failed: {error}"


def _reflect(
    steps: list[ExecutionStep],
    stop_reason: StopReason,
    replans_used: int,
) -> ReflectionResult:
    """
    Classify the outcome of an autonomous run as COMPLETE, PARTIAL, or
    FAILED from observable evidence — not step count alone.

    COMPLETE: the plan ran all the way through (stop_reason="completed"),
              regardless of whether replanning was needed along the way.
    PARTIAL:  execution stopped early but at least one step succeeded,
              meaning some real work was done.
    FAILED:   no steps completed successfully.
    """

    if stop_reason == "completed":
        evidence = [f"Plan ran to completion ({len(steps)} step(s))."]
        if replans_used:
            evidence.append(f"Recovered via {replans_used} replan(s).")
        return ReflectionResult(outcome="COMPLETE", evidence=evidence)

    succeeded = [s for s in steps if s.succeeded]
    failed = [s for s in steps if not s.succeeded]

    if succeeded:
        evidence = [
            f"{len(succeeded)} of {len(steps)} step(s) succeeded.",
            f"Stopped early: {stop_reason}.",
        ]
        if failed:
            evidence.append(f"Failed step(s): {[s.tool_name for s in failed]}.")
        return ReflectionResult(outcome="PARTIAL", evidence=evidence)

    return ReflectionResult(
        outcome="FAILED",
        evidence=[
            "No steps completed successfully.",
            f"Stopped: {stop_reason}.",
        ],
    )


def _describe_planning_failure(exc: Exception) -> str:
    """
    Turn a planning exception into something the user can act on.

    Planning failure is the one failure mode with no steps to point at,
    so whatever this returns is the entire explanation the user gets.
    "fatal_error" alone says nothing about what happened or what to try
    next, which is what a small model producing an unusable plan looked
    like from the outside.
    """

    detail = str(exc).strip()

    # Declining to guess is not a failure to plan. Its message is already
    # a question addressed to the user, so pass it through untouched
    # rather than wrapping it in failure language.
    if isinstance(exc, AmbiguousRequestError):
        return detail

    # A small model frequently emits prose or malformed JSON instead of a
    # plan. That is a limitation of the model, not a mistake by the user,
    # and saying so is more useful than echoing a parse error.
    if isinstance(exc, (ValueError, json.JSONDecodeError)) or "json" in detail.lower():
        return (
            "I couldn't turn that into a plan I could run. This usually "
            "means the request was too vague for the current model, or it "
            "isn't a coding task. Try rephrasing it as a concrete action — "
            "for example \"add a docstring to parse_config in src/utils.py\" "
            "— or switch to Chat mode to just talk."
        )

    if not detail:
        return (
            "Planning failed for an unknown reason. Try rephrasing the "
            "request, or switch to Chat mode."
        )

    return f"Planning failed: {detail}"


@dataclass(slots=True, repr=False)
class ExecutionStep:
    """
    The outcome of one iteration of the autonomous execution loop.
    """

    iteration: int
    tool_name: str
    kwargs: dict[str, Any]
    result: Any = None
    error: str | None = None
    summary: str = ""
    error_type: str | None = None  # "transient" | "validation" | "fatal" | None

    @property
    def succeeded(self) -> bool:
        """
        Return whether this step completed without error.
        """

        return self.error is None

    def __repr__(self) -> str:
        status = "ok" if self.succeeded else f"err={self.error!r:.40}"
        return f"ExecutionStep({self.tool_name}, iter={self.iteration}, {status})"


@dataclass(slots=True)
class ReflectionResult:
    """
    Post-run classification of what the autonomous run actually achieved,
    derived from observable evidence rather than step count alone.
    """

    outcome: ReflectionOutcome
    evidence: list[str]


@dataclass(slots=True)
class ExecutionReport:
    """
    The complete outcome of an autonomous run (or one leg of it, if
    it paused for approval): every step taken across any replans
    (the execution history), why the loop stopped, how many times it
    replanned, and any progress events emitted.
    """

    steps: list[ExecutionStep] = field(default_factory=list)
    stop_reason: StopReason = "completed"
    replans_used: int = 0
    events: list[ProgressEvent] = field(default_factory=list)
    reflection: ReflectionResult | None = None
    confidence_score: float | None = None
    # LLM-based structured reflection — richer than the heuristic `reflection`
    # above; only populated when a ReflectionEngine is wired into the executor.
    llm_reflection: "LLMReflectionResult | None" = None
    # Why the run failed, in terms a user can act on.
    #
    # A planning failure produces no steps, so a client that reports the
    # failed step has nothing to show and falls back to printing the
    # stop_reason — "fatal_error" tells the user nothing about what went
    # wrong or what to do next. The underlying exception used to be
    # logged and then discarded; this carries it out to the surface.
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        """
        Return whether the task completed (as opposed to stopping on
        a fatal error, the iteration cap, cancellation, rejection, or
        still awaiting approval).
        """

        return self.stop_reason == "completed"

    @property
    def step_count(self) -> int:
        """Total number of steps executed (across any replans)."""
        return len(self.steps)

    @property
    def succeeded_steps(self) -> list[ExecutionStep]:
        """Steps that completed without error."""
        return [s for s in self.steps if s.succeeded]

    @property
    def failed_steps(self) -> list[ExecutionStep]:
        """Steps that raised an error."""
        return [s for s in self.steps if not s.succeeded]


@dataclass(slots=True)
class _PausedState:
    """
    Everything needed to resume a run that paused for patch approval,
    exactly where it left off — no re-planning, no re-running
    completed steps.
    """

    prompt: str
    pending: list[ToolCall]
    steps: list[ExecutionStep]
    events: list[ProgressEvent]
    completed_for_replan: list[dict[str, Any]]
    replans_used: int
    iteration: int
