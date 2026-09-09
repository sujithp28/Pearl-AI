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
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, Callable

from src.agent.completion import CompletionService
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
from src.llm.errors import ProviderAuthError
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
from src.prompts.system import build_chat_system_prompt
from src.tools.checkpoints import (
    Checkpoint,
    CheckpointError,
    CheckpointManager,
    RestoreReport,
)
from src.tools.registry import ToolRegistry

if TYPE_CHECKING:
    # Imported for annotations only. Both are constructed lazily inside
    # the _build_* methods below, so the runtime import stays there and
    # a workspace that cannot support them never pays for the import.
    from src.agent.reflection import ReflectionEngine
    from src.agent.verification import VerificationEngine

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


def _checkpoint_to_dict(checkpoint: Checkpoint) -> dict[str, Any]:
    return {
        "id": checkpoint.id,
        "shortId": checkpoint.short_id,
        "label": checkpoint.label,
        "createdAt": checkpoint.created_at,
    }


def _restore_report_to_dict(report: RestoreReport) -> dict[str, Any]:
    return {
        "checkpointId": report.checkpoint_id,
        "restored": report.restored,
        "removed": report.removed,
        "changedAnything": report.changed_anything,
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
    `ChangeManager`/`CommandApprovalManager` state) into the camelCase
    shape the VS Code extension's `patchClient.ts` expects.

    `commands` is additive (Phase 26): existing clients that only read
    `patches` are unaffected; a client that also wants to surface
    pending shell commands for approval can read this new field.

    Each patch's `isDeletion` is additive in the same way: a client that
    ignores it still renders the diff correctly, since a staged deletion
    already reads as a full-file removal. Reading it lets the client
    label the entry as a deletion rather than a large edit.
    """

    patches: list[dict[str, Any]] = []
    commands: list[dict[str, Any]] = []

    if report.stop_reason == "awaiting_approval":
        patches = [
            {
                "path": edit.path,
                "diff": edit.diff,
                "isNewFile": edit.is_new_file,
                "isDeletion": edit.is_deletion,
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

    result: dict[str, Any] = {
        "stopReason": report.stop_reason,
        "steps": [_execution_step_to_dict(step) for step in report.steps],
        "patches": patches,
        "commands": commands,
        "replansUsed": report.replans_used,
    }

    # Additive, like `commands` above: clients that ignore these fields
    # are unaffected. Both are absent rather than faked when they did
    # not run, so a client can distinguish "verified clean" from
    # "never checked".
    if report.error:
        result["error"] = report.error

    verification = getattr(executor, "_last_verification", None)
    if verification is not None:
        result["verification"] = {
            "status": verification.get("status"),
            "testsRun": verification.get("tests_run", 0),
            "testsPassed": verification.get("tests_passed", 0),
            "testsFailed": verification.get("tests_failed", 0),
            "changedFiles": verification.get("changed_files", []),
            "unexpectedFiles": verification.get("unexpected_files", []),
        }

    reflection = report.llm_reflection
    if reflection is not None:
        result["reflection"] = {
            "status": reflection.status,
            "confidence": reflection.confidence,
            "reason": reflection.reason,
            "missingRequirements": list(reflection.missing_requirements),
        }

    return result


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
        chat_llm: LLMClient | None = None,
        personality: PersonalityManager | None = None,
        checkpoints: CheckpointManager | None = None,
    ) -> None:
        self.registry = registry
        self.dispatcher = dispatcher or ToolDispatcher(registry)
        self.planner = planner
        self.memory = memory or Memory()
        # `llm` is the planning/general LLM — kept for backward compat
        # (tests inject it directly). `chat_llm`, when provided, is used
        # for pearl/chat responses so a faster or cheaper model can serve
        # conversational turns while a more capable model handles planning.
        self.llm = llm
        self._chat_llm = chat_llm
        self._personality = personality or PersonalityManager()
        # One store per server, shared between the manual
        # pearl/checkpoint* methods and the automatic pre-write
        # checkpoint AutonomousExecutor takes on approval — see
        # _run_autonomous, which passes this same instance in rather
        # than letting the executor build its own default, so a
        # manual checkpoint and an auto checkpoint show up in the same
        # list.
        self.checkpoints = checkpoints or CheckpointManager()
        self._autonomous_executor: AutonomousExecutor | None = None
        # Inline completion. Constructing this loads no model — the
        # autocomplete client is resolved on the first actual request, so
        # a server whose client never asks for completions pays nothing.
        self._completion_service = CompletionService()

    # -- V2 loop components -------------------------------------------------

    def _build_verifier(self) -> "VerificationEngine | None":
        """
        Verification engine for the current workspace, or None if it
        cannot be constructed.

        Best-effort: a workspace where verification cannot run (no git,
        no tests) must still be able to execute tasks — losing the
        post-apply check is a degradation, refusing to run is a
        regression.
        """
        try:
            from src.agent.verification import VerificationEngine
            from src.config.workspace import get_workspace_root

            return VerificationEngine(workspace_root=get_workspace_root())
        except Exception:
            logger.warning("Verification unavailable for this run.", exc_info=True)
            return None

    def _build_reflection_engine(self) -> "ReflectionEngine | None":
        """
        Reflection engine, or None when no LLM is available.

        Reflection needs a model; without one the executor falls back to
        its heuristic reflection rather than failing.
        """
        try:
            from src.agent.reflection import ReflectionEngine

            client = self._chat_llm or self.llm
            if client is None:
                return None
            return ReflectionEngine(client)
        except Exception:
            logger.warning("Reflection unavailable for this run.", exc_info=True)
            return None

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
        experimental: dict[str, Any] = {
            "pearlPersonality": {},
            "pearlCheckpoints": {},
        }

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

    # `pearl/plan` used to be handled here (`_plan_run`), executing a
    # planned sequence of tool calls directly via the dispatcher, one
    # after another, with no replanning. Removed as a confirmed
    # safety bug, not a style cleanup: dispatching this way runs
    # completely outside AutonomousExecutor, so no ChangeManager is
    # ever active, and every write tool (create_file, etc.) falls
    # through to writing straight to disk with zero approval —
    # verified live by calling the dispatcher the same way this
    # handler did and watching the file appear unreviewed. The VS
    # Code extension never called this method (only Pearl's own
    # tests did). `pearl/runAutonomous` is the only path that stages
    # writes behind approval and is what any client should use.

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

    def _complete(self, params: dict[str, Any], notify: NotifyFn) -> dict[str, Any]:
        """
        Return one inline completion for the text at a cursor position.

        Unlike every other method here, this is on the typing path: it is
        called as the developer types and must return quickly or not at
        all. It therefore never raises for a model failure — an
        unavailable model yields an empty completion with a reason, since
        an error popup mid-keystroke is worse than no suggestion.

        `prefix` is required; `suffix` and `language` refine the result.
        """
        prefix = params.get("prefix")

        if not isinstance(prefix, str):
            raise MCPProtocolError(INVALID_PARAMS, "'prefix' is required.")

        suffix = params.get("suffix") or ""
        language = params.get("language") or ""

        if not isinstance(suffix, str) or not isinstance(language, str):
            raise MCPProtocolError(
                INVALID_PARAMS, "'suffix' and 'language' must be strings."
            )

        result = self._completion_service.complete(
            prefix=prefix, suffix=suffix, language=language
        )

        return {
            "completion": result.text,
            "cached": result.cached,
            "declinedReason": result.declined_reason,
        }

    def _chat(self, params: dict[str, Any], notify: NotifyFn) -> dict[str, Any]:
        """
        Chat directly with the language model (a Pearl-specific
        extension beyond the core MCP methods).

        Streams the response via `pearl/chatChunk` notifications so the
        UI can render text as it arrives rather than waiting for the
        complete response. The final JSON-RPC result carries the full
        accumulated text so callers that don't use notifications still
        work correctly.
        """

        message = params.get("message")

        if not isinstance(message, str) or not message:
            raise MCPProtocolError(INVALID_PARAMS, "'message' is required.")

        # Prefer the dedicated chat LLM when one was configured via
        # ModelRouter; fall back to the general LLM (or build one lazily).
        if self._chat_llm is not None:
            active_llm = self._chat_llm
        else:
            if self.llm is None:
                self.llm = LLMClient()
            active_llm = self.llm

        history = self.memory.recent_messages(limit=Settings.CHAT_HISTORY_TURNS)

        self.memory.record_turn("user", message)

        chunks: list[str] = []

        try:
            for chunk in active_llm.generate_stream(
                message,
                history=history,
                system=build_chat_system_prompt(str(Path.cwd().resolve())),
            ):
                chunks.append(chunk)
                notify("pearl/chatChunk", {"chunk": chunk})
        except ProviderAuthError as exc:
            # The LLM layer already classified this and wrote a message
            # that fits whichever provider the router actually resolved.
            # This used to catch `openai.OpenAIError` and string-match on
            # it, which meant the helpful setup message never fired for a
            # Claude, Gemini or local user — and it put a vendor SDK
            # import in the protocol layer, against Rule LLM-1.
            raise MCPProtocolError(INTERNAL_ERROR, str(exc)) from exc

        response = "".join(chunks)

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
        `AutonomousExecutor`/`ChangeManager` — nothing is duplicated
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
            checkpoints=self.checkpoints,
            # Same V2 wiring the HTTP session uses: without these the
            # extension can only report that tools ran, never whether
            # the task actually succeeded.
            verifier=self._build_verifier(),
            reflection_engine=self._build_reflection_engine(),
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

    def _checkpoint_create(
        self, params: dict[str, Any], notify: NotifyFn
    ) -> dict[str, Any]:
        """
        Snapshot the workspace on demand (a Pearl-specific extension
        beyond the core MCP methods) — the same store, and the same
        `CheckpointManager`, that `AutonomousExecutor.approve()`
        checkpoints into automatically before writing.
        """

        label = params.get("label")

        if label is not None and not isinstance(label, str):
            raise MCPProtocolError(INVALID_PARAMS, "'label' must be a string.")

        try:
            checkpoint = self.checkpoints.create(label or "Manual checkpoint")
        except CheckpointError as exc:
            raise MCPProtocolError(INVALID_PARAMS, str(exc)) from exc

        return {
            "checkpoint": (
                _checkpoint_to_dict(checkpoint) if checkpoint is not None else None
            )
        }

    def _checkpoints_list(
        self, params: dict[str, Any], notify: NotifyFn
    ) -> dict[str, Any]:
        """
        List checkpoints for the current workspace, newest first.
        """

        limit = params.get("limit", 50)

        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise MCPProtocolError(INVALID_PARAMS, "'limit' must be a positive int.")

        checkpoints = self.checkpoints.list(limit=limit)

        return {"checkpoints": [_checkpoint_to_dict(c) for c in checkpoints]}

    def _checkpoint_restore_preview(
        self, params: dict[str, Any], notify: NotifyFn
    ) -> dict[str, Any]:
        """
        Report what restoring a checkpoint would change, without
        changing anything — restoring can delete files created since
        the checkpoint, so a client should show this before the user
        confirms, the same way Pearl shows a diff before writing.
        """

        checkpoint_id = self._require_checkpoint_id(params)

        try:
            report = self.checkpoints.preview_restore(checkpoint_id)
        except CheckpointError as exc:
            raise MCPProtocolError(INVALID_PARAMS, str(exc)) from exc

        return _restore_report_to_dict(report)

    def _checkpoint_restore(
        self, params: dict[str, Any], notify: NotifyFn
    ) -> dict[str, Any]:
        """
        Restore the workspace to a checkpoint.
        """

        checkpoint_id = self._require_checkpoint_id(params)

        try:
            report = self.checkpoints.restore(checkpoint_id)
        except CheckpointError as exc:
            raise MCPProtocolError(INVALID_PARAMS, str(exc)) from exc

        return _restore_report_to_dict(report)

    def _checkpoint_delete(
        self, params: dict[str, Any], notify: NotifyFn
    ) -> dict[str, Any]:
        """
        Delete a checkpoint (hides it from listing and future
        restores — see `CheckpointManager.delete` for why this never
        rewrites git history).
        """

        checkpoint_id = self._require_checkpoint_id(params)

        try:
            self.checkpoints.delete(checkpoint_id)
        except CheckpointError as exc:
            raise MCPProtocolError(INVALID_PARAMS, str(exc)) from exc

        return {"deleted": True}

    def _checkpoint_rename(
        self, params: dict[str, Any], notify: NotifyFn
    ) -> dict[str, Any]:
        """
        Change a checkpoint's display label.
        """

        checkpoint_id = self._require_checkpoint_id(params)
        label = params.get("label")

        if not isinstance(label, str) or not label:
            raise MCPProtocolError(INVALID_PARAMS, "'label' is required.")

        try:
            checkpoint = self.checkpoints.rename(checkpoint_id, label)
        except CheckpointError as exc:
            raise MCPProtocolError(INVALID_PARAMS, str(exc)) from exc

        return {"checkpoint": _checkpoint_to_dict(checkpoint)}

    @staticmethod
    def _require_checkpoint_id(params: dict[str, Any]) -> str:
        checkpoint_id = params.get("id")

        if not isinstance(checkpoint_id, str) or not checkpoint_id:
            raise MCPProtocolError(INVALID_PARAMS, "'id' is required.")

        return checkpoint_id

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
        "pearl/planOnly": _plan_only,
        "pearl/chat": _chat,
        "pearl/complete": _complete,
        "pearl/runAutonomous": _run_autonomous,
        "pearl/approvePatches": _approve_patches,
        "pearl/rejectPatches": _reject_patches,
        "pearl/memory": _memory,
        "pearl/personality": _personality_labels,
        "pearl/checkpointCreate": _checkpoint_create,
        "pearl/checkpoints": _checkpoints_list,
        "pearl/checkpointRestorePreview": _checkpoint_restore_preview,
        "pearl/checkpointRestore": _checkpoint_restore,
        "pearl/checkpointDelete": _checkpoint_delete,
        "pearl/checkpointRename": _checkpoint_rename,
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
