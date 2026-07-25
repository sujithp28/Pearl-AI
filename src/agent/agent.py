"""
Pearl Agent.

Main entry point for the AI Coding Agent.
"""

from __future__ import annotations

import logging
import warnings
from typing import Any, Callable

from src.agent.dispatcher import ToolDispatcher
from src.agent.executor import (
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_MAX_REPLANS,
    AutonomousExecutor,
    ExecutionReport,
    ProgressEvent,
)
from src.agent.planner import Planner, StepResult
from src.llm.client import LLMClient
from src.llm.tool_selector import LLMToolSelector
from src.memory import Memory
from src.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class PearlAgent:
    """
    Main Pearl AI Coding Agent.

    `run_autonomous()` is the recommended entry point for executing a
    request end-to-end: it plans, executes each step, replans on
    failure, and pauses for patch-preview approval before any file
    write reaches disk. Prefer it for any new integration.

    `run()` (single-tool selection) and `plan_and_run()` (sequential
    execution with no replanning or patch approval) predate
    `run_autonomous()` and are kept only for backward compatibility —
    see their docstrings. Neither gates file edits behind approval the
    way `run_autonomous()` does.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        memory: Memory | None = None,
    ) -> None:

        self.registry = registry

        self.dispatcher = ToolDispatcher(registry)

        self.selector = LLMToolSelector(registry)

        self.llm = LLMClient()

        self.planner = Planner(registry, self.dispatcher, self.llm)

        self.memory = memory or Memory()

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

    def run(
        self,
        prompt: str,
    ) -> Any:
        """
        Execute a single tool call selected for `prompt`.

        .. deprecated::
            Legacy single-step path, kept for backward compatibility.
            Prefer `run_autonomous()`, which plans multi-step tasks,
            replans on failure, and pauses for patch-preview approval
            before writing any file — this method does neither.

        Workflow

            User
              │
              ▼
        LLM Tool Selector
              │
              ▼
          ToolCall
              │
              ▼
         Tool Dispatcher
              │
              ▼
        Tool Execution
              │
              ▼
            Result
        """

        warnings.warn(
            "PearlAgent.run() is deprecated; prefer run_autonomous(), "
            "which plans, replans on failure, and gates file writes "
            "behind patch-preview approval.",
            DeprecationWarning,
            stacklevel=2,
        )

        logger.info("User Prompt: %s", prompt)

        self.memory.record_turn("user", prompt)

        tool_call = self.selector.select(prompt)

        logger.info(
            "Selected Tool: %s",
            tool_call.tool_name,
        )

        # Future fallback when no tool is appropriate.
        if tool_call.tool_name == "none":
            logger.info("No suitable tool selected.")
            response = "No suitable tool found for this request."
            self.memory.record_turn("agent", response)
            return response

        try:
            result = self.dispatcher.execute(
                tool_call.tool_name,
                *tool_call.args,
                **tool_call.kwargs,
            )
        except Exception as exc:
            self.memory.record_execution(
                tool_call.tool_name,
                tool_call.kwargs,
                error=str(exc),
            )
            raise

        logger.info("Tool executed successfully.")

        self.memory.record_execution(
            tool_call.tool_name,
            tool_call.kwargs,
            result=result,
        )
        self.memory.record_turn("agent", str(result))

        return result

    def chat(
        self,
        prompt: str,
    ) -> str:
        """
        Chat directly with the language model.

        This bypasses tool execution and is intended for
        conversational use.

        Future versions will integrate reasoning,
        memory, planning, and tool execution into
        a unified conversation pipeline.
        """

        logger.info("Chat request received.")

        self.memory.record_turn("user", prompt)

        response = self.llm.generate(prompt)

        self.memory.record_turn("agent", response)

        return response

    def plan_and_run(
        self,
        prompt: str,
    ) -> list[StepResult]:
        """
        Break a complex request into multiple steps and execute them
        sequentially, stopping at the first failure.

        .. deprecated::
            Legacy multi-step path, kept for backward compatibility.
            Prefer `run_autonomous()`, which additionally replans on
            failure instead of just stopping, and pauses for
            patch-preview approval before writing any file — this
            method does neither.

        Workflow

            User
              │
              ▼
             Planner
              │
              ▼
        [ ToolCall, ToolCall, ... ]
              │
              ▼
         Tool Dispatcher (per step)
              │
              ▼
          [ StepResult, ... ]
        """

        warnings.warn(
            "PearlAgent.plan_and_run() is deprecated; prefer "
            "run_autonomous(), which additionally replans on failure "
            "and gates file writes behind patch-preview approval.",
            DeprecationWarning,
            stacklevel=2,
        )

        logger.info("Planning multi-step execution for: %s", prompt)

        self.memory.record_turn("user", prompt)

        task = self.memory.start_task(prompt)

        try:
            results = self.planner.run(prompt)
        except Exception:
            self.memory.complete_task(task.id, status="failed")
            raise

        for step in results:
            self.memory.record_execution(
                step.tool_name,
                step.kwargs,
                result=step.result,
                error=step.error,
            )

        status = "completed" if all(step.succeeded for step in results) else "failed"
        self.memory.complete_task(task.id, status=status)

        self.memory.record_turn("agent", f"Executed {len(results)} step(s).")

        return results

    def run_autonomous(
        self,
        prompt: str,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        max_replans: int = DEFAULT_MAX_REPLANS,
        on_progress: Callable[[ProgressEvent], None] | None = None,
    ) -> ExecutionReport:
        """
        Recommended entry point: autonomously execute a multi-step
        task. Plans once via the existing `Planner`, then runs each
        step in turn via the existing `ToolDispatcher`, evaluating and
        summarizing the result after every step. A failed step
        triggers a request to the Planner for a revised remaining plan
        (up to `max_replans` times) instead of stopping immediately.
        Stops on completion, an unrecoverable failure, or
        `max_iterations`.

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

        logger.info("Starting autonomous execution for: %s", prompt)

        self.memory.record_turn("user", prompt)

        task = self.memory.start_task(prompt)

        executor = AutonomousExecutor(
            self.planner,
            self.dispatcher,
            max_iterations=max_iterations,
            max_replans=max_replans,
            on_progress=on_progress,
        )

        try:
            report = executor.run(prompt)
        except Exception:
            self.memory.complete_task(task.id, status="failed")
            raise

        for step in report.steps:
            self.memory.record_execution(
                step.tool_name,
                step.kwargs,
                result=step.result,
                error=step.error,
            )

        self.memory.complete_task(
            task.id,
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
