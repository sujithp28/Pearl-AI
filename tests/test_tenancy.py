"""
Tests for per-user sessions (src/api/tenancy.py).

Pearl held one module-level session shared by every request, which is
right for a laptop and unusable when shared: two users would share one
conversation, one workspace, and one approval queue — so either could
approve the other's file writes.

The properties worth asserting are therefore about isolation and about
not regressing the local single-user case, which must keep working with
no configuration at all.
"""

from __future__ import annotations

import pytest

from src.api.tenancy import (
    LOCAL_USER,
    AuthenticationError,
    SessionRegistry,
    auth_required,
    public_mode,
    resolve_user,
)


class _FakeSession:
    """Stands in for PearlSession; only the busy-check is consulted."""

    def __init__(self, name: str = "s", busy: bool = False) -> None:
        self.name = name
        self._busy = busy

    def is_running(self) -> bool:
        return self._busy

    def is_awaiting_approval(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


class TestResolveUser:
    def test_no_auth_configured_means_local_user(self, monkeypatch):
        """
        The default must remain single-user, or this change locks people
        out of their own laptop.
        """
        monkeypatch.delenv("PEARL_AUTH_TOKENS", raising=False)

        assert auth_required() is False
        assert resolve_user(None) == LOCAL_USER
        assert resolve_user("Bearer anything") == LOCAL_USER

    def test_configured_tokens_map_to_users(self, monkeypatch):
        monkeypatch.setenv("PEARL_AUTH_TOKENS", "tok-a:alice,tok-b:bob")

        assert resolve_user("Bearer tok-a") == "alice"
        assert resolve_user("Bearer tok-b") == "bob"

    def test_missing_header_is_rejected_when_auth_is_on(self, monkeypatch):
        monkeypatch.setenv("PEARL_AUTH_TOKENS", "tok-a:alice")

        with pytest.raises(AuthenticationError):
            resolve_user(None)

    def test_unknown_token_is_rejected(self, monkeypatch):
        monkeypatch.setenv("PEARL_AUTH_TOKENS", "tok-a:alice")

        with pytest.raises(AuthenticationError):
            resolve_user("Bearer not-a-real-token")

    @pytest.mark.parametrize("header", ["tok-a", "Basic tok-a", "Bearer", "Bearer "])
    def test_malformed_header_is_rejected(self, monkeypatch, header):
        monkeypatch.setenv("PEARL_AUTH_TOKENS", "tok-a:alice")

        with pytest.raises(AuthenticationError):
            resolve_user(header)

    def test_malformed_token_config_is_ignored_not_crashed(self, monkeypatch):
        """A typo in configuration must not take the server down."""
        monkeypatch.setenv("PEARL_AUTH_TOKENS", "garbage,,:,tok-a:alice,x:")

        assert resolve_user("Bearer tok-a") == "alice"


# ---------------------------------------------------------------------------
# Session isolation — the point of the whole module
# ---------------------------------------------------------------------------


class TestSessionRegistry:
    def test_same_user_reuses_one_session(self):
        registry = SessionRegistry()
        calls = {"n": 0}

        def factory():
            calls["n"] += 1
            return _FakeSession()

        first = registry.get_or_create("alice", factory)
        second = registry.get_or_create("alice", factory)

        assert first is second
        assert calls["n"] == 1, "built a second session for the same user"

    def test_different_users_get_different_sessions(self):
        """
        The isolation guarantee: alice must never see bob's conversation,
        workspace, or pending approvals.
        """
        registry = SessionRegistry()

        alice = registry.get_or_create("alice", lambda: _FakeSession("alice"))
        bob = registry.get_or_create("bob", lambda: _FakeSession("bob"))

        assert alice is not bob
        assert len(registry) == 2

    def test_peek_does_not_create(self):
        registry = SessionRegistry()

        assert registry.peek("nobody") is None
        assert len(registry) == 0

    def test_drop_removes_a_session(self):
        registry = SessionRegistry()
        registry.get_or_create("alice", lambda: _FakeSession())

        assert registry.drop("alice") is True
        assert registry.peek("alice") is None
        assert registry.drop("alice") is False


class TestEviction:
    def test_capacity_is_bounded(self):
        """Each session costs real memory; visitors must not exhaust the host."""
        registry = SessionRegistry(max_sessions=3)

        for i in range(10):
            registry.get_or_create(f"user{i}", lambda: _FakeSession())

        assert len(registry) <= 3

    def test_least_recently_used_is_evicted_first(self):
        registry = SessionRegistry(max_sessions=2)

        registry.get_or_create("old", lambda: _FakeSession("old"))
        registry.get_or_create("mid", lambda: _FakeSession("mid"))
        # Touch "old" so "mid" becomes least-recently-used.
        registry.get_or_create("old", lambda: _FakeSession("other"))
        registry.get_or_create("new", lambda: _FakeSession("new"))

        assert registry.peek("old") is not None
        assert registry.peek("new") is not None
        assert registry.peek("mid") is None

    def test_a_busy_session_is_never_evicted(self):
        """
        Dropping a session mid-run, or while its user is looking at a
        staged diff, would silently discard changes they are about to
        approve.
        """
        registry = SessionRegistry(max_sessions=1)
        busy = _FakeSession("busy", busy=True)
        registry.get_or_create("busy-user", lambda: busy)

        registry.get_or_create("newcomer", lambda: _FakeSession("newcomer"))

        assert registry.peek("busy-user") is busy

    def test_idle_sessions_are_reclaimed(self):
        registry = SessionRegistry(idle_timeout=0.01)
        registry.get_or_create("alice", lambda: _FakeSession())

        import time

        time.sleep(0.05)
        registry.get_or_create("bob", lambda: _FakeSession())

        assert registry.peek("alice") is None

    def test_a_broken_session_does_not_block_eviction(self):
        """A session whose busy-check raises must not become immortal."""

        class _Broken:
            def is_running(self):
                raise RuntimeError("boom")

            def is_awaiting_approval(self):
                raise RuntimeError("boom")

        registry = SessionRegistry(max_sessions=1)
        registry.get_or_create("broken", lambda: _Broken())
        registry.get_or_create("newcomer", lambda: _FakeSession())

        assert len(registry) <= 1


class TestConcurrency:
    def test_concurrent_requests_share_one_session(self):
        """
        Two requests from one user arriving together must not each build
        a session — building one loads a repository index.
        """
        import threading

        registry = SessionRegistry()
        built = []
        results = []

        def factory():
            built.append(1)
            return _FakeSession()

        def worker():
            results.append(registry.get_or_create("alice", factory))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(built) == 1, f"built {len(built)} sessions for one user"
        assert all(r is results[0] for r in results)


# ---------------------------------------------------------------------------
# Public mode
# ---------------------------------------------------------------------------


class TestPublicMode:
    def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv("PEARL_PUBLIC_MODE", raising=False)
        assert public_mode() is False

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes"])
    def test_recognised_values(self, monkeypatch, value):
        monkeypatch.setenv("PEARL_PUBLIC_MODE", value)
        assert public_mode() is True
