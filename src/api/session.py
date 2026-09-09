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

from src.agent.condenser import CannotCondenseError, Condenser
from src.agent.context_engine import ContextEngine
from src.agent.dispatcher import ToolDispatcher
from src.agent.event_bus import (
    ContextCondensedEvent,
    EventBus,
    RunCompleteEvent,
    RunFailedEvent,
)
from src.agent.executor import AutonomousExecutor, ExecutionReport, ProgressEvent
from src.agent.planner import Planner
from src.agent.reflection import ReflectionEngine
from src.agent.synthesizer import Synthesizer
from src.agent.verification import VerificationEngine
from src.config.settings import Settings
from src.llm.errors import ContextLengthError
from src.llm.router import ModelRouter
from src.main import build_registry
from src.memory import Memory
from src.prompts.system import build_chat_system_prompt
from src.repository.context import SemanticContextBuilder
from src.repository.service import RepositoryService
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

        self._synthesizer = Synthesizer(self._chat_llm)

        # Condenser — uses the cheap chat client for summarisation so the
        # planning model isn't consumed by bookkeeping.
        self._condenser = Condenser(self._chat_llm)

        # LLM-based reflection engine — uses chat client (not planning) so
        # it does not consume planning model capacity on bookkeeping.
        self._reflection_engine = ReflectionEngine(self._chat_llm)

        # Repository intelligence (M3 stack) — context builder is lazy-built
        # on first use so it never blocks startup.
        self._context_service = RepositoryService.get_or_build(self.workspace)
        self._context_builder = SemanticContextBuilder()

        # ContextEngine — token-budgeted context pipeline for every model call.
        self._context_engine = ContextEngine(
            memory=self.memory,
            condenser=self._condenser,
            context_service=self._context_service,
        )

        # Verification engine — runs tests after approve() so the executor can
        # replan on failures and reflection can judge from real evidence.
        self._verifier = VerificationEngine(workspace_root=self.workspace)

        # Active executor — None when idle, set during a run
        self._executor: AutonomousExecutor | None = None
        self._executor_lock = threading.Lock()
        # Prompt of the current/most-recent run, needed for post-approval synthesis.
        self._current_prompt: str = ""

        # Build startup index in background
        threading.Thread(
            target=self._build_index, daemon=True, name="pearl-index"
        ).start()

        logger.info("PearlSession ready: workspace=%s", self.workspace)

    def _build_index(self) -> None:
        try:
            build_startup_index(str(self.workspace))
        except Exception:
            logger.warning("Startup index failed (non-fatal)", exc_info=True)

    # ------------------------------------------------------------------ chat

    def chat_stream(self, message: str):
        """
        Yield (event_type, data) tuples for a chat message.
        event_type is "chunk" or "done".

        On ContextLengthError: condense memory and retry once so the user
        never sees a raw context-overflow error from the model.
        """
        # Proactive check before building history so we trim before sending.
        self._condenser.maybe_condense(self.memory, n_ctx=Settings.LOCAL_MODEL_CTX)

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

        except ContextLengthError:
            # History was still too long — try one emergency condensation.
            logger.warning("chat_stream: ContextLengthError; attempting emergency condensation.")
            yield "chunk", "\n[Compressing session history…]\n"
            try:
                self._condenser.condense(self.memory, reason="ContextLengthError in chat")
            except CannotCondenseError as cannot:
                yield "error", (
                    "The conversation is too long to continue and cannot be "
                    f"compressed further: {cannot}"
                )
                return

            # Retry with freshly condensed history.
            history = self.memory.recent_messages(limit=Settings.CHAT_HISTORY_TURNS)
            chunks = []
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
            except Exception as exc2:
                yield "error", f"LLM error after condensation: {exc2}"

        except Exception as exc:
            msg = str(exc)
            if "credentials" in msg.lower() or "api_key" in msg.lower() or "not configured" in msg.lower():
                err = (
                    "Pearl is not connected to a model. "
                    "Open Settings to choose a provider, or add the "
                    "appropriate key to your .env file and restart."
                )
            else:
                err = f"LLM error: {msg}"
            yield "error", err

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

        Proactively condenses Memory before each run.  On ContextLengthError
        during planning or execution, attempts emergency condensation and
        retries up to ``Settings.CONDENSER_MAX_RETRIES`` times.
        """
        from src.config.workspace import set_workspace_root
        set_workspace_root(self.workspace)

        bus = EventBus()
        loop = _get_loop()

        def _bridge() -> None:
            for d in bus.drain_dicts():
                asyncio.run_coroutine_threadsafe(event_queue.put(d), loop)

        bridge_thread = threading.Thread(target=_bridge, daemon=True, name="pearl-bus-bridge")
        bridge_thread.start()

        def _emit(event) -> None:
            bus.emit(event)

        def _close_bus() -> None:
            bus.close()
            bridge_thread.join(timeout=5)

        # Proactive condensation before adding the new user turn so the
        # token estimate reflects the existing history accurately.
        condense_result = self._condenser.maybe_condense(
            self.memory, n_ctx=Settings.LOCAL_MODEL_CTX
        )
        if condense_result.condensed:
            logger.info(
                "Pre-run condensation: %d→%d turns, ~%d→~%d tokens",
                condense_result.turns_before,
                condense_result.turns_after,
                condense_result.tokens_before,
                condense_result.tokens_after,
            )
            _emit(ContextCondensedEvent(
                turns_before=condense_result.turns_before,
                turns_after=condense_result.turns_after,
                tokens_before=condense_result.tokens_before,
                tokens_after=condense_result.tokens_after,
                reason=condense_result.reason,
            ))

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
                loop,
            )

        executor = AutonomousExecutor(
            self.planner,
            self.dispatcher,
            on_progress=on_progress,
            checkpoints=self.checkpoints,
            context_builder=self._context_builder,
            context_service=self._context_service,
            reflection_engine=self._reflection_engine,
            context_engine=self._context_engine,
            verifier=self._verifier,
        )

        with self._executor_lock:
            self._executor = executor
            self._current_prompt = prompt

        # Run with bounded ContextLengthError recovery.
        retries_left = Settings.CONDENSER_MAX_RETRIES
        report = None
        while report is None:
            try:
                report = executor.run(prompt)
            except ContextLengthError as exc:
                if retries_left <= 0:
                    error_msg = (
                        "Context window exhausted and condensation "
                        f"retry limit reached: {exc}"
                    )
                    _emit(RunFailedEvent(error=error_msg, stop_reason="context_exhausted"))
                    _close_bus()
                    asyncio.run_coroutine_threadsafe(
                        event_queue.put({"type": "error", "message": error_msg}),
                        loop,
                    )
                    return
                logger.warning(
                    "ContextLengthError during autonomous run; condensing and retrying "
                    "(%d retries left). Error: %s",
                    retries_left,
                    exc,
                )
                try:
                    self._condenser.condense(self.memory, reason="ContextLengthError in autonomous run")
                except CannotCondenseError as cannot:
                    error_msg = (
                        "Context window exhausted and history cannot be "
                        f"compressed further: {cannot}"
                    )
                    _emit(RunFailedEvent(error=error_msg, stop_reason="cannot_condense"))
                    _close_bus()
                    asyncio.run_coroutine_threadsafe(
                        event_queue.put({"type": "error", "message": error_msg}),
                        loop,
                    )
                    return
                retries_left -= 1
                # Re-create executor with fresh state — the plan itself may
                # have been partially completed and the executor's internal
                # step tracking is invalid after the error.
                executor = AutonomousExecutor(
                    self.planner,
                    self.dispatcher,
                    on_progress=on_progress,
                    checkpoints=self.checkpoints,
                    context_builder=self._context_builder,
                    context_service=self._context_service,
                    reflection_engine=self._reflection_engine,
                    context_engine=self._context_engine,
                    verifier=self._verifier,
                )
                with self._executor_lock:
                    self._executor = executor
            except Exception as exc:
                msg = str(exc)
                if any(k in msg.lower() for k in ("credentials", "api_key", "not configured", "authentication")):
                    msg = (
                        "Pearl is not connected to a model. "
                        "Open Settings to choose a provider, or add the "
                        "appropriate key to your .env file and restart."
                    )
                _emit(RunFailedEvent(error=msg, stop_reason="exception"))
                _close_bus()
                asyncio.run_coroutine_threadsafe(
                    event_queue.put({"type": "error", "message": msg}),
                    loop,
                )
                return

        result = _report_to_dict(report)
        if report.stop_reason != "awaiting_approval":
            result["final_answer"] = self._synthesizer.synthesize(prompt, report)

        _emit(RunCompleteEvent(
            stop_reason=report.stop_reason,
            steps=len(report.steps),
            replans=report.replans_used,
        ))
        _close_bus()

        asyncio.run_coroutine_threadsafe(
            event_queue.put({"type": "result", "report": result}),
            loop,
        )

        if report.stop_reason != "awaiting_approval":
            final_ans = result.get("final_answer", "")
            self.memory.record_turn(
                "agent",
                final_ans or f"Completed ({report.stop_reason}): {len(report.steps)} step(s).",
            )
            with self._executor_lock:
                self._executor = None

    def approve(self) -> dict[str, Any]:
        with self._executor_lock:
            if self._executor is None or not self._executor.is_awaiting_approval():
                return {"error": "No run awaiting approval."}
            executor = self._executor
            prompt = self._current_prompt

        # Capture before apply_all() clears staging
        planned = list(executor.patch_manager.affected_files())

        try:
            report = executor.approve()
        except Exception as exc:
            return {"error": str(exc)}

        result = _report_to_dict(report)

        if report.stop_reason not in ("awaiting_approval", "rejected", "cancelled"):
            # Reuse the executor's verification when it already ran one —
            # re-running the suite here would double every approval's cost.
            verification = executor._last_verification or _run_verification(
                self.workspace, planned
            )
            result["verification"] = verification
            result["final_answer"] = self._synthesizer.synthesize(
                prompt, report, verification=verification
            )
            with self._executor_lock:
                self._executor = None
            final_ans = result.get("final_answer", "")
            self.memory.record_turn(
                "agent",
                final_ans or f"Approved. Completed ({report.stop_reason}): {len(report.steps)} step(s).",
            )

        return result

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
    d: dict[str, Any] = {
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
    if report.error:
        d["error"] = report.error
    if report.llm_reflection is not None:
        d["reflection"] = {
            "status": report.llm_reflection.status,
            "confidence": report.llm_reflection.confidence,
            "reason": report.llm_reflection.reason,
            "missing_requirements": report.llm_reflection.missing_requirements,
            "recommended_action": report.llm_reflection.recommended_action,
        }
    return d


def _safe(v: Any) -> Any:
    if isinstance(v, (str, int, float, bool, type(None))):
        return v
    return str(v)


def _run_verification(workspace: Path, planned_files: list[str]) -> dict[str, Any]:
    try:
        from src.agent.verification import VerificationEngine
        vr = VerificationEngine(workspace_root=workspace).verify(planned_files)
        return {
            "status": vr.status.value,
            "risk": vr.risk.value,
            "confidence": round(vr.confidence, 2),
            "planned_files": list(vr.planned_files),
            "changed_files": list(vr.changed_files),
            "unexpected_files": list(vr.unexpected_files),
            "tests_run": vr.tests_run,
            "tests_passed": vr.tests_passed,
            "tests_failed": vr.tests_failed,
            "diff_summary": vr.diff_summary,
            "evidence": list(vr.evidence),
        }
    except Exception as exc:
        logger.warning("VerificationEngine failed (non-fatal): %s", exc)
        return {"status": "TOOL_SUCCESS_BUT_TASK_UNVERIFIED", "error": str(exc)}
