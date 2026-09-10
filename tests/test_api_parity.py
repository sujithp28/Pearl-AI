"""
HTTP parity with the MCP surface: checkpoints, memory, plan, personality.

The VS Code extension has reached these through JSON-RPC since they were
built. The browser could not, because the HTTP adapter never grew the
routes. These tests cover the adapter, not the machinery underneath it,
which `tests/test_checkpoints.py` already covers against real git.

`Path.home` is redirected for every test here. The checkpoint store
lives at `~/.pearl/workspaces/<key>`, and a test suite that writes into
a real developer's home directory is a test suite that fails on the
second run.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A scratch workspace with the checkpoint store redirected into it."""
    ws = tmp_path / "project"
    ws.mkdir()
    (ws / "main.py").write_text("print('hello')\n", encoding="utf-8")

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    return ws


@pytest.fixture
def server(workspace, monkeypatch):
    from src.api import server as srv

    monkeypatch.setattr(srv, "_session", None)
    monkeypatch.setattr(srv, "_workspace", None)
    srv._registry.clear()
    srv.init_session(workspace)
    return srv


@pytest.fixture
def client(server):
    with TestClient(server.app) as c:
        yield c


def _snapshot(root: Path) -> dict[str, bytes]:
    """Every file under `root`, by relative path, for byte comparison."""
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


class TestCheckpointManagerBinding:
    """
    The session's checkpoint manager must target the session's
    workspace.

    It used to be built as a bare `CheckpointManager()`, which defaults
    to the process working directory. That is the same directory only
    when Pearl is launched from inside the project, so it looked correct
    in ordinary use and silently snapshotted the wrong tree whenever
    `--workspace` pointed elsewhere, or after the workspace was changed
    from the settings dialog.
    """

    def test_manager_targets_the_session_workspace(self, server, workspace):
        assert server.get_session().checkpoints.workspace == workspace.resolve()

    def test_changing_the_workspace_rebinds_the_manager(self, server, tmp_path):
        other = tmp_path / "other"
        other.mkdir()

        with TestClient(server.app) as client:
            r = client.post("/api/workspace", json={"path": str(other)})

        assert r.status_code == 200
        assert server.get_session().checkpoints.workspace == other.resolve()


class TestCheckpointList:
    def test_empty_workspace_lists_nothing(self, client):
        r = client.get("/api/checkpoints")

        assert r.status_code == 200
        assert r.json() == {"checkpoints": []}

    def test_created_checkpoint_appears_with_protocol_keys(self, server, client):
        server.get_session().checkpoints.create("Before the edit")

        body = client.get("/api/checkpoints").json()

        assert len(body["checkpoints"]) == 1
        assert set(body["checkpoints"][0]) == {"id", "shortId", "label", "createdAt"}
        assert body["checkpoints"][0]["label"] == "Before the edit"

    def test_limit_is_applied(self, server, workspace, client):
        session = server.get_session()
        for i in range(3):
            (workspace / f"f{i}.py").write_text(f"# {i}\n", encoding="utf-8")
            session.checkpoints.create(f"Checkpoint {i}")

        body = client.get("/api/checkpoints", params={"limit": 2}).json()

        assert len(body["checkpoints"]) == 2

    @pytest.mark.parametrize("bad", ["0", "-1", "abc"])
    def test_a_bad_limit_is_rejected(self, client, bad):
        """
        422 rather than the 400 used elsewhere here: this is request
        validation, which FastAPI does before the handler runs, whereas
        400 is reserved for a well-formed request the checkpoint store
        then refuses.
        """
        assert client.get("/api/checkpoints", params={"limit": bad}).status_code == 422


class TestRestorePreview:
    def test_reports_what_would_change(self, server, workspace, client):
        session = server.get_session()
        cp = session.checkpoints.create("Before the edit")
        (workspace / "main.py").write_text("print('changed')\n", encoding="utf-8")

        body = client.get(f"/api/checkpoints/{cp.id}/restore-preview").json()

        assert set(body) == {"checkpointId", "restored", "removed", "changedAnything"}
        assert body["changedAnything"] is True

    def test_preview_changes_nothing_on_disk(self, server, workspace, client):
        """
        The whole point of a separate preview call. If it mutated, the
        browser's confirmation prompt would be asking permission for
        something already done.
        """
        session = server.get_session()
        cp = session.checkpoints.create("Before the edit")
        (workspace / "main.py").write_text("print('changed')\n", encoding="utf-8")
        before = _snapshot(workspace)

        client.get(f"/api/checkpoints/{cp.id}/restore-preview")

        assert _snapshot(workspace) == before

    def test_unknown_checkpoint_is_a_400(self, server, client):
        """
        400 rather than 404: the id is a parameter the caller supplied,
        not a resource path Pearl publishes.
        """
        server.get_session().checkpoints.create("Before the edit")

        r = client.get("/api/checkpoints/deadbeef/restore-preview")

        assert r.status_code == 400
        assert "deadbeef" in r.json()["detail"]

    def test_an_empty_store_also_answers_400(self, client):
        """
        Same status whether the store is empty or the id is simply
        wrong. Both are the caller naming something that does not
        exist, and the message says which case it is.
        """
        r = client.get("/api/checkpoints/deadbeef/restore-preview")

        assert r.status_code == 400
        assert r.json()["detail"]


class TestReadRoutesAreNotGated:
    """
    Section 9 of the spec gates the mutating checkpoint routes on a
    shared instance. These two are read-only and stay open, because
    seeing what exists is how a user understands why the others refuse.
    """

    @pytest.fixture
    def authed(self, server, monkeypatch):
        monkeypatch.setenv("PEARL_AUTH_TOKENS", "tok-alice:alice")
        return server

    def test_list_is_available_under_auth(self, authed):
        with TestClient(authed.app) as client:
            r = client.get(
                "/api/checkpoints",
                headers={"Authorization": "Bearer tok-alice"},
            )

        assert r.status_code == 200

    def test_preview_is_available_under_auth(self, authed):
        with TestClient(authed.app) as client:
            r = client.get(
                "/api/checkpoints/deadbeef/restore-preview",
                headers={"Authorization": "Bearer tok-alice"},
            )

        # 400 for the unknown id, not 403: the route itself is reachable.
        assert r.status_code == 400
