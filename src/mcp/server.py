"""
MCP (Model Context Protocol) server for Pearl.

Exposes Pearl's existing `ToolRegistry` / `ToolDispatcher` for tool
discovery and execution, and its `Planner` + `Memory` for multi-step
planning, over the MCP JSON-RPC protocol via stdio.

No tool is redefined here: every tool exposed by this server is a
tool already registered with the `ToolRegistry` passed in (see
`src.main.build_registry` for how Pearl's CLI builds one).
"""

from __future__ import annotations

import json
import logging
import sys
from typing import IO, Any, Callable

from src.agent.dispatcher import ToolDispatcher, ToolExecutionError, ToolNotFoundError
from src.agent.executor import (
    AutonomousExecutor,
    ExecutionReport,
    ExecutionStep,
    ProgressEvent,
)
from src.agent.planner import Planner
from src.config.settings import Settings
from src.llm.client import LLMClient
from src.mcp.protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    MCP_PROTOCOL_VERSION,
    METHOD_NOT_FOUND,
    JsonRpcError,
    JsonRpcRequest,
    JsonRpcResponse,
    MCPProtocolError,
    parse_request,
    tool_to_mcp_schema,
)
from src.memory import Memory
from src.personality import EventKind, PersonalityManager
from src.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

SERVER_NAME = "pearl-mcp"
SERVER_VERSION = "1.2.0-beta"

# A handler calls `notify(method, params)` to push a JSON-RPC
# notification to the client immediately, interleaved with (ahead of)
# its eventual response — the mechanism `pearl/progress` streaming
# uses. Every handler receives one; most ignore it. `handle_request`
# supplies a no-op when the caller doesn't pass one (e.g. every
# existing direct `server.handle_request(request)` call in tests),
# so this is purely additive — nothing that doesn't opt in changes.
NotifyFn = Callable[[str, dict[str, Any]], None]


def _noop_notify(method: str, params: dict[str, Any]) -> None:
    return None


def _progress_event_to_dict(event: ProgressEvent) -> dict[str, Any]:
    return {
        "status": event.status,
        "currentStep": event.current_step,
        "totalSteps": event.total_steps,
        "currentAction": event.current_action,
    }


def _json_safe(value: Any) -> Any:
    """
    Return `value` unchanged if it's JSON-serializable, otherwise its
    string form.

    Tool results aren't guaranteed to be serializable (e.g.
    `execute_shell` returns a `subprocess.CompletedProcess`), but both
    Memory (`Memory.to_dict()`) and JSON-RPC responses require it.
    """

    try:
        json.dumps(value)
    except TypeError:
        return str(value)

    return value


def _execution_step_to_dict(step: ExecutionStep) -> dict[str, Any]:
    return {
        "tool": step.tool_name,
        "arguments": _json_safe(step.kwargs),
        "succeeded": step.succeeded,
        "summary": step.summary,
    }


def _execution_report_to_dict(
    report: ExecutionReport, executor: AutonomousExecutor
) -> dict[str, Any]:
    """
    Convert an `ExecutionReport` (and, for a paused run, the matching
    `PatchManager`/`CommandApprovalManager` state) into the camelCase
    shape the VS Code extension's `patchClient.ts` expects.

    `commands` is additive (Phase 26): existing clients that only read
    `patches` are unaffected; a client that also wants to surface
    pending shell commands for approval can read this new field.
    """

    patches: list[dict[str, Any]] = []
    commands: list[dict[str, Any]] = []

    if report.stop_reason == "awaiting_approval":
        patches = [
            {
                "path": edit.path,
                "diff": edit.diff,
                "isNewFile": edit.is_new_file,
            }
            for edit in executor.patch_manager.pending
        ]
        commands = [
            {
                "command": pending.request.command,
                "cwd": pending.request.cwd,
                "timeout": pending.request.timeout,
            }
            for pending in executor.command_approver.pending
        ]

    return {
        "stopReason": report.stop_reason,
        "steps": [_execution_step_to_dict(step) for step in report.steps],
        "patches": patches,
        "commands": commands,
        "replansUsed": report.replans_used,
    }


class MCPServer:
    """
    MCP server exposing Pearl's tool registry (and, optionally, its
    planner) over JSON-RPC.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        dispatcher: ToolDispatcher | None = None,
        planner: Planner | None = None,
        memory: Memory | None = None,
        llm: LLMClient | None = None,
        personality: PersonalityManager | None = None,
    ) -> None:
        self.registry = registry
        self.dispatcher = dispatcher or ToolDispatcher(registry)
        self.planner = planner
        self.memory = memory or Memory()
        self.llm = llm
        self._personality = personality or PersonalityManager()
        self._autonomous_executor: AutonomousExecutor | None = None

    # -- Request handling ---------------------------------------------------

    def handle_request(
        self,
        request: JsonRpcRequest,
        notify: NotifyFn | None = None,
    ) -> JsonRpcResponse | None:
        """
        Dispatch one JSON-RPC request. Returns None for notifications
        (which never receive a response).

        `notify`, if given, lets the handler push JSON-RPC
        notifications (e.g. `pearl/progress`) to the client while the
        request is still being handled — see `NotifyFn`. Omitting it
        (the default) is exactly the prior behavior: no notifications
        are sent, only the final response.
        """

        active_notify = notify if notify is not None else _noop_notify

        handler = self._HANDLERS.get(request.method)

        if handler is None:
            logger.warning("Unknown MCP method: %s", request.method)

            if request.is_notification:
                return None

            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(
                    METHOD_NOT_FOUND,
                    f"Unknown method: {request.method}",
                ),
            )

        if request.is_notification:
            try:
                handler(self, request.params, active_notify)
            except Exception:
                logger.exception("Error handling notification: %s", request.method)
            return None

        try:
            result = handler(self, request.params, active_notify)
        except MCPProtocolError as exc:
            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(exc.code, exc.message),
            )
        except Exception as exc:
            logger.exception("Internal error handling method: %s", request.method)
            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(INTERNAL_ERROR, str(exc)),
            )

        return JsonRpcResponse(id=request.id, result=result)

    # -- Method implementations ----------------------------------------------

    def _initialize(self, params: dict[str, Any], notify: NotifyFn) -> dict[str, Any]:
        """
        Handle the `initialize` handshake.
        """

        capabilities: dict[str, Any] = {"tools": {}}
        experimental: dict[str, Any] = {"pearlPersonality": {}}

        if self.planner is not None:
            experimental["pearlPlanning"] = {}
            experimental["pearlAutonomous"] = {}

        if self.llm is not None:
            experimental["pearlChat"] = {}

        if experimental:
            capabilities["experimental"] = experimental

        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "serverInfo": {
                "name": SERVER_NAME,
                "version": SERVER_VERSION,
            },
            "capabilities": capabilities,
        }

    def _tools_list(self, params: dict[str, Any], notify: NotifyFn) -> dict[str, Any]:
        """
        List every tool currently in the registry.
        """

        return {"tools": [tool_to_mcp_schema(tool) for tool in self.registry]}

    def _tools_call(self, params: dict[str, Any], notify: NotifyFn) -> dict[str, Any]:
        """
        Execute a registered tool by name via the ToolDispatcher.
        """

        name = params.get("name")

        if not isinstance(name, str) or not name:
            raise MCPProtocolError(INVALID_PARAMS, "'name' is required.")

        arguments = params.get("arguments")

        if arguments is None:
            arguments = {}

        if not isinstance(arguments, dict):
            raise MCPProtocolError(INVALID_PARAMS, "'arguments' must be an object.")

        try:
            result = self.dispatcher.execute(name, **arguments)
        except (ToolNotFoundError, ToolExecutionError) as exc:
            self.memory.record_execution(name, arguments, error=str(exc))

            return {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            }

        self.memory.record_execution(name, arguments, result=_json_safe(result))

        return {
            "content": [{"type": "text", "text": str(result)}],
            "isError": False,
        }

    def _plan_run(self, params: dict[str, Any], notify: NotifyFn) -> dict[str, Any]:
        """
        Break a request into multiple steps and execute them via the
        Planner (a Pearl-specific extension beyond the core MCP
        tool-call methods).
        """

        if self.planner is None:
            raise MCPProtocolError(
                INVALID_PARAMS, "Planning is not enabled on this server."
            )

        prompt = params.get("prompt")

        if not isinstance(prompt, str) or not prompt:
            raise MCPProtocolError(INVALID_PARAMS, "'prompt' is required.")

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
                result=_json_safe(step.result) if step.succeeded else None,
                error=step.error,
            )

        status = "completed" if all(step.succeeded for step in results) else "failed"
        self.memory.complete_task(task.id, status=status)

        return {
            "steps": [
                {
                    "tool": step.tool_name,
                    "arguments": step.kwargs,
                    "result": _json_safe(step.result) if step.succeeded else None,
                    "error": step.error,
                }
                for step in results
            ]
        }

    def _plan_only(self, params: dict[str, Any], notify: NotifyFn) -> dict[str, Any]:
        """
        Return the steps the Planner would execute for a prompt,
        without executing them.

        This reuses `Planner.plan()` (already part of the existing
        planner, previously only used internally by `Planner.run()`)
        so a client can gate each step behind its own approval step
        before ever calling `tools/call`. Nothing is dispatched and
        nothing is recorded in Memory here — only `plan/run` and
        `tools/call` have execution side effects.
        """

        if self.planner is None:
            raise MCPProtocolError(
                INVALID_PARAMS, "Planning is not enabled on this server."
            )

        prompt = params.get("prompt")

        if not isinstance(prompt, str) or not prompt:
            raise MCPProtocolError(INVALID_PARAMS, "'prompt' is required.")

        steps = self.planner.plan(prompt)

        return {
            "steps": [
                {"tool": step.tool_name, "arguments": step.kwargs} for step in steps
            ]
        }

    def _chat(self, params: dict[str, Any], notify: NotifyFn) -> dict[str, Any]:
        """
        Chat directly with the language model (a Pearl-specific
        extension beyond the core MCP methods). Reuses the same
        `LLMClient.generate()` pathway as `PearlAgent.chat()`, and
        records both turns in Memory.
        """

        message = params.get("message")

        if not isinstance(message, str) or not message:
            raise MCPProtocolError(INVALID_PARAMS, "'message' is required.")

        if self.llm is None:
            self.llm = LLMClient()

        # Captured before recording this turn: `record_turn` would
        # otherwise put `message` into the history too, and it's
        # already being sent as the prompt — the model would see the
        # same message twice.
        history = self.memory.recent_messages(limit=Settings.CHAT_HISTORY_TURNS)

        self.memory.record_turn("user", message)

        response = self.llm.generate(message, history=history)

        self.memory.record_turn("agent", response)

        return {"message": response}

    def _run_autonomous(
        self, params: dict[str, Any], notify: NotifyFn
    ) -> dict[str, Any]:
        """
        Start an autonomous run for `prompt` (a Pearl-specific
        extension beyond the core MCP methods).

        Reuses the existing `AutonomousExecutor` exactly as-is: this
        just constructs one (bound to the server's own `planner` /
        `dispatcher`) and calls `.run()`. All planning, execution,
        reflection, cancellation, and patch-preview logic lives in
        `AutonomousExecutor`/`PatchManager` — nothing is duplicated
        here, only translated to/from JSON.
        """

        if self.planner is None:
            raise MCPProtocolError(
                INVALID_PARAMS, "Planning is not enabled on this server."
            )

        prompt = params.get("prompt")

        if not isinstance(prompt, str) or not prompt:
            raise MCPProtocolError(INVALID_PARAMS, "'prompt' is required.")

        if (
            self._autonomous_executor is not None
            and self._autonomous_executor.is_awaiting_approval()
        ):
            raise MCPProtocolError(
                INVALID_PARAMS,
                "An autonomous run is already awaiting approval; call "
                "pearl/approvePatches or pearl/rejectPatches first.",
            )

        executor = AutonomousExecutor(
            self.planner,
            self.dispatcher,
            on_progress=self._make_progress_forwarder(notify),
        )
        self._autonomous_executor = executor

        report = executor.run(prompt)

        return _execution_report_to_dict(report, executor)

    def _approve_patches(
        self, params: dict[str, Any], notify: NotifyFn
    ) -> dict[str, Any]:
        """
        Approve the currently pending patch batch: write it to disk
        and resume execution exactly where it paused (via the same
        `AutonomousExecutor.approve()` used everywhere else — no
        re-planning, no re-running completed steps).
        """

        executor = self._require_awaiting_approval()
        executor.on_progress = self._make_progress_forwarder(notify)

        report = executor.approve()

        return _execution_report_to_dict(report, executor)

    def _reject_patches(
        self, params: dict[str, Any], notify: NotifyFn
    ) -> dict[str, Any]:
        """
        Discard the currently pending patch batch and stop cleanly
        (via `AutonomousExecutor.reject()`).
        """

        executor = self._require_awaiting_approval()
        executor.on_progress = self._make_progress_forwarder(notify)

        report = executor.reject()

        return _execution_report_to_dict(report, executor)

    @staticmethod
    def _make_progress_forwarder(
        notify: NotifyFn,
    ) -> Callable[[ProgressEvent], None]:
        """
        Return an `AutonomousExecutor.on_progress` callback that
        forwards each `ProgressEvent` as a `pearl/progress`
        notification via `notify`.

        Rebuilt fresh for every `_run_autonomous`/`_approve_patches`/
        `_reject_patches` call (reassigning `executor.on_progress`
        each time) rather than fixed at executor construction, so a
        resumed run's progress always streams to *this* call's
        `notify` — the one actually attached to the current request —
        not a stale one captured when the run first started.
        """

        def _forward(event: ProgressEvent) -> None:
            notify("pearl/progress", _progress_event_to_dict(event))

        return _forward

    def _require_awaiting_approval(self) -> AutonomousExecutor:
        if (
            self._autonomous_executor is None
            or not self._autonomous_executor.is_awaiting_approval()
        ):
            raise MCPProtocolError(
                INVALID_PARAMS,
                "No autonomous run is currently awaiting approval.",
            )

        return self._autonomous_executor

    # Keyed by the exact `TimelineStage` string values the VS Code
    # extension's `handleUserMessage` plan-preview flow already uses
    # (`chatController.ts`/`chatHtml.ts`) — that flow has no
    # `AutonomousExecutor` (and so no `pearl/progress` stream) behind
    # it at all, it's driven client-side via `pearl/planOnly` +
    # `tools/call`, so its stage labels need their own, one-shot
    # lookup instead.
    _TIMELINE_EVENT_KINDS: dict[str, EventKind] = {
        "planning": EventKind.PLANNING,
        "plan_ready": EventKind.PLAN_READY,
        "waiting_approval": EventKind.APPROVAL,
        "running_tool": EventKind.EXECUTING,
        "completed": EventKind.COMPLETED,
    }

    def _personality_labels(
        self, params: dict[str, Any], notify: NotifyFn
    ) -> dict[str, Any]:
        """
        Return the configured personality's wording for each of the
        VS Code extension's plan-preview timeline stages (a
        Pearl-specific extension beyond the core MCP methods).

        Read-only, and cheap enough to call on every
        `handleUserMessage` round rather than needing its own cache
        invalidation story: `PersonalityManager.format()` is just a
        couple of dict lookups.
        """

        labels = {
            stage: self._personality.format(event_kind)
            for stage, event_kind in self._TIMELINE_EVENT_KINDS.items()
        }

        return {"labels": labels}

    def _memory(self, params: dict[str, Any], notify: NotifyFn) -> dict[str, Any]:
        """
        Return the current contents of Memory (a Pearl-specific
        extension for inspecting agent state): conversation history,
        task history, execution history, and project facts.

        Read-only: reuses `Memory.to_dict()` exactly as-is; nothing
        here mutates Memory or the underlying `Memory` implementation.
        """

        return self.memory.to_dict()

    def _shutdown(self, params: dict[str, Any], notify: NotifyFn) -> None:
        """
        Handle the `shutdown` request.
        """

        logger.info("Shutdown requested.")

        return None

    _HANDLERS = {
        "initialize": _initialize,
        "tools/list": _tools_list,
        "tools/call": _tools_call,
        "pearl/plan": _plan_run,
        "pearl/planOnly": _plan_only,
        "pearl/chat": _chat,
        "pearl/runAutonomous": _run_autonomous,
        "pearl/approvePatches": _approve_patches,
        "pearl/rejectPatches": _reject_patches,
        "pearl/memory": _memory,
        "pearl/personality": _personality_labels,
        "shutdown": _shutdown,
    }

    # -- stdio transport -------------------------------------------------------

    def run_stdio(
        self,
        input_stream: IO[str] | None = None,
        output_stream: IO[str] | None = None,
    ) -> None:
        """
        Read newline-delimited JSON-RPC messages from `input_stream`
        and write responses to `output_stream` until EOF or an
        `exit` notification is received.
        """

        input_stream = input_stream if input_stream is not None else sys.stdin
        output_stream = output_stream if output_stream is not None else sys.stdout

        logger.info("MCP server listening on stdio.")

        for line in input_stream:
            line = line.strip()

            if not line:
                continue

            if not self.handle_line(line, output_stream):
                break

        logger.info("MCP server stopped.")

    def handle_line(self, line: str, output_stream: IO[str]) -> bool:
        """
        Parse and handle one line of JSON-RPC input.

        Returns False if the caller should stop reading further
        input (an `exit` notification was received), True otherwise.
        """

        try:
            request = parse_request(line)
        except MCPProtocolError as exc:
            response = JsonRpcResponse(
                id=None,
                error=JsonRpcError(exc.code, exc.message),
            )
            output_stream.write(response.to_json() + "\n")
            output_stream.flush()
            return True

        if request.method == "exit":
            logger.info("Received exit notification.")
            return False

        response = self.handle_request(request, self._make_notifier(output_stream))

        if response is not None:
            output_stream.write(response.to_json() + "\n")
            output_stream.flush()

        return True

    @staticmethod
    def _make_notifier(output_stream: IO[str]) -> NotifyFn:
        """
        Return a `NotifyFn` that writes a JSON-RPC notification line
        to `output_stream` and flushes immediately, so a client
        reading line-by-line sees it as soon as it's sent rather than
        buffered behind the eventual response.

        Never raises: a failure to write a notification (e.g. a
        broken pipe) is logged and swallowed rather than aborting
        whatever request is in progress — the same "never let
        progress reporting break execution" rule `AutonomousExecutor`
        already applies to its own `on_progress` callback.
        """

        def _notify(method: str, params: dict[str, Any]) -> None:
            message = {"jsonrpc": "2.0", "method": method, "params": params}

            try:
                output_stream.write(json.dumps(message) + "\n")
                output_stream.flush()
            except Exception:
                logger.warning(
                    "Failed to write notification '%s'.", method, exc_info=True
                )

        return _notify
