"""
Regression tests for API server startup.

The app must be usable when loaded directly by an ASGI host —
`uvicorn src.api.server:app`, which is how the README says to start it.
Previously only `python -m src.api` called `init_session()`, so running
the documented command left `_session` as None and every endpoint behind
`get_session()` answered 503 while the UI showed "Disconnected".
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def uninitialised_app(monkeypatch, tmp_path):
    """The app as an ASGI host loads it: no init_session() call."""
    from src.api import server as srv

    monkeypatch.setattr(srv, "_session", None)
    # Keep the auto-created session pointed at a scratch directory rather
    # than the real cwd, so startup cannot index this repository.
    monkeypatch.setattr(Path, "cwd", classmethod(lambda cls: tmp_path))
    return srv


class TestStartupInitialisesSession:
    def test_status_is_not_503_without_an_explicit_init(self, uninitialised_app):
        """
        The bug: /api/status answered 503 and the UI read "Disconnected"
        for anyone who started the server the documented way.
        """
        with TestClient(uninitialised_app.app) as client:
            response = client.get("/api/status")

        assert response.status_code == 200, (
            "startup must initialise a session; 503 here means the UI "
            "shows Disconnected for the documented start command"
        )
        assert response.json()["ok"] is True

    def test_session_is_created_on_startup(self, uninitialised_app):
        assert uninitialised_app._session is None
        with TestClient(uninitialised_app.app):
            assert uninitialised_app._session is not None

    def test_workspace_defaults_to_cwd(self, uninitialised_app, tmp_path):
        with TestClient(uninitialised_app.app) as client:
            body = client.get("/api/workspace").json()

        assert Path(body["path"]) == tmp_path.resolve()

    def test_session_creation_returns_a_server_id(self, uninitialised_app):
        """
        POST /api/sessions returning 503 was what left the UI holding its
        own local id, so the follow-up PATCH 404'd against an id the
        server had never created.
        """
        with TestClient(uninitialised_app.app) as client:
            response = client.post("/api/sessions", json={})

        assert response.status_code == 200
        session_id = response.json()["session_id"]
        # A real server id, not the UI's local "c<timestamp>" placeholder.
        assert not session_id.startswith("c")
        assert len(session_id) == 36, "expected a UUID"

    def test_created_session_can_then_be_renamed(self, uninitialised_app):
        """The PATCH that was 404-ing must succeed end to end."""
        with TestClient(uninitialised_app.app) as client:
            session_id = client.post("/api/sessions", json={}).json()["session_id"]
            response = client.patch(
                f"/api/sessions/{session_id}", json={"title": "Renamed"}
            )

        assert response.status_code == 200


class TestExplicitInitIsPreserved:
    def test_startup_does_not_override_an_explicit_workspace(
        self, monkeypatch, tmp_path
    ):
        """
        `python -m src.api --workspace X` calls init_session() before
        handing the app to uvicorn. Startup must not replace that choice
        with the process's cwd.
        """
        from src.api import server as srv

        chosen = tmp_path / "chosen"
        chosen.mkdir()
        other = tmp_path / "other"
        other.mkdir()

        monkeypatch.setattr(srv, "_session", None)
        monkeypatch.setattr(Path, "cwd", classmethod(lambda cls: other))

        srv.init_session(chosen)
        explicit_session = srv._session

        with TestClient(srv.app) as client:
            body = client.get("/api/workspace").json()

        assert srv._session is explicit_session, "startup replaced the session"
        assert Path(body["path"]) == chosen.resolve()
