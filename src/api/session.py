"""
Pearl API session — one per server process.

Holds the agent, memory, and executor state for the lifetime of the
local Pearl server.  Mirrors the same state MCPServer holds, but without
the stdio transport layer.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

from src.agent.executor import AutonomousExecutor, ExecutionReport, ProgressEvent
from src.agent.planner import Planner
from src.agent.dispatcher import ToolDispatcher
from src.config.settings import Settings
from src.llm.router import ModelRouter
from src.main import build_registry
from src.memory import Memory
from src.prompts.system import build_chat_system_prompt
from src.tools.checkpoints import CheckpointManager
from src.tools.repo_tools import build_startup_index

logger = logging.getLogger(__name__)


class PearlSession:
    """
    Singleton session for the standalone Pearl server.

    Thread-safe: chat and autonomous runs run in a background thread
    and publish events onto an asyncio Queue that SSE handlers drain.
    """

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()

        self.registry = build_registry()
        self.dispatcher = ToolDispatcher(self.registry)
        self.memory = Memory()
        self.checkpoints = CheckpointManager()

        router = ModelRouter()
        self._planning_llm = router.planning_client()
        self._chat_llm = (
            router.chat_client()
            if not router.all_same_provider()
            else self._planning_llm
        )

        self.planner = Planner(self.registry, self.dispatcher, self._planning_llm)

        # Active executor — None when idle, set during a run
        self._executor: AutonomousExecutor | None = None
        self._executor_lock = threading.Lock()

        # Build startup index in background
        threading.Thread(
            target=self._build_index, daemon=True, name="pearl-index"
        ).start()

        logger.info("PearlSession ready: workspace=%s", self.workspace)

    def _build_index(self) -> None:
        try:
            import os
            old = os.getcwd()
            os.chdir(self.workspace)
            build_startup_index(str(self.workspace))
            os.chdir(old)
        except Exception:
            logger.warning("Startup index failed (non-fatal)", exc_info=True)

    # ------------------------------------------------------------------ chat

    def chat_stream(self, message: str):
        """
        Yield (event_type, data) tuples for a chat message.
        event_type is "chunk" or "done".
        """
        import os
        old_cwd = os.getcwd()
        os.chdir(self.workspace)
        try:
            history = self.memory.recent_messages(limit=Settings.CHAT_HISTORY_TURNS)
            self.memory.record_turn("user", message)

            chunks: list[str] = []
            try:
                for chunk in self._chat_llm.generate_stream(
                    message,
                    history=history,
                    system=build_chat_system_prompt(str(self.workspace)),
                ):
                    chunks.append(chunk)
                    yield "chunk", chunk

                response = "".join(chunks)
                self.memory.record_turn("agent", response)
                yield "done", response

            except Exception as exc:
                msg = str(exc)
                if "credentials" in msg.lower() or "api_key" in msg.lower():
                    err = (
                        "Pearl is not configured: no LLM API key found. "
                        "Set OPENAI_API_KEY, ANTHROPIC_API_KEY, or GEMINI_API_KEY "
                        "in your .env file and restart."
                    )
                else:
                    err = f"LLM error: {msg}"
                yield "error", err
        finally:
            os.chdir(old_cwd)

    # -------------------------------------------------------------- autonomous

    def is_running(self) -> bool:
        with self._executor_lock:
            return self._executor is not None and not self._executor.is_awaiting_approval()

    def is_awaiting_approval(self) -> bool:
        with self._executor_lock:
            return (
                self._executor is not None
                and self._executor.is_awaiting_approval()
            )

    def pending_diff(self) -> str:
        with self._executor_lock:
            if self._executor is None:
                return ""
            return self._executor.patch_manager.combined_diff()

    def pending_files(self) -> list[str]:
        with self._executor_lock:
            if self._executor is None:
                return []
            return self._executor.patch_manager.affected_files()

    def run_autonomous_stream(self, prompt: str, event_queue: asyncio.Queue):
        """
        Run an autonomous task in the current thread, publishing
        structured events onto `event_queue` so the SSE handler can
        forward them to the browser.

        Call from a background thread only.
        """
        import os
        old_cwd = os.getcwd()
        os.chdir(self.workspace)
        try:
            self.memory.record_turn("user", prompt)

            def on_progress(event: ProgressEvent) -> None:
                asyncio.run_coroutine_threadsafe(
                    event_queue.put({
                        "type": "progress",
                        "status": event.status,
                        "currentStep": event.current_step,
                        "totalSteps": event.total_steps,
                        "currentAction": event.current_action,
                    }),
                    _get_loop(),
                )

            executor = AutonomousExecutor(
                self.planner,
                self.dispatcher,
                on_progress=on_progress,
                checkpoints=self.checkpoints,
            )

            with self._executor_lock:
                self._executor = executor

            try:
                report = executor.run(prompt)
            except Exception as exc:
                asyncio.run_coroutine_threadsafe(
                    event_queue.put({"type": "error", "message": str(exc)}),
                    _get_loop(),
                )
                return

            asyncio.run_coroutine_threadsafe(
                event_queue.put({"type": "result", "report": _report_to_dict(report)}),
                _get_loop(),
            )

            if report.stop_reason != "awaiting_approval":
                self.memory.record_turn(
                    "agent",
                    f"Completed ({report.stop_reason}): {len(report.steps)} step(s).",
                )
                with self._executor_lock:
                    self._executor = None
        finally:
            os.chdir(old_cwd)

    def approve(self) -> dict[str, Any]:
        with self._executor_lock:
            if self._executor is None or not self._executor.is_awaiting_approval():
                return {"error": "No run awaiting approval."}
            executor = self._executor

        try:
            report = executor.approve()
        except Exception as exc:
            return {"error": str(exc)}

        if report.stop_reason != "awaiting_approval":
            with self._executor_lock:
                self._executor = None
            self.memory.record_turn(
                "agent",
                f"Approved. Completed ({report.stop_reason}): {len(report.steps)} step(s).",
            )

        return _report_to_dict(report)

    def reject(self) -> dict[str, Any]:
        with self._executor_lock:
            if self._executor is None or not self._executor.is_awaiting_approval():
                return {"error": "No run awaiting approval."}
            executor = self._executor

        try:
            report = executor.reject()
        except Exception as exc:
            return {"error": str(exc)}

        with self._executor_lock:
            self._executor = None

        self.memory.record_turn("agent", "Changes rejected.")
        return _report_to_dict(report)

    def cancel(self) -> bool:
        with self._executor_lock:
            if self._executor is None:
                return False
            self._executor.cancel()
            return True

    def conversation_history(self) -> list[dict[str, Any]]:
        return self.memory.recent_messages(limit=100)

    def workspace_info(self) -> dict[str, Any]:
        return {
            "path": str(self.workspace),
            "name": self.workspace.name,
        }


# ------------------------------------------------------------------ helpers

_loop: asyncio.AbstractEventLoop | None = None


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop
    if _loop is None:
        raise RuntimeError("Event loop not registered")
    return _loop


def register_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def _report_to_dict(report: ExecutionReport) -> dict[str, Any]:
    return {
        "succeeded": report.succeeded,
        "stop_reason": report.stop_reason,
        "replans_used": report.replans_used,
        "steps": [
            {
                "tool_name": s.tool_name,
                "succeeded": s.succeeded,
                "summary": s.summary,
                "result": _safe(s.result),
                "error": s.error,
                "iteration": s.iteration,
            }
            for s in report.steps
        ],
    }


def _safe(v: Any) -> Any:
    if isinstance(v, (str, int, float, bool, type(None))):
        return v
    return str(v)
