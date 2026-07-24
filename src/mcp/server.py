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

import logging
import sys
from typing import IO, Any

from src.agent.dispatcher import ToolDispatcher, ToolExecutionError, ToolNotFoundError
from src.agent.planner import Planner
from src.llm.client import LLMClient
from src.mcp.protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    MCP_PROTOCOL_VERSION,
    JsonRpcError,
    JsonRpcRequest,
    JsonRpcResponse,
    MCPProtocolError,
    parse_request,
    tool_to_mcp_schema,
)
from src.memory import Memory
from src.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

SERVER_NAME = "pearl-mcp"
SERVER_VERSION = "0.1.0"


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
    ) -> None:
        self.registry = registry
        self.dispatcher = dispatcher or ToolDispatcher(registry)
        self.planner = planner
        self.memory = memory or Memory()
        self.llm = llm

    # -- Request handling ---------------------------------------------------

    def handle_request(
        self,
        request: JsonRpcRequest,
    ) -> JsonRpcResponse | None:
        """
        Dispatch one JSON-RPC request. Returns None for notifications
        (which never receive a response).
        """

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
                handler(self, request.params)
            except Exception:
                logger.exception(
                    "Error handling notification: %s", request.method
                )
            return None

        try:
            result = handler(self, request.params)
        except MCPProtocolError as exc:
            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(exc.code, exc.message),
            )
        except Exception as exc:
            logger.exception(
                "Internal error handling method: %s", request.method
            )
            return JsonRpcResponse(
                id=request.id,
                error=JsonRpcError(INTERNAL_ERROR, str(exc)),
            )

        return JsonRpcResponse(id=request.id, result=result)

    # -- Method implementations ----------------------------------------------

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        """
        Handle the `initialize` handshake.
        """

        capabilities: dict[str, Any] = {"tools": {}}
        experimental: dict[str, Any] = {}

        if self.planner is not None:
            experimental["pearlPlanning"] = {}

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

    def _tools_list(self, params: dict[str, Any]) -> dict[str, Any]:
        """
        List every tool currently in the registry.
        """

        return {
            "tools": [
                tool_to_mcp_schema(tool) for tool in self.registry
            ]
        }

    def _tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
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
            raise MCPProtocolError(
                INVALID_PARAMS, "'arguments' must be an object."
            )

        try:
            result = self.dispatcher.execute(name, **arguments)
        except (ToolNotFoundError, ToolExecutionError) as exc:
            self.memory.record_execution(name, arguments, error=str(exc))

            return {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            }

        self.memory.record_execution(name, arguments, result=result)

        return {
            "content": [{"type": "text", "text": str(result)}],
            "isError": False,
        }

    def _plan_run(self, params: dict[str, Any]) -> dict[str, Any]:
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
                result=step.result,
                error=step.error,
            )

        status = (
            "completed"
            if all(step.succeeded for step in results)
            else "failed"
        )
        self.memory.complete_task(task.id, status=status)

        return {
            "steps": [
                {
                    "tool": step.tool_name,
                    "arguments": step.kwargs,
                    "result": step.result if step.succeeded else None,
                    "error": step.error,
                }
                for step in results
            ]
        }

    def _plan_only(self, params: dict[str, Any]) -> dict[str, Any]:
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
                {"tool": step.tool_name, "arguments": step.kwargs}
                for step in steps
            ]
        }

    def _chat(self, params: dict[str, Any]) -> dict[str, Any]:
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

        self.memory.record_turn("user", message)

        response = self.llm.generate(message)

        self.memory.record_turn("agent", response)

        return {"message": response}

    def _memory(self, params: dict[str, Any]) -> dict[str, Any]:
        """
        Return the current contents of Memory (a Pearl-specific
        extension for inspecting agent state): conversation history,
        task history, execution history, and project facts.

        Read-only: reuses `Memory.to_dict()` exactly as-is; nothing
        here mutates Memory or the underlying `Memory` implementation.
        """

        return self.memory.to_dict()

    def _shutdown(self, params: dict[str, Any]) -> None:
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
        "pearl/memory": _memory,
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
        output_stream = (
            output_stream if output_stream is not None else sys.stdout
        )

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

        response = self.handle_request(request)

        if response is not None:
            output_stream.write(response.to_json() + "\n")
            output_stream.flush()

        return True
