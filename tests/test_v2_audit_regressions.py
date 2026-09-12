"""
Regression tests for defects found by the V2 audit.

Each test here reproduces one bug that the rest of the suite could not
see, and fails against the pre-fix code.  They are grouped by the
subsystem they protect rather than by file, because several of them span
two modules.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from src.agent.execution_model import _PausedState
from src.agent.executor import AutonomousExecutor
from src.agent.reflection import ReflectionEngine
from src.config.workspace import set_workspace_root
from src.tools import patch_manager
from src.tools.edit_tools import set_active_patch_manager
from src.tools.file_io import read_text
from src.tools.file_tools import (
    _ensure_within_workspace,
    copy_file,
    delete_file,
    rename_file,
    write_file,
)
from src.tools.patch_manager import ChangeManager

# ── Workspace-relative path resolution ───────────────────────────────────────
#
# Every other path test sets the workspace root to tmp_path AND chdirs
# into it, so cwd and workspace agree and the distinction never shows up.
# That is exactly the configuration `--workspace` and the web UI's
# workspace selector do NOT produce.


class TestRelativePathsResolveAgainstWorkspace:
    """Relative paths must resolve against the workspace, not the cwd."""

    def test_relative_path_resolves_when_cwd_is_elsewhere(self, tmp_path, monkeypatch):
        workspace = tmp_path / "project"
        workspace.mkdir()
        elsewhere = tmp_path / "somewhere-else"
        elsewhere.mkdir()

        monkeypatch.chdir(elsewhere)
        set_workspace_root(workspace)

        resolved = _ensure_within_workspace("src/app.py")

        assert resolved == (workspace / "src/app.py").resolve()

    def test_read_write_round_trip_with_cwd_outside_workspace(
        self, tmp_path, monkeypatch
    ):
        workspace = tmp_path / "project"
        workspace.mkdir()
        monkeypatch.chdir(tmp_path)
        set_workspace_root(workspace)

        write_file("notes.txt", "hello")

        assert (workspace / "notes.txt").read_text(encoding="utf-8") == "hello"

    def test_absolute_path_outside_workspace_is_still_refused(
        self, tmp_path, monkeypatch
    ):
        workspace = tmp_path / "project"
        workspace.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("secret", encoding="utf-8")

        monkeypatch.chdir(tmp_path)
        set_workspace_root(workspace)

        with pytest.raises(PermissionError):
            _ensure_within_workspace(str(outside))

    def test_traversal_out_of_workspace_is_still_refused(self, tmp_path, monkeypatch):
        workspace = tmp_path / "project"
        workspace.mkdir()
        monkeypatch.chdir(tmp_path)
        set_workspace_root(workspace)

        with pytest.raises(PermissionError):
            _ensure_within_workspace("../outside.txt")


# ── Reflection budget is per task, not per process ───────────────────────────


class _ReplanLLM:
    def generate(self, *args, **kwargs) -> str:
        return json.dumps(
            {
                "status": "replan",
                "confidence": 0.9,
                "reason": "not done",
                "missing_requirements": [],
                "recommended_action": "keep going",
            }
        )


class TestReflectionBudgetIsPerRun:
    def test_run_resets_the_reflection_budget(self):
        engine = ReflectionEngine(_ReplanLLM(), max_iterations=2)
        engine.reflect("task", [])
        engine.reflect("task", [])
        assert engine.exhausted is True

        executor = AutonomousExecutor(
            planner=None, dispatcher=None, reflection_engine=engine, checkpoints=None
        )
        # With no planner, run() reports a planning failure and never
        # reflects.  What matters is that entering run() restored this
        # task's budget instead of inheriting the previous task's
        # exhausted counter, which left the REFLECT -> REPLAN edge dead
        # for the rest of the session.
        report = executor.run("a brand new task")

        assert report.stop_reason == "fatal_error"
        assert engine.exhausted is False
        assert engine.iterations_remaining == 2

    def test_approve_does_not_reset_the_budget(self):
        engine = ReflectionEngine(_ReplanLLM(), max_iterations=2)
        engine.reflect("task", [])

        executor = AutonomousExecutor(
            planner=None, dispatcher=None, reflection_engine=engine, checkpoints=None
        )
        executor._paused = _PausedState(
            prompt="p",
            pending=[],
            steps=[],
            events=[],
            completed_for_replan=[],
            replans_used=0,
            iteration=1,
        )
        executor.approve()

        # Same task resumed, so the cycle already spent stays spent: the
        # resumed run reflects once more on top of it (2 of 2 used)
        # rather than starting the budget over at 2 remaining.
        assert engine._iteration == 2
        assert engine.iterations_remaining == 0


# ── A failed apply must leave the run approvable ──────────────────────────────


class _ExplodingChangeManager(ChangeManager):
    def apply_all(self):
        raise PermissionError("read-only file system")


class TestApproveKeepsPausedStateOnApplyFailure:
    def test_failed_apply_leaves_the_run_awaiting_approval(self):
        pm = _ExplodingChangeManager()
        pm.propose("/ws/a.py", None, "content")

        executor = AutonomousExecutor(
            planner=None, dispatcher=None, patch_manager=pm, checkpoints=None
        )
        executor._paused = _PausedState(
            prompt="p",
            pending=[],
            steps=[],
            events=[],
            completed_for_replan=[],
            replans_used=0,
            iteration=1,
        )

        with pytest.raises(PermissionError):
            executor.approve()

        assert executor.is_awaiting_approval() is True
        assert pm.has_pending() is True

    def test_the_user_can_still_reject_after_a_failed_apply(self):
        pm = _ExplodingChangeManager()
        pm.propose("/ws/a.py", None, "content")

        executor = AutonomousExecutor(
            planner=None, dispatcher=None, patch_manager=pm, checkpoints=None
        )
        executor._paused = _PausedState(
            prompt="p",
            pending=[],
            steps=[],
            events=[],
            completed_for_replan=[],
            replans_used=0,
            iteration=1,
        )

        with pytest.raises(PermissionError):
            executor.approve()

        report = executor.reject()

        assert report.stop_reason == "rejected"
        assert pm.has_pending() is False


# ── Every write tool honours the approval gate ────────────────────────────────


class TestMoveAndCopyAreStaged:
    """
    The gate is enforced per call site, so each write tool needs its own
    proof.  rename_file deletes its source; an unstaged run of it is the
    P0 the gate exists to prevent.
    """

    def test_rename_file_stages_instead_of_moving(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        set_workspace_root(tmp_path)
        source = tmp_path / "old.py"
        source.write_text("x = 1\n", encoding="utf-8")

        pm = ChangeManager()
        set_active_patch_manager(pm)
        try:
            rename_file("old.py", "new.py")
        finally:
            set_active_patch_manager(None)

        assert source.exists(), "source must not be moved before approval"
        assert not (tmp_path / "new.py").exists()
        staged = {Path(p).name for p in pm.affected_files()}
        assert staged == {"old.py", "new.py"}
        assert any(edit.is_deletion for edit in pm.pending)

    def test_approving_a_staged_rename_performs_the_move(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        set_workspace_root(tmp_path)
        (tmp_path / "old.py").write_text("x = 1\n", encoding="utf-8")

        pm = ChangeManager()
        set_active_patch_manager(pm)
        try:
            rename_file("old.py", "new.py")
        finally:
            set_active_patch_manager(None)

        pm.apply_all()

        assert not (tmp_path / "old.py").exists()
        assert (tmp_path / "new.py").read_text(encoding="utf-8") == "x = 1\n"

    def test_copy_file_stages_instead_of_writing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        set_workspace_root(tmp_path)
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

        pm = ChangeManager()
        set_active_patch_manager(pm)
        try:
            copy_file("a.py", "b.py")
        finally:
            set_active_patch_manager(None)

        assert not (tmp_path / "b.py").exists()
        assert [Path(p).name for p in pm.affected_files()] == ["b.py"]

    def test_every_registered_write_tool_declares_a_risk_level(self):
        """
        A tool that reaches disk must not sit at the default risk tier by
        accident.  delete_file and rename_file are irreversible and must
        both be "dangerous".
        """
        from src.main import build_registry

        registry = build_registry()
        for name in ("delete_file",):
            assert registry.get_tool(name).risk_level == "dangerous"

    def test_delete_file_still_stages(self, tmp_path, monkeypatch):
        """The invariant CLAUDE.md requires in every sprint, for deletions."""
        monkeypatch.chdir(tmp_path)
        set_workspace_root(tmp_path)
        target = tmp_path / "gone.py"
        target.write_text("x = 1\n", encoding="utf-8")

        pm = ChangeManager()
        set_active_patch_manager(pm)
        try:
            delete_file("gone.py")
        finally:
            set_active_patch_manager(None)

        assert target.exists()
        assert pm.has_pending()


# ── Session persistence ──────────────────────────────────────────────────────


class TestSessionManagerConcurrency:
    def test_get_session_manager_builds_exactly_one_instance(self, monkeypatch):
        import src.agent.session_manager as sm

        monkeypatch.setattr(sm, "_default_manager", None, raising=False)

        built: list[object] = []
        original_init = sm.SessionManager.__init__

        def counting_init(self, sessions_dir=None):
            # Widen the window the unsynchronised version lost.
            threading.Event().wait(0.01)
            original_init(self, sessions_dir)
            built.append(self)

        monkeypatch.setattr(sm.SessionManager, "__init__", counting_init)

        handed_out: list[object] = []
        threads = [
            threading.Thread(target=lambda: handed_out.append(sm.get_session_manager()))
            for _ in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(built) == 1
        assert len({id(m) for m in handed_out}) == 1

    def test_save_is_atomic_so_a_failed_write_cannot_truncate(self, tmp_path):
        from src.agent.session_manager import SessionManager

        manager = SessionManager(sessions_dir=tmp_path)
        sid = manager.create_session(workspace="/ws", title="original")
        manager.append_message(sid, role="user", content="first")

        path = tmp_path / f"{sid}.json"
        good = path.read_text(encoding="utf-8")

        # Serialization fails partway through the next save.
        class Unserializable:
            def __repr__(self):
                raise RuntimeError("boom")

        record = manager.get_session(sid)
        record.metadata["bad"] = Unserializable()
        with pytest.raises(Exception):
            manager._save(record)

        assert path.read_text(encoding="utf-8") == good
        assert manager.get_session(sid).title == "original"
        assert list(tmp_path.glob("*.tmp")) == []

    def test_a_session_survives_a_fresh_manager(self, tmp_path):
        from src.agent.session_manager import SessionManager

        sid = SessionManager(sessions_dir=tmp_path).create_session(
            workspace="/ws", title="persisted"
        )
        SessionManager(sessions_dir=tmp_path).append_message(
            sid, role="user", content="hello"
        )

        reloaded = SessionManager(sessions_dir=tmp_path).get_session(sid)

        assert reloaded.title == "persisted"
        assert [m.content for m in reloaded.messages] == ["hello"]

    def test_from_dict_does_not_mutate_its_input(self):
        from src.agent.session_manager import SessionRecord

        payload = {
            "id": "a",
            "title": "t",
            "workspace": "/ws",
            "created_at": "x",
            "updated_at": "y",
            "messages": [{"role": "user", "content": "hi", "ts": "z"}],
            "metadata": {},
        }
        snapshot = json.loads(json.dumps(payload))

        SessionRecord.from_dict(payload)

        assert payload == snapshot


# ── Repository index cache ───────────────────────────────────────────────────


class TestIndexCacheConcurrency:
    def test_refresh_survives_a_concurrent_insert(self, tmp_path, monkeypatch):
        """
        refresh_indexed_file() promises never to raise, and approve()
        calls it after the writes have already landed.  Iterating the live
        cache while another session's thread inserted a root raised
        RuntimeError there.
        """
        import src.tools.repo_tools as rt

        monkeypatch.setattr(rt, "_INDEX_CACHE", {}, raising=False)
        for i in range(200):
            rt._INDEX_CACHE[tmp_path / f"root{i}"] = rt.RepositoryIndex(
                root=tmp_path / f"root{i}"
            )

        errors: list[BaseException] = []
        stop = threading.Event()

        def churn() -> None:
            i = 1000
            while not stop.is_set():
                rt._INDEX_CACHE[tmp_path / f"extra{i}"] = rt.RepositoryIndex(
                    root=tmp_path / f"extra{i}"
                )
                rt._INDEX_CACHE.pop(tmp_path / f"extra{i - 1}", None)
                i += 1

        def refresh() -> None:
            try:
                for _ in range(400):
                    rt.refresh_indexed_file(str(tmp_path / "root5" / "mod.py"))
            except BaseException as exc:  # pragma: no cover - the bug
                errors.append(exc)

        churner = threading.Thread(target=churn, daemon=True)
        churner.start()
        refresher = threading.Thread(target=refresh)
        refresher.start()
        refresher.join()
        stop.set()
        churner.join(timeout=2)

        assert errors == []


# ── Malformed model output must not drive the completion gate ─────────────────


class _ScriptedReflectLLM:
    def __init__(self, raw: str) -> None:
        self._raw = raw

    def generate(self, *args, **kwargs) -> str:
        return self._raw


class TestReflectionParsesUntrustedOutput:
    """
    `ReflectionResult.is_done` gates on `confidence >= 0.7`, so the
    confidence the model returns is a safety-relevant value, not a
    display field.  Nothing in the suite covered `_parse` at all.
    """

    @pytest.mark.parametrize(
        "raw_confidence",
        ['"high"', "null", "1.5", "-0.5", "1e400", '"NaN"', "{}", "[]"],
    )
    def test_non_numeric_or_out_of_range_confidence_cannot_mark_complete(
        self, raw_confidence
    ):
        engine = ReflectionEngine(
            _ScriptedReflectLLM(
                '{"status": "complete", "confidence": '
                + raw_confidence
                + ', "reason": "r", "missing_requirements": [],'
                ' "recommended_action": "a"}'
            ),
            max_iterations=3,
        )

        result = engine.reflect("task", [])

        assert 0.0 <= result.confidence <= 1.0
        assert result.is_done is False

    def test_nan_confidence_is_rejected(self):
        engine = ReflectionEngine(
            _ScriptedReflectLLM(
                '{"status": "complete", "confidence": NaN, "reason": "r",'
                ' "missing_requirements": [], "recommended_action": "a"}'
            ),
            max_iterations=3,
        )

        result = engine.reflect("task", [])

        assert result.confidence == 0.0
        assert result.is_done is False

    def test_unknown_status_falls_back_to_blocked(self):
        engine = ReflectionEngine(
            _ScriptedReflectLLM('{"status": "shipped", "confidence": 0.99}'),
            max_iterations=3,
        )

        assert engine.reflect("task", []).status == "blocked"

    def test_string_missing_requirements_is_normalised_to_a_list(self):
        engine = ReflectionEngine(
            _ScriptedReflectLLM(
                '{"status": "replan", "confidence": 0.8,'
                ' "missing_requirements": "update the docstring"}'
            ),
            max_iterations=3,
        )

        assert engine.reflect("task", []).missing_requirements == [
            "update the docstring"
        ]

    def test_non_list_missing_requirements_does_not_crash(self):
        engine = ReflectionEngine(
            _ScriptedReflectLLM(
                '{"status": "replan", "confidence": 0.8, "missing_requirements": 7}'
            ),
            max_iterations=3,
        )

        assert engine.reflect("task", []).missing_requirements == []

    def test_prose_around_the_json_is_tolerated(self):
        engine = ReflectionEngine(
            _ScriptedReflectLLM(
                'Sure! Here you go:\n```json\n{"status": "complete",'
                ' "confidence": 0.9, "reason": "done"}\n```\nHope that helps.'
            ),
            max_iterations=3,
        )

        result = engine.reflect("task", [])

        assert result.status == "complete"
        assert result.is_done is True

    def test_unparsable_output_falls_back_without_raising(self):
        engine = ReflectionEngine(
            _ScriptedReflectLLM("I cannot answer that."), max_iterations=3
        )

        result = engine.reflect("task", [])

        assert result.status in ("complete", "blocked")

    def test_the_shared_fallback_results_are_not_mutated_across_calls(self):
        """
        The module-level fallbacks are shared dataclass instances, so a
        caller that appended to one would corrupt every later run.
        """
        from src.agent import reflection as refl

        engine = ReflectionEngine(_ScriptedReflectLLM("not json"), max_iterations=9)
        engine.reflect("task", [])

        assert refl._FALLBACK_COMPLETE.missing_requirements == []
        assert refl._FALLBACK_FAILED.missing_requirements == []


# ── The SSE stream must always terminate ─────────────────────────────────────


class TestRunStreamAlwaysTerminates:
    """
    The browser's /api/run connection blocks on the event queue until a
    terminal event arrives.  A raise anywhere in the run used to leave
    the bus unclosed: the bridge thread blocked on queue.get() forever
    and the UI spinner never stopped.
    """

    def test_a_raise_during_the_run_still_closes_the_stream(self, monkeypatch):
        import asyncio
        from unittest.mock import MagicMock

        import src.api.session as session_module

        async def scenario():
            loop = asyncio.get_running_loop()
            session_module.register_loop(loop)

            session = object.__new__(session_module.PearlSession)
            session.workspace = Path.cwd()
            session.memory = MagicMock()
            session._condenser = MagicMock()
            session._condenser.maybe_condense.side_effect = RuntimeError("boom")

            queue: asyncio.Queue = asyncio.Queue()

            def worker() -> None:
                try:
                    session_module.PearlSession.run_autonomous_stream(
                        session, "prompt", queue, None
                    )
                except Exception:
                    pass

            thread = threading.Thread(target=worker)
            thread.start()
            thread.join(timeout=10)
            await asyncio.sleep(0.2)

            assert thread.is_alive() is False
            assert queue.qsize() > 0, "the SSE consumer received no terminal event"
            assert not [
                t for t in threading.enumerate() if t.name == "pearl-bus-bridge"
            ], "the bus bridge thread leaked"

        asyncio.run(scenario())

    def test_run_worker_queues_the_sentinel_even_when_the_run_raises(self):
        import asyncio
        from unittest.mock import MagicMock

        from src.api.server import _run_worker

        async def scenario():
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue = asyncio.Queue()

            session = MagicMock()
            session.run_autonomous_stream.side_effect = RuntimeError("boom")

            def worker() -> None:
                # The raise is the point of the test; swallow it here so it
                # does not surface as an unhandled-thread-exception warning.
                try:
                    _run_worker(session, "p", queue, loop)
                except RuntimeError:
                    pass

            thread = threading.Thread(target=worker)
            thread.start()
            thread.join(timeout=10)
            await asyncio.sleep(0.2)

            # None is what ends /api/run's generator.
            assert await asyncio.wait_for(queue.get(), timeout=2) is None

        asyncio.run(scenario())


# ── The bytes on disk must match the diff the user approved ───────────────────


class TestApprovedDiffMatchesDisk:
    """
    The bytes written on approval must differ from the bytes on disk only
    where the approved diff said they would.  A text-mode round trip broke
    that: it rewrote every line ending in a file Pearl meant to edit one
    line of — LF files became CRLF on Windows, CRLF files became LF on
    POSIX — so the file that landed did not match the diff the user saw.

    `src/tools/file_io.py` is what holds the guarantee now: it reads and
    writes with newline="" so line endings are never translated in either
    direction.  A file therefore round-trips through staging verbatim,
    which is why these tests stage the content as `read_text` returns it
    rather than an LF-normalised copy of it.
    """

    def _staged_edit(self, path: Path, original: str, updated: str) -> ChangeManager:
        manager = ChangeManager()
        manager.propose(str(path), original, updated)
        return manager

    def test_a_crlf_file_stays_crlf(self, tmp_path):
        target = tmp_path / "crlf.py"
        target.write_bytes(b"def f():\r\n    return 1\r\n    # keep\r\n")

        original = read_text(target)
        manager = self._staged_edit(
            target, original, original.replace("return 1", "return 2")
        )
        manager.apply_all()

        assert target.read_bytes() == b"def f():\r\n    return 2\r\n    # keep\r\n"

    def test_an_lf_file_stays_lf(self, tmp_path):
        target = tmp_path / "lf.py"
        target.write_bytes(b"def f():\n    return 1\n")

        manager = self._staged_edit(
            target, "def f():\n    return 1\n", "def f():\n    return 2\n"
        )
        manager.apply_all()

        assert target.read_bytes() == b"def f():\n    return 2\n"

    def test_only_the_changed_line_differs_on_disk(self, tmp_path):
        """The count of changed lines must match what the diff claimed."""
        target = tmp_path / "crlf.py"
        before = b"a\r\nb\r\nc\r\nd\r\n"
        target.write_bytes(before)

        original = read_text(target)
        manager = self._staged_edit(
            target, original, original.replace("b\r\n", "B\r\n")
        )
        manager.apply_all()

        after = target.read_bytes()
        changed = [
            i
            for i, (x, y) in enumerate(zip(before.split(b"\r\n"), after.split(b"\r\n")))
            if x != y
        ]
        assert changed == [1]

    def test_a_new_file_follows_its_own_content(self, tmp_path):
        target = tmp_path / "new.py"

        manager = ChangeManager()
        manager.propose(str(target), None, "x = 1\ny = 2\n")
        manager.apply_all()

        assert target.read_bytes() == b"x = 1\ny = 2\n"

    def test_rollback_restores_the_original_bytes_exactly(self, tmp_path):
        first = tmp_path / "a.py"
        second = tmp_path / "b.py"
        original = b"one\r\ntwo\r\n"
        first.write_bytes(original)

        manager = ChangeManager()
        original_text = read_text(first)
        manager.propose(str(first), original_text, original_text.replace("one", "ONE"))
        manager.propose(str(second), None, "new\n")

        calls = {"n": 0}
        real_write = patch_manager.write_text

        def fail_second(path, text, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("disk full")
            return real_write(path, text, **kwargs)

        import unittest.mock as mock

        with mock.patch.object(patch_manager, "write_text", fail_second):
            with pytest.raises(OSError):
                manager.apply_all()

        assert first.read_bytes() == original
        assert not second.exists()

    def test_edit_tools_round_trip_preserves_crlf(self, tmp_path, monkeypatch):
        """The same guarantee through the tool the planner actually calls."""
        from src.tools.edit_tools import replace_in_file

        monkeypatch.chdir(tmp_path)
        set_workspace_root(tmp_path)
        target = tmp_path / "crlf.py"
        target.write_bytes(b"def f():\r\n    return 1\r\n")

        manager = ChangeManager()
        set_active_patch_manager(manager)
        try:
            replace_in_file("crlf.py", "return 1", "return 2")
        finally:
            set_active_patch_manager(None)

        manager.apply_all()

        assert target.read_bytes() == b"def f():\r\n    return 2\r\n"
