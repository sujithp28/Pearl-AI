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

from src.api.session import PendingPlanError
from src.llm.parser import ToolCall
from src.personality.timeline import TIMELINE_EVENT_KINDS


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


class TestMutatingRoutesAreGated:
    """
    Section 9 of the spec. Checkpoints cannot have an ownership model
    while every user shares one workspace: the store is keyed by
    workspace path, and changing the workspace is already refused once
    auth is configured, so one user restoring rolls back the files
    everyone else is working in.

    The mutating routes therefore refuse under exactly the condition
    that already guards `POST /api/workspace`.
    """

    ROUTES = [
        ("post", "/api/checkpoints", {"label": "x"}),
        ("patch", "/api/checkpoints/deadbeef", {"label": "x"}),
        ("delete", "/api/checkpoints/deadbeef", None),
        ("post", "/api/checkpoints/deadbeef/restore", None),
    ]

    @pytest.mark.parametrize("method,path,body", ROUTES)
    def test_refused_when_auth_is_configured(
        self, server, monkeypatch, method, path, body
    ):
        monkeypatch.setenv("PEARL_AUTH_TOKENS", "tok-alice:alice")

        with TestClient(server.app) as client:
            kwargs = {"headers": {"Authorization": "Bearer tok-alice"}}
            if body is not None:
                kwargs["json"] = body
            r = getattr(client, method)(path, **kwargs)

        assert r.status_code == 403
        assert "shared instance" in r.json()["detail"]

    @pytest.mark.parametrize("method,path,body", ROUTES)
    def test_refused_in_public_mode(self, server, monkeypatch, method, path, body):
        monkeypatch.setenv("PEARL_PUBLIC_MODE", "true")

        with TestClient(server.app) as client:
            kwargs = {"json": body} if body is not None else {}
            r = getattr(client, method)(path, **kwargs)

        assert r.status_code == 403

    def test_allowed_on_a_local_single_user_run(self, client):
        """
        The gate must cost the ordinary case nothing. Almost every Pearl
        user runs it on their own machine with no auth at all.
        """
        r = client.post("/api/checkpoints", json={"label": "Manual"})

        assert r.status_code == 200


class TestSharedStoreIsNotIsolated:
    """
    Documents the constraint rather than pretending it away.

    If per-user workspaces ever arrive this test fails, which is exactly
    when the gate above should be reconsidered.
    """

    def test_one_users_checkpoint_is_visible_to_another(self, server, monkeypatch):
        server.get_session().checkpoints.create("Alice was here")
        monkeypatch.setenv("PEARL_AUTH_TOKENS", "tok-alice:alice,tok-bob:bob")

        with TestClient(server.app) as client:
            bob = client.get(
                "/api/checkpoints",
                headers={"Authorization": "Bearer tok-bob"},
            ).json()

        assert [c["label"] for c in bob["checkpoints"]] == ["Alice was here"]


class TestCheckpointMutation:
    def test_create_returns_the_new_checkpoint(self, client):
        body = client.post("/api/checkpoints", json={"label": "Manual"}).json()

        assert body["checkpoint"]["label"] == "Manual"
        assert set(body["checkpoint"]) == {"id", "shortId", "label", "createdAt"}

    def test_create_without_a_label_uses_a_default(self, client):
        body = client.post("/api/checkpoints", json={}).json()

        assert body["checkpoint"]["label"] == "Manual checkpoint"

    def test_create_with_nothing_to_capture_returns_null(self, server, client):
        """
        `CheckpointManager.create` returns None when the workspace has
        not changed since the last snapshot. The route reports that
        honestly instead of inventing an empty checkpoint.
        """
        server.get_session().checkpoints.create("First")

        body = client.post("/api/checkpoints", json={"label": "Second"}).json()

        assert body["checkpoint"] is None

    def test_rename_changes_the_label(self, server, client):
        cp = server.get_session().checkpoints.create("Old name")

        body = client.patch(
            f"/api/checkpoints/{cp.id}", json={"label": "New name"}
        ).json()

        assert body["checkpoint"]["label"] == "New name"

    def test_rename_requires_a_label(self, server, client):
        cp = server.get_session().checkpoints.create("Old name")

        r = client.patch(f"/api/checkpoints/{cp.id}", json={"label": ""})

        assert r.status_code == 422

    def test_delete_hides_it_from_the_listing(self, server, client):
        cp = server.get_session().checkpoints.create("Doomed")

        assert client.delete(f"/api/checkpoints/{cp.id}").json() == {"deleted": True}
        assert client.get("/api/checkpoints").json()["checkpoints"] == []

    def test_unknown_id_is_a_400_on_every_mutating_route(self, client):
        assert (
            client.patch("/api/checkpoints/deadbeef", json={"label": "x"}).status_code
            == 400
        )
        assert client.delete("/api/checkpoints/deadbeef").status_code == 400
        assert client.post("/api/checkpoints/deadbeef/restore").status_code == 400


class TestRestore:
    def test_restore_puts_the_file_back(self, server, workspace, client):
        cp = server.get_session().checkpoints.create("Before the edit")
        (workspace / "main.py").write_text("print('changed')\n", encoding="utf-8")

        body = client.post(f"/api/checkpoints/{cp.id}/restore").json()

        assert body["changedAnything"] is True
        assert (workspace / "main.py").read_text(encoding="utf-8") == "print('hello')\n"

    def test_restore_removes_files_created_since(self, server, workspace, client):
        """
        The reason restore is preview-then-confirm rather than one
        click. A file created after the snapshot is deleted by the
        restore, and the user has to be shown that before it happens.
        """
        cp = server.get_session().checkpoints.create("Before the edit")
        (workspace / "scratch.py").write_text("# new\n", encoding="utf-8")

        body = client.post(f"/api/checkpoints/{cp.id}/restore").json()

        assert "scratch.py" in body["removed"]
        assert not (workspace / "scratch.py").exists()


class TestMemory:
    """
    Read-only view of what Pearl remembers: the conversation, the tasks
    it has been given, facts about the project, and what it has actually
    executed.

    It reuses `Memory.to_dict()` exactly as the protocol adapter does,
    so the two surfaces cannot describe the same session differently.
    """

    def test_returns_the_four_memory_sections(self, client):
        body = client.get("/api/memory").json()

        assert set(body) == {"conversation", "tasks", "project", "execution_history"}

    def test_an_empty_session_returns_empty_sections(self, client):
        body = client.get("/api/memory").json()

        assert body["conversation"] == []
        assert body["tasks"] == []
        assert body["execution_history"] == []

    def test_recorded_turns_are_visible(self, server, client):
        server.get_session().memory.record_turn("user", "rename the registry")

        body = client.get("/api/memory").json()

        assert len(body["conversation"]) == 1
        assert body["conversation"][0]["content"] == "rename the registry"

    def test_reading_memory_does_not_change_it(self, server, client):
        session = server.get_session()
        session.memory.record_turn("user", "rename the registry")
        before = session.memory.to_dict()

        client.get("/api/memory")

        assert session.memory.to_dict() == before

    def test_is_available_on_a_shared_instance(self, server, monkeypatch):
        """
        Read-only, and scoped to the caller's own session rather than
        the shared workspace, so it needs no section 9 gate.
        """
        monkeypatch.setenv("PEARL_AUTH_TOKENS", "tok-alice:alice")

        with TestClient(server.app) as client:
            r = client.get("/api/memory", headers={"Authorization": "Bearer tok-alice"})

        assert r.status_code == 200


class TestPlanPreview:
    """
    The preview must run the plan it showed, and the browser must never
    be able to hand the executor a step list of its own.
    """

    @pytest.fixture
    def planned(self, server, monkeypatch):
        """Stub the planner so no model is needed."""
        steps = [ToolCall(tool_name="read_file", args=(), kwargs={"path": "main.py"})]
        monkeypatch.setattr(
            server.get_session().planner, "plan", lambda prompt, **kw: steps
        )
        return server

    def test_returns_an_id_and_the_steps(self, planned):
        with TestClient(planned.app) as client:
            body = client.post("/api/plan", json={"prompt": "read main"}).json()

        assert body["planId"]
        assert body["steps"] == [
            {"tool": "read_file", "arguments": {"path": "main.py"}}
        ]

    def test_planning_alone_executes_nothing(self, planned, workspace):
        before = _snapshot(workspace)

        with TestClient(planned.app) as client:
            client.post("/api/plan", json={"prompt": "read main"})

        assert _snapshot(workspace) == before

    def test_a_planning_failure_is_a_400(self, server, monkeypatch):
        def _boom(prompt, **kw):
            raise RuntimeError("model unreachable")

        monkeypatch.setattr(server.get_session().planner, "plan", _boom)

        with TestClient(server.app) as client:
            r = client.post("/api/plan", json={"prompt": "read main"})

        assert r.status_code == 400
        assert "model unreachable" in r.json()["detail"]


class TestPlanIdLifecycle:
    """
    Section 5 of the spec. Every one of these returns 400 rather than
    falling back to planning: a silent fallback would run something the
    user never saw.
    """

    def test_an_unknown_id_is_refused(self, client):
        r = client.post("/api/run", json={"prompt": "go", "planId": "nope"})

        assert r.status_code == 400
        assert "plan" in r.json()["detail"].lower()

    def test_a_plan_is_consumed_on_first_use(self, server):
        session = server.get_session()
        plan_id = session.store_plan(
            [ToolCall(tool_name="read_file", args=(), kwargs={"path": "main.py"})]
        )

        session.take_plan(plan_id)

        with pytest.raises(PendingPlanError):
            session.take_plan(plan_id)

    def test_a_new_plan_replaces_the_previous_one(self, server):
        session = server.get_session()
        step = ToolCall(tool_name="read_file", args=(), kwargs={"path": "main.py"})
        first = session.store_plan([step])
        session.store_plan([step])

        with pytest.raises(PendingPlanError):
            session.take_plan(first)

    def test_an_expired_plan_is_refused(self, server, monkeypatch):
        session = server.get_session()
        plan_id = session.store_plan(
            [ToolCall(tool_name="read_file", args=(), kwargs={"path": "main.py"})]
        )

        # Jump past the TTL rather than sleeping through it.
        import src.api.session as session_module

        real = session_module.time.monotonic
        monkeypatch.setattr(
            session_module.time,
            "monotonic",
            lambda: real() + session.PLAN_TTL_SECONDS + 1,
        )

        with pytest.raises(PendingPlanError):
            session.take_plan(plan_id)

    def test_one_users_plan_id_is_useless_to_another(self, server, monkeypatch):
        """
        Not enforced by a check that could be forgotten: the plan lives
        on the session object, and sessions are already per user.
        """
        alice = server.get_session()
        plan_id = alice.store_plan(
            [ToolCall(tool_name="read_file", args=(), kwargs={"path": "main.py"})]
        )
        monkeypatch.setenv("PEARL_AUTH_TOKENS", "tok-alice:alice,tok-bob:bob")

        with TestClient(server.app) as client:
            r = client.post(
                "/api/run",
                json={"prompt": "go", "planId": plan_id},
                headers={"Authorization": "Bearer tok-bob"},
            )

        assert r.status_code == 400


class TestRunWithoutAPlanIsUnchanged:
    def test_run_still_accepts_a_bare_prompt(self, server, monkeypatch):
        """
        The default path. `planId` omitted means the executor plans for
        itself, exactly as before this endpoint existed.
        """
        captured = {}

        def _fake_stream(prompt, queue, initial_plan=None):
            captured["prompt"] = prompt
            captured["initial_plan"] = initial_plan
            queue.put_nowait(None)

        monkeypatch.setattr(server.get_session(), "run_autonomous_stream", _fake_stream)

        with TestClient(server.app) as client:
            r = client.post("/api/run", json={"prompt": "go"})
            r.read()

        assert r.status_code == 200
        assert captured["initial_plan"] is None


class TestPersonalityLabels:
    """
    Stage wording for the plan preview, in the configured voice.

    The stage names are a client-facing contract shared with the VS Code
    extension, so they come from one map rather than being spelled out
    again here.
    """

    def test_labels_cover_exactly_the_timeline_stages(self, client):
        body = client.get("/api/personality").json()

        assert set(body["labels"]) == set(TIMELINE_EVENT_KINDS)

    def test_every_stage_has_wording(self, client):
        labels = client.get("/api/personality").json()["labels"]

        assert all(isinstance(v, str) and v for v in labels.values())

    def test_wording_follows_the_configured_personality(self, client, monkeypatch):
        """
        Read through the personality system rather than hardcoded, so
        changing the configured personality changes the preview.
        """
        from src.personality import EventKind, PersonalityManager

        expected = PersonalityManager().format(EventKind.PLAN_READY)

        assert client.get("/api/personality").json()["labels"]["plan_ready"] == expected

    def test_is_available_on_a_shared_instance(self, server, monkeypatch):
        monkeypatch.setenv("PEARL_AUTH_TOKENS", "tok-alice:alice")

        with TestClient(server.app) as client:
            r = client.get(
                "/api/personality", headers={"Authorization": "Bearer tok-alice"}
            )

        assert r.status_code == 200
