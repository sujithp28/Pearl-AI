"""
Autonomous multi-step task execution for Pearl, with a reflection
loop and patch-preview approval.

After every tool execution, the outcome is evaluated and summarized.
A failed step doesn't immediately end the run: the Planner is asked
for a revised remaining plan (bounded by `max_replans`, to prevent
infinite replan loops), and execution continues with that plan.

While a run is in progress, every editing tool (`create_file`,
`replace_in_file`, `edit_lines`, `patch_file`) stages its changes in
a `PatchManager` instead of writing to disk ("preview mode" — the
default here). Once the current plan runs out of steps (or would
otherwise stop) with edits still staged, execution pauses and
`run()`/`approve()` returns an `ExecutionReport` with
`stop_reason="awaiting_approval"` instead of finishing. Calling
`approve()` writes the staged edits and resumes exactly where
execution paused (no re-planning, no re-running completed steps);
calling `reject()` discards them and stops cleanly.

Reuses `Planner` (for both the initial plan and any replans) and
`ToolDispatcher` (for execution) as-is; this module only adds the
loop, evaluation, replanning, cancellation, and patch-approval
orchestration around them.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from src.agent.dispatcher import ToolDispatcher
from src.agent.planner import Planner
from src.llm.parser import ToolCall
from src.tools.edit_tools import set_active_patch_manager
from src.tools.patch_manager import PatchManager

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS = 10
DEFAULT_MAX_REPLANS = 3

StopReason = Literal[
    "completed",
    "max_iterations",
    "fatal_error",
    "cancelled",
    "awaiting_approval",
    "rejected",
]

ProgressStatus = Literal[
    "planning",
    "executing_step",
    "step_completed",
    "step_failed",
    "replanning",
    "task_completed",
    "cancelled",
    "awaiting_approval",
    "rejected",
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
_MSG_AWAITING_APPROVAL = "📄 Patch ready — take a look and let me know."
_MSG_REJECTED = "🗑️ No worries, discarding that patch."


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
    The complete outcome of an autonomous run (or one leg of it, if
    it paused for approval): every step taken across any replans
    (the execution history), why the loop stopped, how many times it
    replanned, and any progress events emitted.
    """

    steps: list[ExecutionStep] = field(default_factory=list)
    stop_reason: StopReason = "completed"
    replans_used: int = 0
    events: list[ProgressEvent] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        """
        Return whether the task completed (as opposed to stopping on
        a fatal error, the iteration cap, cancellation, rejection, or
        still awaiting approval).
        """

        return self.stop_reason == "completed"


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

    Also supports patch-preview approval: while running, editing
    tools stage their changes in `self.patch_manager` instead of
    writing to disk. Whenever the loop would otherwise stop with
    edits still staged, it pauses instead (`stop_reason=
    "awaiting_approval"`); `approve()` writes them and resumes,
    `reject()` discards them and stops cleanly.
    """

    def __init__(
        self,
        planner: Planner,
        dispatcher: ToolDispatcher,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        max_replans: int = DEFAULT_MAX_REPLANS,
        on_progress: Callable[[ProgressEvent], None] | None = None,
        patch_manager: PatchManager | None = None,
    ) -> None:
        self.planner = planner
        self.dispatcher = dispatcher
        self.max_iterations = max_iterations
        self.max_replans = max_replans
        self.on_progress = on_progress
        self.patch_manager = patch_manager or PatchManager()
        self._cancel_event = threading.Event()
        self._paused: _PausedState | None = None

    def cancel(self) -> None:
        """
        Request cancellation of the current (or next) `run()` call.

        Thread-safe and idempotent: safe to call from any thread,
        including concurrently with `run()`, and safe to call more
        than once. If execution is currently paused awaiting
        approval, the next `approve()` or `reject()` call finalizes
        it as cancelled (discarding any pending patches) instead of
        resuming or completing normally.
        """

        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        """
        Return whether cancellation has been requested.
        """

        return self._cancel_event.is_set()

    def is_awaiting_approval(self) -> bool:
        """
        Return whether execution is currently paused awaiting a call
        to `approve()` or `reject()`.
        """

        return self._paused is not None

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

    def _check_awaiting_approval(
        self,
        prompt: str,
        pending: list[ToolCall],
        steps: list[ExecutionStep],
        events: list[ProgressEvent],
        completed_for_replan: list[dict[str, Any]],
        replans_used: int,
        iteration: int,
    ) -> ExecutionReport | None:
        """
        If there are patches staged and not yet approved, pause: save
        everything needed to resume later, emit the final
        `awaiting_approval` progress event, and return the paused
        `ExecutionReport`. Otherwise return None (nothing pending).
        """

        if not self.patch_manager.has_pending():
            return None

        affected = self.patch_manager.affected_files()

        logger.info(
            "Autonomous execution paused: %d file(s) awaiting "
            "approval: %s",
            len(affected),
            affected,
        )

        self._paused = _PausedState(
            prompt=prompt,
            pending=pending,
            steps=steps,
            events=events,
            completed_for_replan=completed_for_replan,
            replans_used=replans_used,
            iteration=iteration,
        )

        self._emit(
            events,
            "awaiting_approval",
            current_step=len(steps),
            total_steps=len(steps) + len(pending),
            current_action=_MSG_AWAITING_APPROVAL,
        )

        return ExecutionReport(
            steps=steps,
            stop_reason="awaiting_approval",
            replans_used=replans_used,
            events=events,
        )

    def _finalize_cancelled_while_paused(
        self, state: _PausedState
    ) -> ExecutionReport:
        """
        Resolve a cancel() that arrived while execution was paused
        awaiting approval: discard the pending patches and finalize
        as cancelled, preserving the execution history collected so
        far.
        """

        discarded = self.patch_manager.discard_all()

        logger.info(
            "Cancelled while awaiting approval; discarded %d pending "
            "file(s): %s",
            len(discarded),
            discarded,
        )

        set_active_patch_manager(None)

        self._emit(
            state.events,
            "cancelled",
            current_step=len(state.steps),
            total_steps=len(state.steps),
            current_action=_MSG_CANCELLED,
        )

        return ExecutionReport(
            steps=state.steps,
            stop_reason="cancelled",
            replans_used=state.replans_used,
            events=state.events,
        )

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
        replan budget is exhausted), `max_iterations` is reached,
        execution is cancelled, or edits are staged and awaiting
        approval.
        """

        if self._paused is not None:
            raise RuntimeError(
                "Execution is already awaiting approval; call "
                "approve() or reject() first."
            )

        logger.info("Starting autonomous execution for: %s", prompt)

        steps: list[ExecutionStep] = []
        events: list[ProgressEvent] = []
        completed_for_replan: list[dict[str, Any]] = []

        set_active_patch_manager(self.patch_manager)

        self._emit(
            events,
            "planning",
            current_step=0,
            total_steps=0,
            current_action=_MSG_PLANNING,
        )

        pending: list[ToolCall] = list(self.planner.plan(prompt))

        return self._finish_or_pause(
            self._execute(
                prompt, pending, steps, events, completed_for_replan, 0, 0
            )
        )

    def approve(self) -> ExecutionReport:
        """
        Approve every currently staged patch: write it to disk, then
        resume execution exactly where it paused — no re-planning,
        no re-running already-completed steps.

        If cancellation was requested while paused, finalizes as
        cancelled (discarding the staged patches) instead of
        resuming.
        """

        if self._paused is None:
            raise RuntimeError(
                "No execution is currently awaiting approval."
            )

        state = self._paused
        self._paused = None

        if self.is_cancelled():
            return self._finalize_cancelled_while_paused(state)

        applied = self.patch_manager.apply_all()

        logger.info("Approved %d file(s): %s", len(applied), applied)

        set_active_patch_manager(self.patch_manager)

        return self._finish_or_pause(
            self._execute(
                state.prompt,
                state.pending,
                state.steps,
                state.events,
                state.completed_for_replan,
                state.replans_used,
                state.iteration,
            )
        )

    def reject(self) -> ExecutionReport:
        """
        Discard every currently staged patch and stop cleanly,
        preserving the execution history collected so far.

        If cancellation was requested while paused, finalizes as
        cancelled instead (the practical effect is the same: nothing
        is written).
        """

        if self._paused is None:
            raise RuntimeError(
                "No execution is currently awaiting approval."
            )

        state = self._paused
        self._paused = None

        if self.is_cancelled():
            return self._finalize_cancelled_while_paused(state)

        discarded = self.patch_manager.discard_all()

        logger.info("Rejected %d file(s): %s", len(discarded), discarded)

        set_active_patch_manager(None)

        self._emit(
            state.events,
            "rejected",
            current_step=len(state.steps),
            total_steps=len(state.steps),
            current_action=_MSG_REJECTED,
        )

        return ExecutionReport(
            steps=state.steps,
            stop_reason="rejected",
            replans_used=state.replans_used,
            events=state.events,
        )

    def _finish_or_pause(self, report: ExecutionReport) -> ExecutionReport:
        """
        Deactivate preview mode unless the report represents a pause
        (in which case a later `approve()` reactivates it).
        """

        if report.stop_reason != "awaiting_approval":
            set_active_patch_manager(None)

        return report

    def _execute(
        self,
        prompt: str,
        pending: list[ToolCall],
        steps: list[ExecutionStep],
        events: list[ProgressEvent],
        completed_for_replan: list[dict[str, Any]],
        replans_used: int,
        iteration: int,
    ) -> ExecutionReport:
        """
        Run the core execution loop starting from the given state.

        Shared by `run()` (starting fresh, iteration 0, an empty
        history) and `approve()` (resuming exactly where a prior
        `_execute()` call paused).
        """

        while pending:
            iteration += 1

            if iteration > self.max_iterations:
                logger.info(
                    "Autonomous execution stopped: max iterations "
                    "(%d) reached.",
                    self.max_iterations,
                )

                awaiting = self._check_awaiting_approval(
                    prompt,
                    pending,
                    steps,
                    events,
                    completed_for_replan,
                    replans_used,
                    iteration,
                )
                if awaiting is not None:
                    return awaiting

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

                    awaiting = self._check_awaiting_approval(
                        prompt,
                        pending,
                        steps,
                        events,
                        completed_for_replan,
                        replans_used,
                        iteration,
                    )
                    if awaiting is not None:
                        return awaiting

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

                    awaiting = self._check_awaiting_approval(
                        prompt,
                        pending,
                        steps,
                        events,
                        completed_for_replan,
                        replans_used,
                        iteration,
                    )
                    if awaiting is not None:
                        return awaiting

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

        awaiting = self._check_awaiting_approval(
            prompt, pending, steps, events, completed_for_replan,
            replans_used, iteration,
        )
        if awaiting is not None:
            return awaiting

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
