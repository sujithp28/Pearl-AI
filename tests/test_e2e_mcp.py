"""
End-to-end tests: a real `python -m src.mcp` subprocess, driven over
real stdio with real JSON-RPC bytes.

Why this file exists. Pearl had ~780 passing tests and still shipped a
timeout constant far too small for a real model call, because every
test replaced the transport with a fake and never measured anything
real. That is the blind spot this closes: here the *only* thing faked
is the model itself (`ScriptedProvider`, selected via
`PEARL_LLM_PROVIDER=scripted`), because a model's output is
genuinely non-deterministic and can't be asserted on. Everything else
runs for real:

  - a real Python subprocess, started the way the VS Code extension
    starts it
  - real stdout/stdin pipes and real newline-delimited framing
  - the real MCPServer, Planner, ToolDispatcher, tool registry
  - the real PatchManager approval gate, writing to a real temp dir

These are slower than unit tests (subprocess startup, real indexing)
but there are only a handful, and they exercise the seams no in-process
test can reach.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


class MCPProcess:
    """
    A real Pearl MCP server subprocess, spoken to over real pipes.
    """

    def __init__(self, workspace: Path, scripted_responses: list[str]) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT)
        env["PEARL_LLM_PROVIDER"] = "scripted"
        env["PEARL_SCRIPTED_RESPONSES"] = json.dumps(scripted_responses)
        # Keep the run hermetic: no personality wording drift, and no
        # inherited .env pointing at a real model server.
        env["PERSONALITY"] = "professional"
        env["EMOJI_MODE"] = "none"

        self.process = subprocess.Popen(
            [sys.executable, "-m", "src.mcp"],
            cwd=str(workspace),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._next_id = 0

    def request(self, method: str, params: dict | None = None) -> dict:
        """
        Send one JSON-RPC request and return its response, skipping
        any notifications (e.g. `pearl/progress`) that arrive first.
        """

        self._next_id += 1
        request_id = self._next_id

        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }

        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

        return self._read_response(request_id)

    def _read_response(self, request_id: int) -> dict:
        assert self.process.stdout is not None

        while True:
            line = self.process.stdout.readline()

            if not line:
                stderr = ""
                if self.process.stderr is not None:
                    stderr = self.process.stderr.read()
                raise AssertionError(
                    f"server closed stdout before answering id={request_id}.\n"
                    f"stderr:\n{stderr}"
                )

            line = line.strip()

            if not line:
                continue

            message = json.loads(line)

            # Notifications carry no id — skip past them to the reply.
            if message.get("id") == request_id:
                return message

    def notifications(self, method: str, params: dict | None = None) -> list[dict]:
        """
        Send a request and return every notification received before
        its response, so streaming can be asserted on for real.
        """

        self._next_id += 1
        request_id = self._next_id

        assert self.process.stdin is not None
        self.process.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params or {},
                }
            )
            + "\n"
        )
        self.process.stdin.flush()

        assert self.process.stdout is not None
        collected: list[dict] = []

        while True:
            line = self.process.stdout.readline()

            if not line:
                raise AssertionError("server closed stdout mid-request")

            line = line.strip()

            if not line:
                continue

            message = json.loads(line)

            if message.get("id") == request_id:
                return collected

            collected.append(message)

    def close(self) -> None:
        try:
            if self.process.stdin is not None and not self.process.stdin.closed:
                self.process.stdin.write(
                    json.dumps({"jsonrpc": "2.0", "method": "exit"}) + "\n"
                )
                self.process.stdin.flush()
            self.process.wait(timeout=15)
        except Exception:
            self.process.kill()
            self.process.wait(timeout=15)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "existing.py").write_text("def already_here():\n    return 1\n")
    return tmp_path


def _server(workspace: Path, responses: list[str]) -> MCPProcess:
    return MCPProcess(workspace, responses)


# ---------------------------------------------------------------------
# The transport itself
# ---------------------------------------------------------------------


def test_real_subprocess_answers_initialize(workspace):
    server = _server(workspace, ["unused"])

    try:
        response = server.request("initialize")

        assert response["result"]["serverInfo"]["name"] == "pearl-mcp"
        assert "protocolVersion" in response["result"]
    finally:
        server.close()


def test_real_subprocess_lists_its_real_tools(workspace):
    server = _server(workspace, ["unused"])

    try:
        server.request("initialize")
        response = server.request("tools/list")

        names = {tool["name"] for tool in response["result"]["tools"]}

        # The genuine registry from src.main.build_registry(), not a
        # fixture — if tool registration breaks, this catches it.
        assert {"read_file", "create_file", "git_status"} <= names
    finally:
        server.close()


def test_malformed_line_is_answered_without_killing_the_server(workspace):
    """
    A parse error must not take the connection down — the next
    request has to still work.
    """

    server = _server(workspace, ["unused"])

    try:
        server.request("initialize")

        assert server.process.stdin is not None
        server.process.stdin.write("this is not json\n")
        server.process.stdin.flush()

        # Skip the error response for the malformed line, then prove
        # the server is still alive and serving.
        assert server.process.stdout is not None
        server.process.stdout.readline()

        response = server.request("tools/list")
        assert "result" in response
    finally:
        server.close()


# ---------------------------------------------------------------------
# Real tool execution against a real filesystem
# ---------------------------------------------------------------------


def test_tools_call_really_reads_a_real_file(workspace):
    server = _server(workspace, ["unused"])

    try:
        server.request("initialize")

        response = server.request(
            "tools/call",
            {"name": "read_file", "arguments": {"path": "existing.py"}},
        )

        assert response["result"]["isError"] is False
        assert "already_here" in response["result"]["content"][0]["text"]
    finally:
        server.close()


def test_workspace_boundary_is_enforced_in_a_real_subprocess(workspace):
    """
    The security boundary, verified against a real process with a real
    cwd — not a monkeypatched `Path.cwd()`.
    """

    server = _server(workspace, ["unused"])

    try:
        server.request("initialize")

        response = server.request(
            "tools/call",
            {"name": "read_file", "arguments": {"path": "/etc/passwd"}},
        )

        assert response["result"]["isError"] is True
        assert "escapes workspace" in response["result"]["content"][0]["text"]
    finally:
        server.close()


# ---------------------------------------------------------------------
# The full autonomous loop: plan -> stage -> approve -> disk
# ---------------------------------------------------------------------


def test_autonomous_run_stages_a_patch_without_writing_it(workspace):
    plan = json.dumps(
        {
            "steps": [
                {
                    "tool": "create_file",
                    "arguments": {"path": "generated.py", "content": "x = 1\n"},
                }
            ]
        }
    )

    server = _server(workspace, [plan])

    try:
        server.request("initialize")

        response = server.request(
            "pearl/runAutonomous", {"prompt": "create generated.py"}
        )

        assert response["result"]["stopReason"] == "awaiting_approval"
        assert len(response["result"]["patches"]) == 1
        # The actual guarantee: nothing on disk yet.
        assert not (workspace / "generated.py").exists()
    finally:
        server.close()


def test_approval_really_writes_the_file_to_disk(workspace):
    plan = json.dumps(
        {
            "steps": [
                {
                    "tool": "create_file",
                    "arguments": {"path": "generated.py", "content": "x = 1\n"},
                }
            ]
        }
    )

    server = _server(workspace, [plan])

    try:
        server.request("initialize")
        server.request("pearl/runAutonomous", {"prompt": "create generated.py"})

        response = server.request("pearl/approvePatches")

        assert response["result"]["stopReason"] == "completed"
        assert (workspace / "generated.py").read_text() == "x = 1\n"
    finally:
        server.close()


def test_rejection_really_leaves_the_disk_untouched(workspace):
    plan = json.dumps(
        {
            "steps": [
                {
                    "tool": "create_file",
                    "arguments": {"path": "generated.py", "content": "x = 1\n"},
                }
            ]
        }
    )

    server = _server(workspace, [plan])

    try:
        server.request("initialize")
        server.request("pearl/runAutonomous", {"prompt": "create generated.py"})

        response = server.request("pearl/rejectPatches")

        assert response["result"]["stopReason"] == "rejected"
        assert not (workspace / "generated.py").exists()
    finally:
        server.close()


# ---------------------------------------------------------------------
# Streaming, over the real wire
# ---------------------------------------------------------------------


def test_progress_notifications_really_arrive_before_the_response(workspace):
    """
    The M1 streaming feature, verified as actual interleaved bytes on
    a real pipe rather than as a captured callback.
    """

    plan = json.dumps({"steps": [{"tool": "pwd", "arguments": {}}]})

    server = _server(workspace, [plan])

    try:
        server.request("initialize")

        notifications = server.notifications(
            "pearl/runAutonomous", {"prompt": "where am i"}
        )

        assert notifications, "expected pearl/progress notifications"
        assert all(n["method"] == "pearl/progress" for n in notifications)
        assert all("id" not in n for n in notifications)

        statuses = [n["params"]["status"] for n in notifications]
        assert "planning" in statuses
    finally:
        server.close()
