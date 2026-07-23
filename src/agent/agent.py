"""
Pearl Agent.

Main entry point for the AI Coding Agent.
"""

from __future__ import annotations

import logging
from typing import Any

from src.agent.dispatcher import ToolDispatcher
from src.agent.planner import Planner, StepResult
from src.llm.client import LLMClient
from src.llm.tool_selector import LLMToolSelector
from src.memory import Memory
from src.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class PearlAgent:
    """
    Main Pearl AI Coding Agent.

    Responsibilities
    ----------------
    - Receive user requests.
    - Ask the LLM which tool should be executed.
    - Execute the selected tool.
    - Return the tool result.

    Future versions will extend this workflow with:
    - Memory
    - Planning
    - Multi-tool execution
    - Reflection
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
        Execute a complete user request.

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
        Break a complex request into multiple steps and execute
        them sequentially.

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

        status = (
            "completed"
            if all(step.succeeded for step in results)
            else "failed"
        )
        self.memory.complete_task(task.id, status=status)

        self.memory.record_turn(
            "agent", f"Executed {len(results)} step(s)."
        )

        return results

    def available_tools(self) -> list[dict[str, Any]]:
        """
        Return metadata describing all registered tools.
        """

        return self.registry.get_tools()