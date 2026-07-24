"""
Autonomous multi-step task execution for Pearl, with a reflection
loop.

After every tool execution, the outcome is evaluated and summarized.
A failed step doesn't immediately end the run: the Planner is asked
for a revised remaining plan (bounded by `max_replans`, to prevent
infinite replan loops), and execution continues with that plan.

Reuses `Planner` (for both the initial plan and any replans) and
`ToolDispatcher` (for execution) as-is; this module only adds the
loop, evaluation, and replanning orchestration around them.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from src.agent.dispatcher import ToolDispatcher
from src.agent.planner import Planner
from src.llm.parser import ToolCall

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS = 10
DEFAULT_MAX_REPLANS = 3

StopReason = Literal[
    "completed", "max_iterations", "fatal_error", "cancelled"
]

ProgressStatus = Literal[
    "planning",
    "executing_step",
    "step_completed",
    "step_failed",
    "replanning",
    "task_completed",
    "cancelled",
]

_SUMMARY_TRUNCATE = 200

# User-facing progress messages (fun, friendly tone). Purely cosmetic
# text passed as `ProgressEvent.current_action` — they carry no
# information used by the execution loop itself.
_MSG_PLANNING = "🗺️ Plotting world domination... I mean, your solution."
_MSG_EXECUTING_STEP = "🛠️ Hammering out some code..."
_MSG_STEP_COMPLETED = "✨ Looking much better now."
_MSG_STEP_FAILED = "😅 Well... that didn't work."
_MSG_REPLANNING = "🔄 Plot twist! Trying another approach..."
_MSG_TASK_COMPLETED_SUCCESS = "🎉 Done! No bugs were intentionally added."
_MSG_TASK_COMPLETED_FAILURE = (
    "💀 I fought bravely... but this one needs a human."
)
_MSG_CANCELLED = "🛑 Cancelled — stopping right where we are."


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


@dataclass(slots=True)
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

    @property
    def succeeded(self) -> bool:
        """
        Return whether this step completed without error.
        """

        return self.error is None


@dataclass(slots=True)
class ExecutionReport:
    """
    The complete outcome of an autonomous run: every step taken
    across any replans (the execution history), why the loop
    stopped, and how many times it replanned.
    """

    steps: list[ExecutionStep] = field(default_factory=list)
    stop_reason: StopReason = "completed"
    replans_used: int = 0
    events: list[ProgressEvent] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        """
        Return whether the task completed (as opposed to stopping on
        a fatal error or the iteration cap).
        """

        return self.stop_reason == "completed"


class AutonomousExecutor:
    """
    Executes a Planner-produced plan one step at a time, evaluating
    and summarizing each result, and asking the Planner to revise the
    remaining plan (up to `max_replans` times) whenever a step fails,
    instead of stopping immediately.

    Supports cooperative cancellation: `cancel()` may be called from
    any thread (e.g. a UI thread) while `run()` is executing on
    another. Cancellation is checked before every tool execution and
    before every replan — an in-flight tool call itself is never
    interrupted, but the loop stops at the next checkpoint.
    """

    def __init__(
        self,
        planner: Planner,
        dispatcher: ToolDispatcher,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        max_replans: int = DEFAULT_MAX_REPLANS,
        on_progress: Callable[[ProgressEvent], None] | None = None,
    ) -> None:
        self.planner = planner
        self.dispatcher = dispatcher
        self.max_iterations = max_iterations
        self.max_replans = max_replans
        self.on_progress = on_progress
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        """
        Request cancellation of the current (or next) `run()` call.

        Thread-safe and idempotent: safe to call from any thread,
        including concurrently with `run()`, and safe to call more
        than once.
        """

        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        """
        Return whether cancellation has been requested.
        """

        return self._cancel_event.is_set()

    def _check_cancelled(
        self,
        events: list[ProgressEvent],
        steps: list[ExecutionStep],
    ) -> bool:
        """
        If cancellation has been requested, emit the final
        `cancelled` progress event and return True.

        Callers must stop and return an
        `ExecutionReport(stop_reason="cancelled", ...)` immediately
        whenever this returns True, preserving `steps` and `events`
        exactly as accumulated so far.
        """

        if not self.is_cancelled():
            return False

        logger.info("Autonomous execution cancelled.")

        self._emit(
            events,
            "cancelled",
            current_step=len(steps),
            total_steps=len(steps),
            current_action=_MSG_CANCELLED,
        )

        return True

    def _emit(
        self,
        events: list[ProgressEvent],
        status: ProgressStatus,
        current_step: int,
        total_steps: int,
        current_action: str,
    ) -> None:
        """
        Record a progress event and, if a listener is attached, push
        it there in real time.

        A broken listener never breaks execution: any exception it
        raises is logged and swallowed.
        """

        event = ProgressEvent(
            status=status,
            current_step=current_step,
            total_steps=total_steps,
            current_action=current_action,
        )

        events.append(event)

        if self.on_progress is not None:
            try:
                self.on_progress(event)
            except Exception:
                logger.warning(
                    "Progress callback raised for '%s' event; ignoring.",
                    status,
                    exc_info=True,
                )

    def run(self, prompt: str) -> ExecutionReport:
        """
        Plan `prompt`, then execute steps one at a time until the
        task completes, a failure can't be recovered from (the
        replan budget is exhausted), or `max_iterations` is reached.
        """

        logger.info("Starting autonomous execution for: %s", prompt)

        steps: list[ExecutionStep] = []
        events: list[ProgressEvent] = []
        completed_for_replan: list[dict[str, Any]] = []
        replans_used = 0
        iteration = 0

        self._emit(
            events,
            "planning",
            current_step=0,
            total_steps=0,
            current_action=_MSG_PLANNING,
        )

        pending: list[ToolCall] = list(self.planner.plan(prompt))

        while pending:
            iteration += 1

            if iteration > self.max_iterations:
                logger.info(
                    "Autonomous execution stopped: max iterations "
                    "(%d) reached.",
                    self.max_iterations,
                )

                self._emit(
                    events,
                    "task_completed",
                    current_step=len(steps),
                    total_steps=len(steps),
                    current_action=_MSG_TASK_COMPLETED_FAILURE,
                )

                return ExecutionReport(
                    steps=steps,
                    stop_reason="max_iterations",
                    replans_used=replans_used,
                    events=events,
                )

            tool_call = pending.pop(0)

            if tool_call.tool_name == "none":
                logger.info(
                    "Step %d: no tool needed; task considered complete.",
                    iteration,
                )

                steps.append(
                    ExecutionStep(
                        iteration=iteration,
                        tool_name=tool_call.tool_name,
                        kwargs=tool_call.kwargs,
                        summary="No tool needed; task complete.",
                    )
                )

                break

            logger.info(
                "Step %d: executing '%s' with %s.",
                iteration,
                tool_call.tool_name,
                tool_call.kwargs,
            )

            if self._check_cancelled(events, steps):
                return ExecutionReport(
                    steps=steps,
                    stop_reason="cancelled",
                    replans_used=replans_used,
                    events=events,
                )

            self._emit(
                events,
                "executing_step",
                current_step=iteration,
                total_steps=iteration + len(pending),
                current_action=_MSG_EXECUTING_STEP,
            )

            try:
                result = self.dispatcher.execute(
                    tool_call.tool_name,
                    *tool_call.args,
                    **tool_call.kwargs,
                )
            except Exception as exc:
                error = str(exc)
                summary = _summarize(tool_call.tool_name, False, error=error)

                logger.error("Step %d: %s", iteration, summary)

                steps.append(
                    ExecutionStep(
                        iteration=iteration,
                        tool_name=tool_call.tool_name,
                        kwargs=tool_call.kwargs,
                        error=error,
                        summary=summary,
                    )
                )

                self._emit(
                    events,
                    "step_failed",
                    current_step=iteration,
                    total_steps=iteration + len(pending),
                    current_action=_MSG_STEP_FAILED,
                )

                if replans_used >= self.max_replans:
                    logger.info(
                        "Autonomous execution stopped: max replans "
                        "(%d) reached after '%s' failed.",
                        self.max_replans,
                        tool_call.tool_name,
                    )

                    self._emit(
                        events,
                        "task_completed",
                        current_step=len(steps),
                        total_steps=len(steps),
                        current_action=_MSG_TASK_COMPLETED_FAILURE,
                    )

                    return ExecutionReport(
                        steps=steps,
                        stop_reason="fatal_error",
                        replans_used=replans_used,
                        events=events,
                    )

                logger.info(
                    "Step %d: asking Planner for a revised plan "
                    "(replan %d/%d).",
                    iteration,
                    replans_used + 1,
                    self.max_replans,
                )

                if self._check_cancelled(events, steps):
                    return ExecutionReport(
                        steps=steps,
                        stop_reason="cancelled",
                        replans_used=replans_used,
                        events=events,
                    )

                self._emit(
                    events,
                    "replanning",
                    current_step=iteration,
                    total_steps=iteration + len(pending),
                    current_action=_MSG_REPLANNING,
                )

                try:
                    revised = self.planner.replan(
                        prompt,
                        completed=completed_for_replan,
                        failed={
                            "tool": tool_call.tool_name,
                            "arguments": tool_call.kwargs,
                            "error": error,
                        },
                    )
                except Exception as replan_exc:
                    logger.error(
                        "Step %d: replanning after '%s' failed: %s",
                        iteration,
                        tool_call.tool_name,
                        replan_exc,
                    )

                    self._emit(
                        events,
                        "task_completed",
                        current_step=len(steps),
                        total_steps=len(steps),
                        current_action=_MSG_TASK_COMPLETED_FAILURE,
                    )

                    return ExecutionReport(
                        steps=steps,
                        stop_reason="fatal_error",
                        replans_used=replans_used,
                        events=events,
                    )

                replans_used += 1
                pending = list(revised)

                logger.info(
                    "Step %d: replanned; %d step(s) remaining.",
                    iteration,
                    len(pending),
                )

                continue

            summary = _summarize(tool_call.tool_name, True, result=result)

            logger.info("Step %d: %s", iteration, summary)

            steps.append(
                ExecutionStep(
                    iteration=iteration,
                    tool_name=tool_call.tool_name,
                    kwargs=tool_call.kwargs,
                    result=result,
                    summary=summary,
                )
            )

            completed_for_replan.append(
                {
                    "tool": tool_call.tool_name,
                    "arguments": tool_call.kwargs,
                    "result": result,
                }
            )

            self._emit(
                events,
                "step_completed",
                current_step=iteration,
                total_steps=iteration + len(pending),
                current_action=_MSG_STEP_COMPLETED,
            )

        logger.info(
            "Autonomous execution finished: %d step(s) run (%d replan(s)).",
            len(steps),
            replans_used,
        )

        self._emit(
            events,
            "task_completed",
            current_step=len(steps),
            total_steps=len(steps),
            current_action=_MSG_TASK_COMPLETED_SUCCESS,
        )

        return ExecutionReport(
            steps=steps,
            stop_reason="completed",
            replans_used=replans_used,
            events=events,
        )
