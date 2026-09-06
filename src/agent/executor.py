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
default here), and `execute_shell` likewise stages its command in a
`CommandApprovalManager` instead of running it. Once the current plan
runs out of steps (or would otherwise stop) with edits and/or
commands still staged, execution pauses and `run()`/`approve()`
returns an `ExecutionReport` with `stop_reason="awaiting_approval"`
instead of finishing. Calling `approve()` writes the staged edits and
runs the staged commands, then resumes exactly where execution paused
(no re-planning, no re-running completed steps); calling `reject()`
discards them (writing and running nothing) and stops cleanly.

Reuses `Planner` (for both the initial plan and any replans) and
`ToolDispatcher` (for execution) as-is; this module only adds the
loop, evaluation, replanning, cancellation, and patch-approval
orchestration around them.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Literal

from src.agent.conversational import AmbiguousRequestError
from src.agent.dispatcher import ToolDispatcher
from src.agent.planner import Planner
from src.agent.retry import classify_error, is_transient_error
from src.agent.verification import VerificationEngine, VerificationStatus
from src.config.workspace import get_workspace_root
from src.llm.client import LLMCancelled
from src.llm.parser import ToolCall
from src.personality import EventKind, PersonalityManager
from src.tools.checkpoints import CheckpointManager
from src.tools.command_approval import CommandApprovalManager
from src.tools.edit_tools import set_active_patch_manager
from src.tools.patch_manager import PatchManager
from src.tools.repo_tools import refresh_indexed_file
from src.tools.shell_tools import _run_shell_command, set_active_command_approver

if TYPE_CHECKING:
    from src.agent.context_engine import ContextEngine
    from src.agent.reflection import ReflectionEngine as LLMReflectionEngine
    from src.agent.reflection import ReflectionResult as LLMReflectionResult
    from src.repository.context import SemanticContextBuilder
    from src.repository.service import RepositoryService

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS = 10
DEFAULT_MAX_REPLANS = 3
DEFAULT_MAX_RETRIES = 3

# Sentinel prefix returned by execute_shell / run_python when they stage a
# command rather than running it immediately.  Used in approve() to identify
# which completed ExecutionSteps need their result backfilled with the real
# command output once the approval actually runs the commands.
_STAGED_COMMAND_PREFIX = "Command staged for approval:"

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


class CheckpointCoordinator:
    """
    Manages checkpoint creation around approval writes.

    Extracted from AutonomousExecutor to group checkpoint-related
    logic as a named concern. All checkpoint creation goes through
    here so the policy (best-effort, swallow failures) is defined once.
    """

    def __init__(self, checkpoints: CheckpointManager | None) -> None:
        self.checkpoints = checkpoints

    def create_before_write(self, prompt: str) -> Any:
        """
        Record a restore point before a write batch reaches disk.

        Returns the created checkpoint object, or None if checkpointing
        is disabled, nothing new to capture, or creation failed.
        Swallows failures — a broken checkpoint must never block a write.
        """
        if self.checkpoints is None:
            return None
        try:
            return self.checkpoints.create(f"Before: {prompt[:72]}")
        except Exception:
            logger.warning(
                "Could not create a checkpoint; proceeding without undo "
                "for this change.",
                exc_info=True,
            )
            return None


class ApprovalCoordinator:
    """
    Manages the approval gate: patch staging, command staging, and
    the pause/resume lifecycle.

    Extracted from AutonomousExecutor to group all approval-related
    state as a named concern. AutonomousExecutor delegates to this
    for all PatchManager / CommandApprovalManager interactions.
    """

    def __init__(
        self,
        patch_manager: PatchManager,
        command_approver: CommandApprovalManager,
    ) -> None:
        self.patch_manager = patch_manager
        self.command_approver = command_approver
        self._paused: _PausedState | None = None

    def is_awaiting_approval(self) -> bool:
        return self._paused is not None

    def pause(self, state: _PausedState) -> None:
        self._paused = state

    def take_paused(self) -> _PausedState:
        """Pop and return the paused state (raises if not paused)."""
        if self._paused is None:
            raise RuntimeError("No execution is currently awaiting approval.")
        state = self._paused
        self._paused = None
        return state

    def discard_all(self) -> tuple[list[str], list[str]]:
        """Discard all staged patches and commands; return (files, cmds)."""
        files = self.patch_manager.discard_all()
        cmds = self.command_approver.discard_all()
        return files, cmds


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

    Also supports patch-preview and command approval: while running,
    editing tools stage their changes in `self.patch_manager`, and
    `execute_shell` stages its command in `self.command_approver`,
    instead of writing to disk / running. Whenever the loop would
    otherwise stop with edits and/or commands still staged, it pauses
    instead (`stop_reason="awaiting_approval"`); `approve()` writes
    the edits, runs the commands, and resumes; `reject()` discards
    both and stops cleanly.
    """

    def __init__(
        self,
        planner: Planner,
        dispatcher: ToolDispatcher,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        max_replans: int = DEFAULT_MAX_REPLANS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        on_progress: Callable[[ProgressEvent], None] | None = None,
        patch_manager: PatchManager | None = None,
        command_approver: CommandApprovalManager | None = None,
        personality: PersonalityManager | None = None,
        checkpoints: CheckpointManager | None = None,
        context_builder: "SemanticContextBuilder | None" = None,
        context_service: "RepositoryService | None" = None,
        context_engine: "ContextEngine | None" = None,
        verifier: VerificationEngine | None = None,
        reflection_engine: "LLMReflectionEngine | None" = None,
    ) -> None:
        self.planner = planner
        self.dispatcher = dispatcher
        self.max_iterations = max_iterations
        self.max_replans = max_replans
        self.max_retries = max_retries
        self.on_progress = on_progress

        # Build concrete PatchManager / CommandApprovalManager instances first
        # so coordinators and backward-compat attributes point to the same objects.
        _pm = patch_manager if patch_manager is not None else PatchManager()
        _ca = (
            command_approver
            if command_approver is not None
            else CommandApprovalManager(runner=_run_shell_command)
        )
        _ck = checkpoints if checkpoints is not None else CheckpointManager()

        # Coordinator layer: named concerns extracted from the executor body.
        self.approval_coordinator = ApprovalCoordinator(_pm, _ca)
        self.checkpoint_coordinator = CheckpointCoordinator(_ck)

        # Backward-compat direct attributes — same objects as coordinators hold.
        self.patch_manager = _pm
        self.command_approver = _ca
        self.checkpoints = _ck

        # Only ever shapes the wording of ProgressEvent.current_action
        # below — never anything the planner, dispatcher, or any tool
        # sees or acts on.
        self._personality = personality or PersonalityManager()
        self._cancel_event = threading.Event()
        self._initial_confidence_score: float | None = None
        self._current_prompt: str = ""  # set by run(); used by _finish_or_pause for LLM reflection
        # ContextEngine is preferred — builds token-budgeted workspace context.
        # Falls back to the legacy SemanticContextBuilder when not provided.
        self._context_engine = context_engine
        self._context_builder = context_builder
        self._context_service = context_service
        # Optional post-apply verification engine — when present, runs
        # tests after approve() and replans on test failures (bounded by
        # max_replans like any other replan).
        self._verifier = verifier
        self._reflection_engine = reflection_engine
        # Verification evidence from the most recent approve(), fed to the
        # reflection engine so reflection judges from test results rather
        # than tool-success alone. None until a verified approve() happens.
        self._last_verification: dict[str, Any] | None = None
        # Live replan history of the in-flight _execute() call, so a
        # reflection-driven replan can tell the planner what is already done
        # (with arguments intact, which ExecutionStep alone would not give).
        self._last_completed_for_replan: list[dict[str, Any]] = []

    def _log_structured(self, event: str, **fields: Any) -> None:
        """
        Emit a structured JSON log record at DEBUG level.

        Each record is a JSON object with at least an ``event`` key.
        These records are parsed by log aggregators and monitoring
        pipelines; the human-readable INFO/ERROR calls remain unchanged
        and are not replaced by this method.
        """

        logger.debug(json.dumps({"event": event, **fields}))

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

        return self.approval_coordinator.is_awaiting_approval()

    # Backward-compat proxy so tests and external code that read/write
    # executor._paused directly still work after the coordinator refactor.
    @property
    def _paused(self) -> "_PausedState | None":
        return self.approval_coordinator._paused

    @_paused.setter
    def _paused(self, value: "_PausedState | None") -> None:
        self.approval_coordinator._paused = value

    def _build_workspace_context(self, prompt: str) -> str:
        """
        Return a semantic context string for `prompt`.

        Prefers ContextEngine (token-budgeted, history-aware) when available.
        Falls back to the legacy SemanticContextBuilder path.
        Returns "" on any error so a context failure never blocks planning.
        """
        if self._context_engine is not None:
            try:
                return self._context_engine.build(task=prompt).context_block
            except Exception:
                logger.warning(
                    "ContextEngine.build failed; falling back to legacy builder.",
                    exc_info=True,
                )

        if self._context_builder is None or self._context_service is None:
            return ""

        try:
            return self._context_builder.build(prompt, self._context_service)
        except Exception:
            logger.warning(
                "Semantic context build failed; proceeding without context.",
                exc_info=True,
            )
            return ""

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
            current_action=self._personality.format(EventKind.CANCELLED),
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
        If there are patches and/or commands staged and not yet
        approved, pause: save everything needed to resume later, emit
        the final `awaiting_approval` progress event, and return the
        paused `ExecutionReport`. Otherwise return None (nothing
        pending).
        """

        patches_pending = self.patch_manager.has_pending()
        commands_pending = self.command_approver.has_pending()

        if not patches_pending and not commands_pending:
            return None

        affected = self.patch_manager.affected_files()
        affected_commands = self.command_approver.affected_commands()

        logger.info(
            "Autonomous execution paused: %d file(s) and %d command(s) "
            "awaiting approval: %s | %s",
            len(affected),
            len(affected_commands),
            affected,
            affected_commands,
        )

        self.approval_coordinator.pause(_PausedState(
            prompt=prompt,
            pending=pending,
            steps=steps,
            events=events,
            completed_for_replan=completed_for_replan,
            replans_used=replans_used,
            iteration=iteration,
        ))

        self._emit(
            events,
            "awaiting_approval",
            current_step=len(steps),
            total_steps=len(steps) + len(pending),
            current_action=self._personality.format(EventKind.APPROVAL),
        )

        return ExecutionReport(
            steps=steps,
            stop_reason="awaiting_approval",
            replans_used=replans_used,
            events=events,
        )

    def _finalize_cancelled_while_paused(self, state: _PausedState) -> ExecutionReport:
        """
        Resolve a cancel() that arrived while execution was paused
        awaiting approval: discard the pending patches and commands
        and finalize as cancelled, preserving the execution history
        collected so far.
        """

        discarded = self.patch_manager.discard_all()
        discarded_commands = self.command_approver.discard_all()

        logger.info(
            "Cancelled while awaiting approval; discarded %d pending "
            "file(s) and %d command(s): %s | %s",
            len(discarded),
            len(discarded_commands),
            discarded,
            discarded_commands,
        )

        set_active_patch_manager(None)
        set_active_command_approver(None)

        self._emit(
            state.events,
            "cancelled",
            current_step=len(state.steps),
            total_steps=len(state.steps),
            current_action=self._personality.format(EventKind.CANCELLED),
        )

        return ExecutionReport(
            steps=state.steps,
            stop_reason="cancelled",
            replans_used=state.replans_used,
            events=state.events,
            reflection=_reflect(state.steps, "cancelled", state.replans_used),
            confidence_score=self._initial_confidence_score,
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

    def _warn_if_dirty_workspace(self) -> None:
        """
        Log a warning when there are uncommitted workspace changes before
        an autonomous run starts.

        This mirrors Aider's dirty-commit detection: existing changes
        are NOT part of this task and should not be confused with the
        ones Pearl is about to propose. Non-fatal — never blocks the run.
        """
        try:
            # Pin to the active workspace: without cwd this reports the
            # *process* directory's repo, which is a different project
            # entirely whenever Pearl runs against an external workspace.
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(get_workspace_root()),
            )
            if result.returncode == 0 and result.stdout.strip():
                logger.warning(
                    "Autonomous run started with uncommitted workspace changes. "
                    "These pre-existing changes will not be affected by this task "
                    "unless the task explicitly stages them. "
                    "Consider committing or stashing them first.\n%s",
                    result.stdout.strip()[:400],
                )
        except Exception:
            pass  # Not a git repo, or git unavailable — non-fatal

    def run(self, prompt: str) -> ExecutionReport:
        """
        Plan `prompt`, then execute steps one at a time until the
        task completes, a failure can't be recovered from (the
        replan budget is exhausted), `max_iterations` is reached,
        execution is cancelled, or edits are staged and awaiting
        approval.
        """

        if self.approval_coordinator.is_awaiting_approval():
            raise RuntimeError(
                "Execution is already awaiting approval; call "
                "approve() or reject() first."
            )

        self._current_prompt = prompt
        logger.info("Starting autonomous execution for: %s", prompt)
        self._log_structured("run_start", prompt=prompt[:200])

        steps: list[ExecutionStep] = []
        events: list[ProgressEvent] = []
        completed_for_replan: list[dict[str, Any]] = []

        self._warn_if_dirty_workspace()

        set_active_patch_manager(self.patch_manager)
        set_active_command_approver(self.command_approver)

        self._emit(
            events,
            "planning",
            current_step=0,
            total_steps=0,
            current_action=self._personality.format(EventKind.PLANNING),
        )

        # Checked immediately before the call, not just after: a
        # cancel() that arrives while this is the *only* work
        # outstanding (nothing has executed yet, so there is no later
        # checkpoint to catch it) must not be silently ignored.
        if self._check_cancelled(events, steps):
            return ExecutionReport(
                steps=steps,
                stop_reason="cancelled",
                replans_used=0,
                events=events,
                reflection=_reflect(steps, "cancelled", 0),
            )

        workspace_context = self._build_workspace_context(prompt)

        try:
            pending = self._plan_with_retry(prompt, workspace_context, events, steps)
        except LLMCancelled:
            self._check_cancelled(events, steps)
            return ExecutionReport(
                steps=steps,
                stop_reason="cancelled",
                replans_used=0,
                events=events,
                reflection=_reflect(steps, "cancelled", 0),
            )
        except Exception as plan_exc:
            # Both planning attempts failed — surface the error clearly.
            logger.error("Planning failed after retry: %s", plan_exc)
            self._emit(
                events,
                "task_failed",
                current_step=0,
                total_steps=0,
                current_action=self._personality.format(EventKind.FAILURE),
            )
            set_active_patch_manager(None)
            set_active_command_approver(None)
            return ExecutionReport(
                steps=steps,
                stop_reason="fatal_error",
                replans_used=1,
                events=events,
                error=_describe_planning_failure(plan_exc),
                reflection=_reflect(steps, "fatal_error", 1),
            )

        self._initial_confidence_score = self.planner.last_confidence_score

        return self._finish_or_pause(
            self._execute(prompt, pending, steps, events, completed_for_replan, 0, 0)
        )

    def approve(self) -> ExecutionReport:
        """
        Approve every currently staged patch and command: write the
        patches to disk, run the commands, then resume execution
        exactly where it paused — no re-planning, no re-running
        already-completed steps.

        If cancellation was requested while paused, finalizes as
        cancelled (discarding the staged patches and commands)
        instead of resuming.
        """

        state = self.approval_coordinator.take_paused()

        if self.is_cancelled():
            return self._finalize_cancelled_while_paused(state)

        # Snapshot *before* anything reaches disk, so this approval is
        # undoable. Best-effort: a workspace where checkpointing can't
        # work (no git binary, unwritable directory) must still be able
        # to approve changes — losing undo is a degradation, refusing
        # the write would be a regression.
        checkpoint = self.checkpoint_coordinator.create_before_write(state.prompt)
        if checkpoint is not None:
            self._emit(
                state.events,
                "checkpoint_created",
                current_step=state.iteration,
                total_steps=state.iteration + len(state.pending),
                current_action=self._personality.format(EventKind.CHECKPOINT),
            )

        applied = self.patch_manager.apply_all()

        logger.info("Approved %d file(s): %s", len(applied), applied)

        for applied_path in applied:
            refresh_indexed_file(applied_path)

        command_count = len(self.command_approver.pending)
        try:
            command_results = self.command_approver.approve_all()
        except subprocess.CalledProcessError as exc:
            logger.error("Approved command failed with non-zero exit: %s", exc)
            command_results = []

        logger.info("Approved and ran %d command(s).", command_count)

        # Backfill: steps that staged commands recorded the pre-approval
        # placeholder as their result.  Now that the commands have actually
        # run, replace those placeholders with the real stdout so that
        # the execution report and the reflection phase see useful output.
        result_iter = iter(command_results)
        for step in state.steps:
            if isinstance(step.result, str) and step.result.startswith(
                _STAGED_COMMAND_PREFIX
            ):
                cmd_result = next(result_iter, None)
                if cmd_result is not None:
                    real_out = (
                        cmd_result.stdout.strip() or f"(exit {cmd_result.returncode})"
                    )
                    step.result = real_out
                    step.summary = _summarize(step.tool_name, True, result=real_out)

        # Keep completed_for_replan in sync so any subsequent replan sees
        # real results rather than staging placeholders.
        result_iter2 = iter(command_results)
        for entry in state.completed_for_replan:
            if isinstance(entry.get("result"), str) and entry["result"].startswith(
                _STAGED_COMMAND_PREFIX
            ):
                cmd_result = next(result_iter2, None)
                if cmd_result is not None:
                    entry["result"] = (
                        cmd_result.stdout.strip() or f"(exit {cmd_result.returncode})"
                    )

        # Verification replan: if tests fail after applying, feed the
        # failure back to the planner so remaining steps can be revised.
        # Bounded by max_replans like any other recovery path.
        if self._verifier is not None and applied:
            vr = self._verifier.verify(applied)
            # Retain as reflection evidence: reflection must judge from test
            # results and unexpected changes, not from tool success alone.
            # Full shape so callers can surface it without re-running tests.
            self._last_verification = {
                "status": getattr(vr.status, "value", str(vr.status)),
                "risk": getattr(vr.risk, "value", str(vr.risk)),
                "confidence": round(vr.confidence, 2),
                "planned_files": list(vr.planned_files),
                "changed_files": list(vr.changed_files),
                "unexpected_files": list(vr.unexpected_files),
                "tests_run": vr.tests_run,
                "tests_passed": vr.tests_passed,
                "tests_failed": vr.tests_failed,
                "diff_summary": vr.diff_summary,
                "evidence": [str(e) for e in vr.evidence],
            }
            if (
                vr.tests_failed > 0
                and state.replans_used < self.max_replans
                and not self.is_cancelled()
            ):
                self._emit(
                    state.events,
                    "replanning",
                    current_step=state.iteration,
                    total_steps=state.iteration + len(state.pending),
                    current_action=self._personality.format(EventKind.REPLANNING),
                )
                test_summary = "\n".join(str(e) for e in vr.evidence[-5:])
                try:
                    revised = self.planner.replan(
                        state.prompt,
                        completed=state.completed_for_replan,
                        failed={
                            "tool": "run_tests",
                            "error": (
                                f"{vr.tests_failed} test(s) failed after applying "
                                f"changes.\n{test_summary[:600]}"
                            ),
                            "error_type": "test_failure",
                            "suggestion": (
                                "Fix the failing tests. Do not repeat steps already "
                                "completed."
                            ),
                        },
                        cancel_check=self.is_cancelled,
                    )
                    state.pending = list(revised)
                    state.replans_used += 1
                    logger.info(
                        "Verification replan: %d test(s) failed; "
                        "replanned with %d new step(s).",
                        vr.tests_failed,
                        len(state.pending),
                    )
                except Exception as exc:
                    logger.warning(
                        "Verification replan failed (%s); proceeding with "
                        "original pending steps.",
                        exc,
                    )

        set_active_patch_manager(self.patch_manager)
        set_active_command_approver(self.command_approver)

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
        Discard every currently staged patch and command and stop
        cleanly, preserving the execution history collected so far.

        If cancellation was requested while paused, finalizes as
        cancelled instead (the practical effect is the same: nothing
        is written or run).
        """

        state = self.approval_coordinator.take_paused()

        if self.is_cancelled():
            return self._finalize_cancelled_while_paused(state)

        discarded, discarded_commands = self.approval_coordinator.discard_all()

        logger.info(
            "Rejected %d file(s) and %d command(s): %s | %s",
            len(discarded),
            len(discarded_commands),
            discarded,
            discarded_commands,
        )

        set_active_patch_manager(None)
        set_active_command_approver(None)

        self._emit(
            state.events,
            "rejected",
            current_step=len(state.steps),
            total_steps=len(state.steps),
            current_action=self._personality.format(EventKind.REJECTED),
        )

        return ExecutionReport(
            steps=state.steps,
            stop_reason="rejected",
            replans_used=state.replans_used,
            events=state.events,
            confidence_score=self._initial_confidence_score,
        )

    def _finish_or_pause(self, report: ExecutionReport) -> ExecutionReport:
        """
        Deactivate preview/approval mode unless the report represents
        a pause (in which case a later `approve()` reactivates it).

        For every terminal path (completed, cancelled, fatal_error,
        max_iterations) any patches or commands still staged are
        discarded here — the canonical discard point for the
        mid-execution cancel case, where _check_cancelled() returns
        early before _check_awaiting_approval() can run.
        """

        report.confidence_score = self._initial_confidence_score

        if report.stop_reason == "awaiting_approval":
            return report

        # LLM-based structured reflection — only on natural completion so
        # cancelled/fatal runs don't waste a model call.  Runs BEFORE teardown
        # so a "replan" verdict can resume execution with staging still active.
        if (
            self._reflection_engine is not None
            and report.stop_reason in ("completed", "max_iterations")
            and not self.is_cancelled()
        ):
            try:
                report.llm_reflection = self._reflection_engine.reflect(
                    self._current_prompt,
                    report.steps,
                    verification=self._last_verification,
                )
                logger.info(
                    "LLM reflection: status=%s confidence=%.2f",
                    report.llm_reflection.status,
                    report.llm_reflection.confidence,
                )
            except Exception as exc:
                logger.warning("LLM reflection failed (non-fatal): %s", exc)

            # REFLECT → REPLAN edge.  Bounded three ways: the replan budget,
            # the reflection engine's own iteration cap, and cancellation.
            refl = report.llm_reflection
            if (
                refl is not None
                and refl.should_replan
                and report.replans_used < self.max_replans
                and not self._reflection_engine.exhausted
                and not self.is_cancelled()
            ):
                resumed = self._replan_from_reflection(report, refl)
                if resumed is not None:
                    return self._finish_or_pause(resumed)

        # Terminal path — tear down staging and record heuristic reflection.
        self.patch_manager.discard_all()
        self.command_approver.discard_all()
        set_active_patch_manager(None)
        set_active_command_approver(None)
        report.reflection = _reflect(
            report.steps, report.stop_reason, report.replans_used
        )

        return report

    def _replan_from_reflection(
        self,
        report: ExecutionReport,
        refl: "LLMReflectionResult",
    ) -> ExecutionReport | None:
        """
        Replan from a reflection verdict of "replan" and resume execution.

        Returns the resumed report, or None when replanning could not
        produce a revised plan — the caller then finalizes `report` as-is
        rather than looping.
        """

        missing = "; ".join(refl.missing_requirements) or refl.reason

        self._emit(
            report.events,
            "replanning",
            current_step=len(report.steps),
            total_steps=len(report.steps),
            current_action=self._personality.format(EventKind.REPLANNING),
        )

        completed = self._last_completed_for_replan

        try:
            revised = list(self.planner.replan(
                self._current_prompt,
                completed=completed,
                failed={
                    "tool": "reflection",
                    "error": f"Task judged incomplete: {refl.reason}",
                    "error_type": "incomplete_task",
                    "suggestion": (
                        f"Address the remaining requirements: {missing}. "
                        "Do not repeat steps already completed."
                    ),
                },
                cancel_check=self.is_cancelled,
            ))
        except Exception as exc:
            logger.warning(
                "Reflection-driven replan failed (%s); finalizing as-is.", exc
            )
            return None

        if not revised:
            logger.info("Reflection replan produced no steps; finalizing as-is.")
            return None

        logger.info(
            "Reflection replan: %d new step(s) for remaining work: %s",
            len(revised),
            missing[:120],
        )

        set_active_patch_manager(self.patch_manager)
        set_active_command_approver(self.command_approver)

        return self._execute(
            self._current_prompt,
            revised,
            report.steps,
            report.events,
            completed,
            report.replans_used + 1,
            len(report.steps),
        )

    def _plan_with_retry(
        self,
        prompt: str,
        workspace_context: str,
        events: list[ProgressEvent],
        steps: list[ExecutionStep],
    ) -> list[ToolCall]:
        """
        Attempt to produce a valid plan with one retry on failure.

        If the first attempt fails (bad JSON, invalid tool, validation
        error), the error is fed back to the model as a replanning prompt
        and one more attempt is made.  A second failure propagates to the
        caller.

        ``LLMCancelled`` is never retried — it propagates immediately.
        ``AmbiguousRequestError`` is not retried either: the request did
        not say what to do, and asking the model a second time only
        raises the odds it invents something.
        """
        try:
            return list(self.planner.plan(
                prompt,
                workspace_context=workspace_context,
                cancel_check=self.is_cancelled,
            ))
        except (LLMCancelled, AmbiguousRequestError):
            raise
        except Exception as first_exc:
            logger.warning(
                "Plan attempt 1 failed (%s: %s); retrying with error feedback.",
                type(first_exc).__name__,
                first_exc,
            )
            self._log_structured(
                "plan_retry",
                error=str(first_exc)[:200],
                error_type=type(first_exc).__name__,
            )
            self._emit(
                events,
                "replanning",
                current_step=0,
                total_steps=0,
                current_action=self._personality.format(EventKind.REPLANNING),
            )
            # Feed the validation failure back to the model as a replan prompt.
            return list(self.planner.replan(
                prompt,
                completed=[],
                failed={
                    "tool": "planning",
                    "error": str(first_exc)[:400],
                    "error_type": "validation",
                    "suggestion": (
                        "Your previous plan was malformed or invalid. "
                        "Return a fresh, correctly structured JSON plan."
                    ),
                },
                cancel_check=self.is_cancelled,
            ))

    @staticmethod
    def _recovery_suggestion(tool_name: str, exc: Exception) -> str:
        """
        Return a one-sentence hint the replanner can include in the next plan.

        Kept narrow: only produces suggestions for failure modes that are both
        common (file not found, wrong extension, permission denied) and where a
        specific next-step exists. Everything else returns an empty string so
        the replanner isn't polluted with generic advice.
        """
        msg = str(exc).lower()
        if tool_name in ("read_file", "write_file", "replace_in_file", "edit_lines"):
            if "no such file" in msg or "not found" in msg or "does not exist" in msg:
                return (
                    "The target file was not found. Use search_code to locate "
                    "the correct path before attempting another file operation."
                )
            if "permission" in msg or "access" in msg or "workspace" in msg.lower():
                return (
                    "The path is outside the workspace boundary. "
                    "Use list_files to discover valid paths."
                )
        if tool_name == "search_code" and ("invalid" in msg or "regex" in msg):
            return "The search pattern was invalid. Simplify the pattern and retry."
        return ""

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

        Retry counts are local to each `_execute()` call — they reset
        on `approve()` resumption. This is intentional: after the user
        approves staged changes we start fresh on retries for the
        remaining steps, which have not yet been attempted.
        """

        # Expose the live replan history so _finish_or_pause() can hand it to
        # the planner on a reflection-driven replan.
        self._last_completed_for_replan = completed_for_replan

        # Per-step transient retry counter, keyed by id() of the ToolCall
        # object. id() is safe here because we re-insert the exact same
        # object when retrying, so the identity is stable across attempts.
        # After a replan the new ToolCall objects have different ids, so
        # old retry counts for a failed step cannot pollute the new plan.
        _retry_counts: dict[int, int] = {}

        # Counts how many times each (tool_name, serialised_kwargs) pair
        # has failed with a fatal error across all replans.  When the same
        # action fails twice, replanning cannot help — stop to prevent loops.
        _failed_action_counts: dict[tuple[str, str], int] = {}

        while pending:
            iteration += 1

            if iteration > self.max_iterations:
                logger.info(
                    "Autonomous execution stopped: max iterations (%d) reached.",
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
                    "task_failed",
                    current_step=len(steps),
                    total_steps=len(steps),
                    current_action=self._personality.format(EventKind.FAILURE),
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
            self._log_structured(
                "step_start",
                iteration=iteration,
                tool=tool_call.tool_name,
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
                current_action=self._personality.format(EventKind.EXECUTING),
            )

            try:
                result = self.dispatcher.execute(
                    tool_call.tool_name,
                    *tool_call.args,
                    **tool_call.kwargs,
                )
            except Exception as exc:
                # Transient errors are retried before the replan path.
                # The same ToolCall object is re-queued at the front of
                # pending and iteration is decremented so the retry does
                # not consume an iteration slot or a replan slot.
                retry_count = _retry_counts.get(id(tool_call), 0)

                if is_transient_error(exc) and retry_count < self.max_retries:
                    _retry_counts[id(tool_call)] = retry_count + 1
                    pending.insert(0, tool_call)
                    iteration -= 1

                    logger.warning(
                        "Step %d: '%s' failed with transient error "
                        "(attempt %d/%d); retrying: %s",
                        iteration + 1,
                        tool_call.tool_name,
                        retry_count + 1,
                        self.max_retries,
                        exc,
                    )
                    self._log_structured(
                        "step_retry",
                        iteration=iteration + 1,
                        tool=tool_call.tool_name,
                        attempt=retry_count + 1,
                    )

                    self._emit(
                        events,
                        "step_retrying",
                        current_step=iteration,
                        total_steps=iteration + len(pending),
                        current_action=self._personality.format(EventKind.WARNING),
                    )

                    continue

                error_type = classify_error(exc)
                error = str(exc)
                summary = _summarize(tool_call.tool_name, False, error=error)

                logger.error("Step %d: %s", iteration, summary)
                self._log_structured(
                    "step_failure",
                    iteration=iteration,
                    tool=tool_call.tool_name,
                    error_type=error_type,
                    error=error[:200],
                )

                steps.append(
                    ExecutionStep(
                        iteration=iteration,
                        tool_name=tool_call.tool_name,
                        kwargs=tool_call.kwargs,
                        error=error,
                        summary=summary,
                        error_type=error_type,
                    )
                )

                self._emit(
                    events,
                    "step_failed",
                    current_step=iteration,
                    total_steps=iteration + len(pending),
                    current_action=self._personality.format(EventKind.WARNING),
                )

                # Repeated-action guard: if the same tool+arguments fails
                # twice with a fatal error, replanning cannot help — the
                # model would just produce the same broken step again.
                try:
                    _action_key = (
                        tool_call.tool_name,
                        json.dumps(
                            {k: str(v) for k, v in sorted(tool_call.kwargs.items())},
                            sort_keys=True,
                        ),
                    )
                except Exception:
                    _action_key = (tool_call.tool_name, str(tool_call.kwargs))

                _failed_action_counts[_action_key] = (
                    _failed_action_counts.get(_action_key, 0) + 1
                )

                if _failed_action_counts[_action_key] >= 2:
                    logger.warning(
                        "Step %d: '%s' with the same arguments has failed "
                        "%d times; aborting to prevent a replan loop.",
                        iteration,
                        tool_call.tool_name,
                        _failed_action_counts[_action_key],
                    )
                    self._log_structured(
                        "repeated_action_abort",
                        iteration=iteration,
                        tool=tool_call.tool_name,
                        fail_count=_failed_action_counts[_action_key],
                    )

                    awaiting = self._check_awaiting_approval(
                        prompt, pending, steps, events,
                        completed_for_replan, replans_used, iteration,
                    )
                    if awaiting is not None:
                        return awaiting

                    self._emit(
                        events,
                        "task_failed",
                        current_step=len(steps),
                        total_steps=len(steps),
                        current_action=self._personality.format(EventKind.FAILURE),
                    )
                    return ExecutionReport(
                        steps=steps,
                        stop_reason="fatal_error",
                        replans_used=replans_used,
                        events=events,
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
                        "task_failed",
                        current_step=len(steps),
                        total_steps=len(steps),
                        current_action=self._personality.format(EventKind.FAILURE),
                    )

                    return ExecutionReport(
                        steps=steps,
                        stop_reason="fatal_error",
                        replans_used=replans_used,
                        events=events,
                    )

                logger.info(
                    "Step %d: asking Planner for a revised plan (replan %d/%d).",
                    iteration,
                    replans_used + 1,
                    self.max_replans,
                )
                self._log_structured(
                    "replan_start",
                    iteration=iteration,
                    replan_number=replans_used + 1,
                    failed_tool=tool_call.tool_name,
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
                    current_action=self._personality.format(EventKind.REPLANNING),
                )

                try:
                    suggestion = self._recovery_suggestion(tool_call.tool_name, exc)
                    revised = self.planner.replan(
                        prompt,
                        completed=completed_for_replan,
                        failed={
                            "tool": tool_call.tool_name,
                            "arguments": tool_call.kwargs,
                            "error": error,
                            "error_type": error_type,
                            **({"suggestion": suggestion} if suggestion else {}),
                        },
                        cancel_check=self.is_cancelled,
                        # Pass the 1-indexed replan number so the confidence
                        # score reflects that this plan is being generated
                        # after N prior failures.
                        replans_used=replans_used + 1,
                    )
                except LLMCancelled:
                    self._check_cancelled(events, steps)
                    return ExecutionReport(
                        steps=steps,
                        stop_reason="cancelled",
                        replans_used=replans_used,
                        events=events,
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
                        "task_failed",
                        current_step=len(steps),
                        total_steps=len(steps),
                        current_action=self._personality.format(EventKind.FAILURE),
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
            self._log_structured(
                "step_success",
                iteration=iteration,
                tool=tool_call.tool_name,
            )

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
                current_action=self._personality.format(EventKind.SUCCESS),
            )

        logger.info(
            "Autonomous execution finished: %d step(s) run (%d replan(s)).",
            len(steps),
            replans_used,
        )
        self._log_structured(
            "run_complete",
            steps_total=len(steps),
            replans_used=replans_used,
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
            current_action=self._personality.format(EventKind.COMPLETED),
        )

        return ExecutionReport(
            steps=steps,
            stop_reason="completed",
            replans_used=replans_used,
            events=events,
        )
