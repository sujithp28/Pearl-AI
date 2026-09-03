"""
P6 regression tests — workspace-root propagation.

Verifies that:
  - run_autonomous_stream() no longer calls os.chdir()
  - tools use the thread-local workspace root, not Path.cwd()
  - concurrent sessions retain their own workspace root
  - relative paths resolve against the correct workspace
  - _ensure_within_workspace() still blocks path traversal
"""

from __future__ import annotations

import inspect
import os
import threading
from pathlib import Path

import pytest

from src.config.workspace import clear_workspace_root, get_workspace_root, set_workspace_root


# ---------------------------------------------------------------------------
# Workspace ContextVar helpers
# ---------------------------------------------------------------------------


class TestWorkspaceHelpers:
    def test_get_workspace_root_fallback(self, tmp_path: Path) -> None:
        """When no root is set, get_workspace_root() returns Path.cwd()."""
        clear_workspace_root()
        assert get_workspace_root() == Path.cwd().resolve()

    def test_set_and_get(self, tmp_path: Path) -> None:
        clear_workspace_root()
        set_workspace_root(tmp_path)
        assert get_workspace_root() == tmp_path.resolve()

    def test_clear_restores_fallback(self, tmp_path: Path) -> None:
        set_workspace_root(tmp_path)
        clear_workspace_root()
        assert get_workspace_root() == Path.cwd().resolve()

    def test_resolves_path(self, tmp_path: Path) -> None:
        """set_workspace_root() resolves the path."""
        # Create a sub-dir so we can pass a non-resolved path
        sub = tmp_path / "a" / ".." / "b"
        sub.mkdir(parents=True, exist_ok=True)
        set_workspace_root(sub)
        assert get_workspace_root() == sub.resolve()
        clear_workspace_root()


# ---------------------------------------------------------------------------
# Thread isolation
# ---------------------------------------------------------------------------


class TestConcurrentWorkspaceIsolation:
    def test_threads_have_independent_workspace_roots(self, tmp_path: Path) -> None:
        """Two threads setting different roots do not interfere."""
        ws_a = tmp_path / "workspace_a"
        ws_b = tmp_path / "workspace_b"
        ws_a.mkdir(); ws_b.mkdir()

        results: dict[str, Path | None] = {"a": None, "b": None}
        barrier = threading.Barrier(2)

        def run_a() -> None:
            set_workspace_root(ws_a)
            barrier.wait()  # both threads have set their root
            results["a"] = get_workspace_root()

        def run_b() -> None:
            set_workspace_root(ws_b)
            barrier.wait()
            results["b"] = get_workspace_root()

        t_a = threading.Thread(target=run_a, daemon=True)
        t_b = threading.Thread(target=run_b, daemon=True)
        t_a.start(); t_b.start()
        t_a.join(); t_b.join()

        assert results["a"] == ws_a.resolve(), "Thread A got the wrong workspace"
        assert results["b"] == ws_b.resolve(), "Thread B got the wrong workspace"
        assert results["a"] != results["b"]

    def test_main_thread_unaffected_by_worker_thread(self, tmp_path: Path) -> None:
        """A worker thread setting a workspace root does not affect the main thread."""
        clear_workspace_root()
        initial = get_workspace_root()

        ws_worker = tmp_path / "worker_ws"
        ws_worker.mkdir()
        done = threading.Event()

        def worker() -> None:
            set_workspace_root(ws_worker)
            done.wait()  # hold until main thread has sampled its root

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        sampled = get_workspace_root()
        done.set()
        t.join()

        assert sampled == initial, (
            "Worker thread workspace root leaked into main thread"
        )


# ---------------------------------------------------------------------------
# _ensure_within_workspace uses thread-local root
# ---------------------------------------------------------------------------


class TestEnsureWithinWorkspace:
    def test_resolves_against_workspace_root(self, tmp_path: Path) -> None:
        """Files under the workspace are allowed."""
        from src.tools.file_tools import _ensure_within_workspace

        set_workspace_root(tmp_path)
        target = tmp_path / "src" / "foo.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()

        resolved = _ensure_within_workspace(str(target))
        assert resolved == target.resolve()
        clear_workspace_root()

    def test_blocks_traversal_above_workspace(self, tmp_path: Path) -> None:
        """Path traversal above workspace root is blocked regardless of cwd."""
        from src.tools.file_tools import _ensure_within_workspace

        ws = tmp_path / "workspace"
        ws.mkdir()
        set_workspace_root(ws)

        # Attempt to escape to the parent of workspace
        escape = str(ws / ".." / "escape.txt")
        with pytest.raises(PermissionError):
            _ensure_within_workspace(escape)

        clear_workspace_root()

    def test_workspace_root_not_overridden_by_cwd(self, tmp_path: Path) -> None:
        """Even if cwd is different, workspace root is honoured."""
        from src.tools.file_tools import _ensure_within_workspace

        ws = tmp_path / "ws"
        ws.mkdir()
        other = tmp_path / "other"
        other.mkdir()
        target = ws / "file.py"
        target.touch()

        # Set workspace to ws but cwd is other (simulate old os.chdir() not called)
        original_cwd = os.getcwd()
        set_workspace_root(ws)
        try:
            os.chdir(str(other))
            resolved = _ensure_within_workspace(str(target))
            assert resolved == target.resolve()
        finally:
            os.chdir(original_cwd)
            clear_workspace_root()


# ---------------------------------------------------------------------------
# session.py no longer calls os.chdir()
# ---------------------------------------------------------------------------


class TestSessionNoChdir:
    def test_run_autonomous_stream_does_not_call_os_chdir(self) -> None:
        """run_autonomous_stream() must not call os.chdir() (G4 equivalent for agent runs)."""
        from src.api.session import PearlSession

        source = inspect.getsource(PearlSession.run_autonomous_stream)
        assert "os.chdir" not in source, (
            "run_autonomous_stream() still calls os.chdir() — remove it (P6 fix)"
        )

    def test_run_autonomous_stream_calls_set_workspace_root(self) -> None:
        """run_autonomous_stream() must set the thread-local workspace root."""
        from src.api.session import PearlSession

        source = inspect.getsource(PearlSession.run_autonomous_stream)
        assert "set_workspace_root" in source, (
            "run_autonomous_stream() must call set_workspace_root() to propagate workspace"
        )
