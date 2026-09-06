"""
Tests for src/agent/session_manager.py
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from src.agent.session_manager import SessionManager, SessionRecord, SessionMessage


@pytest.fixture
def manager(tmp_path: Path) -> SessionManager:
    return SessionManager(sessions_dir=tmp_path)


# ---------------------------------------------------------------------------
# create_session
# ---------------------------------------------------------------------------


class TestCreateSession:
    def test_returns_uuid_string(self, manager: SessionManager) -> None:
        sid = manager.create_session(workspace="/tmp/proj")
        assert isinstance(sid, str)
        assert len(sid) == 36  # UUID format

    def test_default_title_uses_short_id(self, manager: SessionManager) -> None:
        sid = manager.create_session(workspace="/tmp/proj")
        rec = manager.get_session(sid)
        assert sid[:8] in rec.title

    def test_custom_title_stored(self, manager: SessionManager) -> None:
        sid = manager.create_session(workspace="/tmp/proj", title="My Session")
        rec = manager.get_session(sid)
        assert rec.title == "My Session"

    def test_workspace_stored(self, manager: SessionManager) -> None:
        sid = manager.create_session(workspace="/home/user/code")
        rec = manager.get_session(sid)
        assert rec.workspace == "/home/user/code"

    def test_metadata_stored(self, manager: SessionManager) -> None:
        sid = manager.create_session(workspace="/tmp", metadata={"branch": "main"})
        rec = manager.get_session(sid)
        assert rec.metadata["branch"] == "main"

    def test_session_file_created(self, manager: SessionManager, tmp_path: Path) -> None:
        sid = manager.create_session(workspace="/tmp/proj")
        assert (tmp_path / f"{sid}.json").exists()


# ---------------------------------------------------------------------------
# get_session
# ---------------------------------------------------------------------------


class TestGetSession:
    def test_returns_session_record(self, manager: SessionManager) -> None:
        sid = manager.create_session(workspace="/tmp/proj")
        rec = manager.get_session(sid)
        assert isinstance(rec, SessionRecord)

    def test_raises_key_error_on_missing(self, manager: SessionManager) -> None:
        with pytest.raises(KeyError):
            manager.get_session("00000000-0000-0000-0000-000000000000")

    def test_raises_value_error_on_path_traversal(self, manager: SessionManager) -> None:
        with pytest.raises(ValueError):
            manager.get_session("../evil")

    def test_raises_value_error_on_null_bytes(self, manager: SessionManager) -> None:
        with pytest.raises(ValueError):
            manager.get_session("id\x00evil")


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------


class TestListSessions:
    def test_empty_initially(self, manager: SessionManager) -> None:
        assert manager.list_sessions() == []

    def test_returns_all_sessions(self, manager: SessionManager) -> None:
        manager.create_session(workspace="/a")
        manager.create_session(workspace="/b")
        sessions = manager.list_sessions()
        assert len(sessions) == 2

    def test_sorted_newest_first(self, manager: SessionManager) -> None:
        sid1 = manager.create_session(workspace="/a")
        sid2 = manager.create_session(workspace="/b")
        # Touch sid1 so it becomes definitively newer than sid2.
        manager.rename_session(sid1, "Touched")
        sessions = manager.list_sessions()
        ids = [s["id"] for s in sessions]
        assert ids[0] == sid1

    def test_ordering_holds_within_one_clock_tick(self) -> None:
        """
        Windows advances its clock in ~15.6 ms steps, so these three
        operations all read the same wall-clock value. Ordering must come
        from the timestamps being strictly increasing, not from the clock
        happening to tick between calls — otherwise the sidebar order is
        random, and this test only passes by luck.
        """
        import tempfile
        from pathlib import Path

        for _ in range(50):
            mgr = SessionManager(Path(tempfile.mkdtemp(prefix="tick_")))
            first = mgr.create_session(workspace="/a")
            mgr.create_session(workspace="/b")
            mgr.rename_session(first, "Touched")

            assert mgr.list_sessions()[0]["id"] == first

    def test_timestamps_are_strictly_increasing(self) -> None:
        from src.agent.session_manager import _now

        stamps = [_now() for _ in range(1000)]

        assert len(set(stamps)) == len(stamps), "duplicate timestamps"
        assert stamps == sorted(stamps), "timestamps not monotonic"

    def test_summary_dict_keys(self, manager: SessionManager) -> None:
        manager.create_session(workspace="/tmp")
        sessions = manager.list_sessions()
        s = sessions[0]
        for key in ("id", "title", "workspace", "created_at", "updated_at", "message_count"):
            assert key in s

    def test_message_count_correct(self, manager: SessionManager) -> None:
        sid = manager.create_session(workspace="/tmp")
        manager.append_message(sid, role="user", content="hello")
        manager.append_message(sid, role="assistant", content="world")
        sessions = manager.list_sessions()
        assert sessions[0]["message_count"] == 2


# ---------------------------------------------------------------------------
# delete_session
# ---------------------------------------------------------------------------


class TestDeleteSession:
    def test_delete_removes_session(self, manager: SessionManager) -> None:
        sid = manager.create_session(workspace="/tmp")
        manager.delete_session(sid)
        assert manager.list_sessions() == []

    def test_delete_noop_on_missing(self, manager: SessionManager) -> None:
        # Should not raise
        manager.delete_session("00000000-0000-0000-0000-000000000000")

    def test_get_raises_after_delete(self, manager: SessionManager) -> None:
        sid = manager.create_session(workspace="/tmp")
        manager.delete_session(sid)
        with pytest.raises(KeyError):
            manager.get_session(sid)


# ---------------------------------------------------------------------------
# rename_session
# ---------------------------------------------------------------------------


class TestRenameSession:
    def test_title_updated(self, manager: SessionManager) -> None:
        sid = manager.create_session(workspace="/tmp")
        manager.rename_session(sid, "Better Title")
        assert manager.get_session(sid).title == "Better Title"

    def test_updated_at_bumped(self, manager: SessionManager) -> None:
        sid = manager.create_session(workspace="/tmp")
        before = manager.get_session(sid).updated_at
        manager.rename_session(sid, "New Title")
        after = manager.get_session(sid).updated_at
        assert after >= before


# ---------------------------------------------------------------------------
# append_message / load_history
# ---------------------------------------------------------------------------


class TestMessages:
    def test_append_adds_message(self, manager: SessionManager) -> None:
        sid = manager.create_session(workspace="/tmp")
        manager.append_message(sid, role="user", content="hello")
        rec = manager.get_session(sid)
        assert len(rec.messages) == 1
        assert rec.messages[0].role == "user"
        assert rec.messages[0].content == "hello"

    def test_load_history_returns_api_format(self, manager: SessionManager) -> None:
        sid = manager.create_session(workspace="/tmp")
        manager.append_message(sid, role="user", content="hi")
        manager.append_message(sid, role="assistant", content="hello")
        history = manager.load_history(sid)
        assert history[0] == {"role": "user", "content": "hi"}
        assert history[1] == {"role": "assistant", "content": "hello"}

    def test_agent_role_mapped_to_assistant(self, manager: SessionManager) -> None:
        sid = manager.create_session(workspace="/tmp")
        manager.append_message(sid, role="agent", content="I will fix it.")
        history = manager.load_history(sid)
        assert history[0]["role"] == "assistant"

    def test_load_history_limit(self, manager: SessionManager) -> None:
        sid = manager.create_session(workspace="/tmp")
        for i in range(10):
            manager.append_message(sid, role="user", content=f"msg {i}")
        history = manager.load_history(sid, limit=3)
        assert len(history) == 3
        assert history[-1]["content"] == "msg 9"

    def test_append_raises_on_missing_session(self, manager: SessionManager) -> None:
        with pytest.raises(KeyError):
            manager.append_message(
                "00000000-0000-0000-0000-000000000000", role="user", content="x"
            )

    def test_concurrent_appends_are_safe(self, manager: SessionManager) -> None:
        sid = manager.create_session(workspace="/tmp")
        errors: list[Exception] = []

        def _append(n: int) -> None:
            try:
                for i in range(10):
                    manager.append_message(sid, role="user", content=f"{n}-{i}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_append, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        rec = manager.get_session(sid)
        assert len(rec.messages) == 50


# ---------------------------------------------------------------------------
# update_metadata
# ---------------------------------------------------------------------------


class TestUpdateMetadata:
    def test_metadata_merged(self, manager: SessionManager) -> None:
        sid = manager.create_session(workspace="/tmp", metadata={"a": 1})
        manager.update_metadata(sid, {"b": 2})
        rec = manager.get_session(sid)
        assert rec.metadata["a"] == 1
        assert rec.metadata["b"] == 2

    def test_metadata_key_overwritten(self, manager: SessionManager) -> None:
        sid = manager.create_session(workspace="/tmp", metadata={"key": "old"})
        manager.update_metadata(sid, {"key": "new"})
        assert manager.get_session(sid).metadata["key"] == "new"
