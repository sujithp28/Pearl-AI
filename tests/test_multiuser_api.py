"""
End-to-end multi-user behaviour of the HTTP API.

Asserts the two things that decide whether Pearl can be served to more
than one person:

* Two authenticated users get separate sessions, so neither can see or
  act on the other's conversation and pending approvals.
* Endpoints that are safe on a laptop but dangerous when shared refuse
  once the instance is shared.

And, just as importantly, that a local run with no configuration is
completely unchanged.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def server(monkeypatch, tmp_path):
    """A fresh app with the workspace pointed at a scratch directory."""
    from src.api import server as srv

    monkeypatch.setattr(srv, "_session", None)
    monkeypatch.setattr(srv, "_workspace", None)
    srv._registry.clear()
    srv.init_session(tmp_path)
    return srv


class TestLocalModeUnchanged:
    def test_no_auth_means_no_token_needed(self, server):
        with TestClient(server.app) as client:
            assert client.get("/api/status").status_code == 200

    def test_local_requests_share_one_session(self, server):
        """
        Without auth there is one user, so Pearl behaves exactly as it
        did before per-user sessions existed.
        """
        with TestClient(server.app) as client:
            client.get("/api/status")
            client.get("/api/status")

        # Nothing was added to the registry: the startup session serves.
        assert len(server._registry) == 0


class TestAuthenticatedIsolation:
    @pytest.fixture
    def authed(self, server, monkeypatch):
        monkeypatch.setenv("PEARL_AUTH_TOKENS", "tok-alice:alice,tok-bob:bob")
        return server

    def test_request_without_a_token_is_rejected(self, authed):
        with TestClient(authed.app) as client:
            assert client.get("/api/status").status_code == 401

    def test_invalid_token_is_rejected(self, authed):
        with TestClient(authed.app) as client:
            response = client.get(
                "/api/status", headers={"Authorization": "Bearer wrong"}
            )
            assert response.status_code == 401

    def test_valid_token_is_accepted(self, authed):
        with TestClient(authed.app) as client:
            response = client.get(
                "/api/status", headers={"Authorization": "Bearer tok-alice"}
            )
            assert response.status_code == 200

    def test_two_users_get_two_sessions(self, authed):
        """
        The core isolation guarantee. Sharing one session would let
        either user approve the other's file writes.
        """
        with TestClient(authed.app) as client:
            client.get("/api/status", headers={"Authorization": "Bearer tok-alice"})
            client.get("/api/status", headers={"Authorization": "Bearer tok-bob"})

        assert authed._registry.peek("alice") is not None
        assert authed._registry.peek("bob") is not None
        assert authed._registry.peek("alice") is not authed._registry.peek("bob")

    def test_repeat_requests_reuse_the_same_session(self, authed):
        with TestClient(authed.app) as client:
            for _ in range(3):
                client.get(
                    "/api/status", headers={"Authorization": "Bearer tok-alice"}
                )

        assert len(authed._registry) == 1


class TestWorkspaceEndpointIsGated:
    def test_allowed_locally(self, server, tmp_path):
        """On a laptop the caller already has filesystem access."""
        target = tmp_path / "other"
        target.mkdir()

        with TestClient(server.app) as client:
            response = client.post("/api/workspace", json={"path": str(target)})

        assert response.status_code == 200

    def test_refused_when_auth_is_configured(self, server, monkeypatch, tmp_path):
        """
        POST {"path": "/"} would hand any caller the entire host
        filesystem, since every file tool resolves against it.
        """
        monkeypatch.setenv("PEARL_AUTH_TOKENS", "tok-alice:alice")
        target = tmp_path / "other"
        target.mkdir()

        with TestClient(server.app) as client:
            response = client.post(
                "/api/workspace",
                json={"path": str(target)},
                headers={"Authorization": "Bearer tok-alice"},
            )

        assert response.status_code == 403
        assert "shared instance" in response.json()["detail"]

    def test_refused_in_public_mode(self, server, monkeypatch, tmp_path):
        monkeypatch.setenv("PEARL_PUBLIC_MODE", "true")
        target = tmp_path / "other"
        target.mkdir()

        with TestClient(server.app) as client:
            response = client.post("/api/workspace", json={"path": str(target)})

        assert response.status_code == 403

    def test_root_cannot_be_selected_on_a_shared_instance(
        self, server, monkeypatch
    ):
        """The specific attack, stated plainly."""
        monkeypatch.setenv("PEARL_PUBLIC_MODE", "true")

        with TestClient(server.app) as client:
            response = client.post("/api/workspace", json={"path": "/"})

        assert response.status_code == 403
