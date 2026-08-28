"""
Tests for Phase 6 — RepositoryService (src/repository/service.py).

Coverage
--------
* Construction — get_or_build resolves to absolute path, repr
* Lazy build — .index and .graph trigger _build() on first access
* Cache hit — same root returns the same object; no second build
* is_ready — False before any access, True after
* invalidate — resets index/graph; next access rebuilds
* clear_cache — empties class-level dict
* Real pipeline — service built from real Python files produces
  non-empty index and graph
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.repository.service import RepositoryService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(root: Path, rel: str, content: str) -> None:
    full = root / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Cache and construction
# ---------------------------------------------------------------------------


class TestRepositoryServiceCache:
    def setup_method(self) -> None:
        RepositoryService.clear_cache()

    def teardown_method(self) -> None:
        RepositoryService.clear_cache()

    def test_get_or_build_returns_same_object_for_same_root(self, tmp_path: Path) -> None:
        svc1 = RepositoryService.get_or_build(tmp_path)
        svc2 = RepositoryService.get_or_build(tmp_path)
        assert svc1 is svc2

    def test_get_or_build_different_roots_different_objects(self, tmp_path: Path) -> None:
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        root_a.mkdir()
        root_b.mkdir()
        svc_a = RepositoryService.get_or_build(root_a)
        svc_b = RepositoryService.get_or_build(root_b)
        assert svc_a is not svc_b

    def test_get_or_build_resolves_relative_dot_to_absolute(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        svc_dot = RepositoryService.get_or_build(Path("."))
        svc_abs = RepositoryService.get_or_build(tmp_path)
        assert svc_dot is svc_abs

    def test_clear_cache_empties_dict(self, tmp_path: Path) -> None:
        RepositoryService.get_or_build(tmp_path)
        RepositoryService.clear_cache()
        # After clear, a new call constructs a fresh object
        svc_after = RepositoryService.get_or_build(tmp_path)
        assert svc_after is not None
        assert not svc_after.is_ready()


# ---------------------------------------------------------------------------
# Lazy build and is_ready
# ---------------------------------------------------------------------------


class TestRepositoryServiceLazyBuild:
    def setup_method(self) -> None:
        RepositoryService.clear_cache()

    def teardown_method(self) -> None:
        RepositoryService.clear_cache()

    def test_is_ready_false_before_any_access(self, tmp_path: Path) -> None:
        svc = RepositoryService.get_or_build(tmp_path)
        assert not svc.is_ready()

    def test_index_access_triggers_build(self, tmp_path: Path) -> None:
        _write(tmp_path, "hello.py", "def hello(): pass\n")
        svc = RepositoryService.get_or_build(tmp_path)
        _ = svc.index
        assert svc.is_ready()

    def test_graph_access_triggers_build(self, tmp_path: Path) -> None:
        _write(tmp_path, "hello.py", "def hello(): pass\n")
        svc = RepositoryService.get_or_build(tmp_path)
        _ = svc.graph
        assert svc.is_ready()

    def test_second_access_does_not_rebuild(self, tmp_path: Path) -> None:
        _write(tmp_path, "hello.py", "def hello(): pass\n")
        svc = RepositoryService.get_or_build(tmp_path)
        idx1 = svc.index
        idx2 = svc.index
        assert idx1 is idx2

    def test_empty_directory_builds_empty_index(self, tmp_path: Path) -> None:
        svc = RepositoryService.get_or_build(tmp_path)
        idx = svc.index
        assert idx.stats().file_count == 0
        assert idx.stats().symbol_count == 0


# ---------------------------------------------------------------------------
# Invalidate
# ---------------------------------------------------------------------------


class TestRepositoryServiceInvalidate:
    def setup_method(self) -> None:
        RepositoryService.clear_cache()

    def teardown_method(self) -> None:
        RepositoryService.clear_cache()

    def test_invalidate_resets_ready_state(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.py", "class A: pass\n")
        svc = RepositoryService.get_or_build(tmp_path)
        _ = svc.index
        assert svc.is_ready()
        svc.invalidate()
        assert not svc.is_ready()

    def test_access_after_invalidate_rebuilds(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.py", "class A: pass\n")
        svc = RepositoryService.get_or_build(tmp_path)
        idx_before = svc.index
        svc.invalidate()
        idx_after = svc.index
        # A fresh build returns a new object
        assert idx_before is not idx_after


# ---------------------------------------------------------------------------
# Real pipeline
# ---------------------------------------------------------------------------


class TestRepositoryServiceRealPipeline:
    def setup_method(self) -> None:
        RepositoryService.clear_cache()

    def teardown_method(self) -> None:
        RepositoryService.clear_cache()

    def test_service_indexes_python_files(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/agent.py",
            "class PearlAgent:\n    def run(self): pass\n",
        )
        _write(
            tmp_path,
            "src/tools.py",
            "from src.agent import PearlAgent\ndef helper(): pass\n",
        )
        svc = RepositoryService.get_or_build(tmp_path)
        idx = svc.index

        assert idx.stats().file_count == 2
        entries = idx.lookup("PearlAgent")
        assert entries, "PearlAgent should be indexed"
        assert entries[0].relative_path == "src/agent.py"

    def test_service_builds_import_graph(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.py", "")
        _write(tmp_path, "b.py", "from a import something\n")
        svc = RepositoryService.get_or_build(tmp_path)
        g = svc.graph

        stats = g.stats()
        assert stats.file_node_count == 2

    def test_repr_shows_ready_state(self, tmp_path: Path) -> None:
        svc = RepositoryService.get_or_build(tmp_path)
        assert "unbuilt" in repr(svc)
        _ = svc.index
        assert "ready" in repr(svc)
