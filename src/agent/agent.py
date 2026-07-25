"""
Pearl Agent.

Main entry point for the AI Coding Agent.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from src.agent.dispatcher import ToolDispatcher
from src.agent.executor import (
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_MAX_REPLANS,
    AutonomousExecutor,
    ExecutionReport,
    ProgressEvent,
)
from src.agent.planner import Planner
from src.config.settings import Settings
from src.llm.client import LLMClient
from src.memory import Memory
from src.prompts.system import build_chat_system_prompt
from src.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class PearlAgent:
    """
    Main Pearl AI Coding Agent.

    `run_autonomous()` is the entry point for executing a request
    end-to-end: it plans, executes each step, replans on failure, and
    pauses for patch-preview approval before any file write reaches
    disk. When it pauses (`ExecutionReport.stop_reason ==
    "awaiting_approval"`), call `approve()` or `reject()` to resolve
    it before starting another request.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        memory: Memory | None = None,
    ) -> None:

        self.registry = registry

        self.dispatcher = ToolDispatcher(registry)

        self.llm = LLMClient()

        self.planner = Planner(registry, self.dispatcher, self.llm)

        self.memory = memory or Memory()

        # The in-flight AutonomousExecutor, kept across calls so a
        # paused run can be resolved by a later approve()/reject() —
        # mirrors how MCPServer holds one across pearl/runAutonomous
        # and pearl/approvePatches|rejectPatches being separate calls.
        self._executor: AutonomousExecutor | None = None
        self._task_id: str | None = None
        # How many of the current run's ExecutionReport.steps have
        # already been recorded into Memory — steps accumulate across
        # a pause/resume rather than resetting, so this prevents
        # double-recording the pre-pause steps when approve()/reject()
        # finalizes.
        self._recorded_step_count: int = 0

        logger.info("Pearl Agent initialized.")

    def execute_tool(
        self,
        tool_name: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        Execute a tool directly.
        """

        logger.info("Executing tool: %s", tool_name)

        return self.dispatcher.execute(
            tool_name,
            *args,
            **kwargs,
        )

    def chat(
        self,
        prompt: str,
    ) -> str:
        """
        Chat directly with the language model, with the session's
        recent conversation history included so replies can follow
        the thread rather than treating every message as the first.

        This bypasses tool execution and is intended for
        conversational use.
        """

        logger.info("Chat request received.")

        # Captured before recording this turn — see the same note in
        # `MCPServer._chat`: recording first would send `prompt` twice.
        history = self.memory.recent_messages(limit=Settings.CHAT_HISTORY_TURNS)

        self.memory.record_turn("user", prompt)

        response = self.llm.generate(
            prompt,
            history=history,
            system=build_chat_system_prompt(str(Path.cwd().resolve())),
        )

        self.memory.record_turn("agent", response)

        return response

    def run_autonomous(
        self,
        prompt: str,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        max_replans: int = DEFAULT_MAX_REPLANS,
        on_progress: Callable[[ProgressEvent], None] | None = None,
    ) -> ExecutionReport:
        """
        Autonomously execute a multi-step task. Plans once via the
        existing `Planner`, then runs each step in turn via the
        existing `ToolDispatcher`, evaluating and summarizing the
        result after every step. A failed step triggers a request to
        the Planner for a revised remaining plan (up to `max_replans`
        times) instead of stopping immediately. Stops on completion,
        an unrecoverable failure, `max_iterations`, or — if edits
        and/or commands are staged when the plan would otherwise
        finish — pauses with `stop_reason="awaiting_approval"`.

        A paused run must be resolved with `approve()` or `reject()`
        before starting another one; calling this again first raises.

        Workflow

            User
              │
              ▼
             Planner (plan once)
              │
              ▼
        [ ToolCall, ToolCall, ... ]
              │
              ▼
         AutonomousExecutor (evaluate + summarize + replan loop)
              │
              ▼
          ExecutionReport
        """

        if self._executor is not None and self._executor.is_awaiting_approval():
            raise RuntimeError(
                "An autonomous run is already awaiting approval; call "
                "approve() or reject() first."
            )

        logger.info("Starting autonomous execution for: %s", prompt)

        self.memory.record_turn("user", prompt)

        task = self.memory.start_task(prompt)
        self._task_id = task.id
        self._recorded_step_count = 0

        self._executor = AutonomousExecutor(
            self.planner,
            self.dispatcher,
            max_iterations=max_iterations,
            max_replans=max_replans,
            on_progress=on_progress,
        )

        try:
            report = self._executor.run(prompt)
        except Exception:
            self.memory.complete_task(task.id, status="failed")
            raise

        return self._record_report(report)

    def approve(self) -> ExecutionReport:
        """
        Approve every patch and command currently staged by the
        paused run started by `run_autonomous()`: write the patches to
        disk, run the commands, then resume execution exactly where it
        paused (no re-planning, no re-running already-completed
        steps).
        """

        executor = self._require_awaiting_approval()

        return self._record_report(executor.approve())

    def reject(self) -> ExecutionReport:
        """
        Discard every patch and command currently staged by the
        paused run started by `run_autonomous()` — writing and running
        nothing — and stop the run.
        """

        executor = self._require_awaiting_approval()

        return self._record_report(executor.reject())

    def _require_awaiting_approval(self) -> AutonomousExecutor:
        if self._executor is None or not self._executor.is_awaiting_approval():
            raise RuntimeError("No autonomous run is currently awaiting approval.")

        return self._executor

    def _record_report(self, report: ExecutionReport) -> ExecutionReport:
        """
        Record any steps not already recorded into Memory, and — once
        the run has reached a terminal state — complete the task and
        record a summary turn.

        Safe to call after `run_autonomous()`, `approve()`, or
        `reject()`: `ExecutionReport.steps` accumulates across a
        pause/resume rather than resetting on each call, so only the
        steps beyond `self._recorded_step_count` (new since the last
        call) are recorded.
        """

        for step in report.steps[self._recorded_step_count :]:
            self.memory.record_execution(
                step.tool_name,
                step.kwargs,
                result=step.result,
                error=step.error,
            )

        self._recorded_step_count = len(report.steps)

        if report.stop_reason == "awaiting_approval":
            return report

        if self._task_id is not None:
            self.memory.complete_task(
                self._task_id,
                status="completed" if report.succeeded else "failed",
            )

        self.memory.record_turn(
            "agent",
            f"Autonomous execution finished ({report.stop_reason}): "
            f"{len(report.steps)} step(s) run.",
        )

        return report

    def available_tools(self) -> list[dict[str, Any]]:
        """
        Return metadata describing all registered tools.
        """

        return self.registry.get_tools()
