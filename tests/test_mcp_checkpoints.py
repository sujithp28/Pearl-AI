"""
Integration tests for the `pearl/checkpoint*` MCP methods (Sprint 1:
the Checkpoint System).

Exercises `MCPServer.handle_request` directly (no subprocess),
matching the convention in `test_mcp_server.py`/`test_mcp_autonomous.py`,
against a real `CheckpointManager` over a real temp workspace — this
is a thin JSON translation layer over `src/tools/checkpoints.py`, so
these tests focus on the translation (params, camelCase response
shape, error mapping) and on the manual/automatic checkpoint stores
being genuinely shared, not on re-testing the git mechanics already
covered by `tests/test_checkpoints.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agent.dispatcher import ToolDispatcher
from src.agent.planner import Planner
from src.mcp.protocol import INVALID_PARAMS, JsonRpcRequest
from src.mcp.server import MCPServer
from src.memory import Memory
from src.tools.checkpoints import CheckpointManager
from src.tools.edit_tools import create_file
from src.tools.registry import ToolRegistry


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "a.txt").write_text("original\n")
    monkeypatch.chdir(ws)
    return ws


@pytest.fixture
def pearl_home(tmp_path):
    return tmp_path / "pearl_home"


def build_server(workspace: Path, pearl_home: Path, with_planner: bool = True):
    registry = ToolRegistry()
    registry.register(create_file)
    dispatcher = ToolDispatcher(registry)
    planner = Planner(registry, dispatcher) if with_planner else None

    return MCPServer(
        registry,
        dispatcher=dispatcher,
        planner=planner,
        memory=Memory(),
        checkpoints=CheckpointManager(workspace, pearl_home=pearl_home),
    )


# ---------------------------------------------------------------------
# pearl/checkpointCreate
# ---------------------------------------------------------------------


def test_checkpoint_create_returns_a_checkpoint(workspace, pearl_home):
    server = build_server(workspace, pearl_home)

    response = server.handle_request(
        JsonRpcRequest(method="pearl/checkpointCreate", id=1, params={})
    )

    assert response.error is None
    checkpoint = response.result["checkpoint"]
    assert checkpoint["label"] == "Manual checkpoint"
    assert len(checkpoint["id"]) == 40
    assert checkpoint["shortId"] == checkpoint["id"][:8]
    assert "createdAt" in checkpoint


def test_checkpoint_create_uses_the_given_label(workspace, pearl_home):
    server = build_server(workspace, pearl_home)

    response = server.handle_request(
        JsonRpcRequest(
            method="pearl/checkpointCreate", id=1, params={"label": "before refactor"}
        )
    )

    assert response.result["checkpoint"]["label"] == "before refactor"


def test_checkpoint_create_returns_null_when_nothing_changed(workspace, pearl_home):
    server = build_server(workspace, pearl_home)

    server.handle_request(
        JsonRpcRequest(method="pearl/checkpointCreate", id=1, params={})
    )
    response = server.handle_request(
        JsonRpcRequest(method="pearl/checkpointCreate", id=2, params={})
    )

    assert response.result["checkpoint"] is None


def test_checkpoint_create_rejects_a_non_string_label(workspace, pearl_home):
    server = build_server(workspace, pearl_home)

    response = server.handle_request(
        JsonRpcRequest(method="pearl/checkpointCreate", id=1, params={"label": 5})
    )

    assert response.error.code == INVALID_PARAMS


# ---------------------------------------------------------------------
# pearl/checkpoints (list)
# ---------------------------------------------------------------------


def test_checkpoints_list_is_empty_before_any_checkpoint(workspace, pearl_home):
    server = build_server(workspace, pearl_home)

    response = server.handle_request(
        JsonRpcRequest(method="pearl/checkpoints", id=1, params={})
    )

    assert response.result["checkpoints"] == []


def test_checkpoints_list_is_newest_first(workspace, pearl_home):
    server = build_server(workspace, pearl_home)

    server.handle_request(
        JsonRpcRequest(
            method="pearl/checkpointCreate", id=1, params={"label": "oldest"}
        )
    )
    (workspace / "a.txt").write_text("v2\n")
    server.handle_request(
        JsonRpcRequest(
            method="pearl/checkpointCreate", id=2, params={"label": "newest"}
        )
    )

    response = server.handle_request(
        JsonRpcRequest(method="pearl/checkpoints", id=3, params={})
    )

    labels = [c["label"] for c in response.result["checkpoints"]]
    assert labels == ["newest", "oldest"]


def test_checkpoints_list_rejects_a_non_positive_limit(workspace, pearl_home):
    server = build_server(workspace, pearl_home)

    response = server.handle_request(
        JsonRpcRequest(method="pearl/checkpoints", id=1, params={"limit": 0})
    )

    assert response.error.code == INVALID_PARAMS


# ---------------------------------------------------------------------
# pearl/checkpointRestorePreview / pearl/checkpointRestore
# ---------------------------------------------------------------------


def test_checkpoint_restore_preview_reports_without_changing_anything(
    workspace, pearl_home
):
    server = build_server(workspace, pearl_home)

    created = server.handle_request(
        JsonRpcRequest(method="pearl/checkpointCreate", id=1, params={})
    )
    checkpoint_id = created.result["checkpoint"]["id"]

    (workspace / "a.txt").write_text("MODIFIED\n")

    response = server.handle_request(
        JsonRpcRequest(
            method="pearl/checkpointRestorePreview",
            id=2,
            params={"id": checkpoint_id},
        )
    )

    assert response.result["restored"] == ["a.txt"]
    assert response.result["changedAnything"] is True
    assert (workspace / "a.txt").read_text() == "MODIFIED\n"


def test_checkpoint_restore_actually_restores(workspace, pearl_home):
    server = build_server(workspace, pearl_home)

    created = server.handle_request(
        JsonRpcRequest(method="pearl/checkpointCreate", id=1, params={})
    )
    checkpoint_id = created.result["checkpoint"]["id"]

    (workspace / "a.txt").write_text("MODIFIED\n")

    response = server.handle_request(
        JsonRpcRequest(
            method="pearl/checkpointRestore", id=2, params={"id": checkpoint_id}
        )
    )

    assert response.result["restored"] == ["a.txt"]
    assert (workspace / "a.txt").read_text() == "original\n"


def test_checkpoint_restore_unknown_id_is_a_protocol_error(workspace, pearl_home):
    server = build_server(workspace, pearl_home)
    server.handle_request(
        JsonRpcRequest(method="pearl/checkpointCreate", id=1, params={})
    )

    response = server.handle_request(
        JsonRpcRequest(method="pearl/checkpointRestore", id=2, params={"id": "0" * 40})
    )

    assert response.error.code == INVALID_PARAMS


def test_checkpoint_restore_missing_id_is_a_protocol_error(workspace, pearl_home):
    server = build_server(workspace, pearl_home)

    response = server.handle_request(
        JsonRpcRequest(method="pearl/checkpointRestore", id=1, params={})
    )

    assert response.error.code == INVALID_PARAMS


# ---------------------------------------------------------------------
# pearl/checkpointDelete
# ---------------------------------------------------------------------


def test_checkpoint_delete_removes_it_from_listing(workspace, pearl_home):
    server = build_server(workspace, pearl_home)

    created = server.handle_request(
        JsonRpcRequest(method="pearl/checkpointCreate", id=1, params={})
    )
    checkpoint_id = created.result["checkpoint"]["id"]

    response = server.handle_request(
        JsonRpcRequest(
            method="pearl/checkpointDelete", id=2, params={"id": checkpoint_id}
        )
    )

    assert response.result == {"deleted": True}

    listed = server.handle_request(
        JsonRpcRequest(method="pearl/checkpoints", id=3, params={})
    )
    assert listed.result["checkpoints"] == []


def test_checkpoint_delete_unknown_id_is_a_protocol_error(workspace, pearl_home):
    server = build_server(workspace, pearl_home)

    response = server.handle_request(
        JsonRpcRequest(method="pearl/checkpointDelete", id=1, params={"id": "0" * 40})
    )

    assert response.error.code == INVALID_PARAMS


# ---------------------------------------------------------------------
# pearl/checkpointRename
# ---------------------------------------------------------------------


def test_checkpoint_rename_changes_the_label(workspace, pearl_home):
    server = build_server(workspace, pearl_home)

    created = server.handle_request(
        JsonRpcRequest(
            method="pearl/checkpointCreate", id=1, params={"label": "original"}
        )
    )
    checkpoint_id = created.result["checkpoint"]["id"]

    response = server.handle_request(
        JsonRpcRequest(
            method="pearl/checkpointRename",
            id=2,
            params={"id": checkpoint_id, "label": "renamed"},
        )
    )

    assert response.result["checkpoint"]["label"] == "renamed"


def test_checkpoint_rename_missing_label_is_a_protocol_error(workspace, pearl_home):
    server = build_server(workspace, pearl_home)

    created = server.handle_request(
        JsonRpcRequest(method="pearl/checkpointCreate", id=1, params={})
    )
    checkpoint_id = created.result["checkpoint"]["id"]

    response = server.handle_request(
        JsonRpcRequest(
            method="pearl/checkpointRename", id=2, params={"id": checkpoint_id}
        )
    )

    assert response.error.code == INVALID_PARAMS


# ---------------------------------------------------------------------
# initialize advertises the capability
# ---------------------------------------------------------------------


def test_initialize_advertises_checkpoints_capability(workspace, pearl_home):
    server = build_server(workspace, pearl_home)

    response = server.handle_request(JsonRpcRequest(method="initialize", id=1))

    assert "pearlCheckpoints" in response.result["capabilities"]["experimental"]


# ---------------------------------------------------------------------
# Manual and automatic checkpoints share one store
# ---------------------------------------------------------------------


def test_manual_and_automatic_checkpoints_share_the_same_store(
    workspace, pearl_home, monkeypatch
):
    """
    AutonomousExecutor.approve() already checkpoints automatically
    before writing (Sprint 0). This must be the same store the manual
    pearl/checkpoint* methods read and write, not a separate one the
    executor builds for itself — otherwise a checkpoint taken during
    an autonomous run would be invisible to pearl/checkpoints, and a
    manual checkpoint invisible to whatever the executor uses for
    undo.
    """

    server = build_server(workspace, pearl_home)

    server.handle_request(
        JsonRpcRequest(
            method="pearl/checkpointCreate", id=1, params={"label": "manual"}
        )
    )

    # Something must actually change on disk between the manual
    # checkpoint and the auto-checkpoint, or the auto-checkpoint has
    # nothing new to capture and correctly returns None (no "Before:"
    # entry) — that's not a bug, just not what this test is checking.
    (workspace / "a.txt").write_text("changed before the autonomous run\n")

    monkeypatch.setattr(
        server.planner.client,
        "generate_json",
        lambda prompt, cancel_check=None: {
            "steps": [
                {
                    "tool": "create_file",
                    "arguments": {"path": "new.py", "content": "x = 1\n"},
                }
            ]
        },
    )

    server.handle_request(
        JsonRpcRequest(
            method="pearl/runAutonomous", id=2, params={"prompt": "create a file"}
        )
    )
    server.handle_request(
        JsonRpcRequest(method="pearl/approvePatches", id=3, params={})
    )

    listed = server.handle_request(
        JsonRpcRequest(method="pearl/checkpoints", id=4, params={})
    )

    labels = [c["label"] for c in listed.result["checkpoints"]]
    assert "manual" in labels
    assert any(label.startswith("Before:") for label in labels)
