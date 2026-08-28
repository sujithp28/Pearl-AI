"""
Tests for M4 multi-file refactoring tools (src/tools/refactor_tools.py).

Coverage
--------
* batch_write_files — path validation, empty input, too many files,
  direct write, preview-mode staging, directory creation, index refresh
* rename_symbol — bad identifiers, same name, no occurrences,
  word-boundary matching, multi-file rename, preview-mode staging
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.tools.refactor_tools import batch_write_files, rename_symbol


# ---------------------------------------------------------------------------
# batch_write_files
# ---------------------------------------------------------------------------


class TestBatchWriteFiles:
    def test_raises_on_empty_dict(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="must not be empty"):
            batch_write_files({})

    def test_raises_when_too_many_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        files = {f"f{i}.py": f"# {i}" for i in range(51)}
        with pytest.raises(ValueError, match="Too many files"):
            batch_write_files(files)

    def test_raises_on_path_outside_workspace(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        with pytest.raises(PermissionError):
            batch_write_files({"/etc/evil.py": "boom"})

    def test_writes_files_in_apply_mode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        files = {
            "a.py": "x = 1\n",
            "b.py": "y = 2\n",
        }
        result = batch_write_files(files)
        assert (tmp_path / "a.py").read_text() == "x = 1\n"
        assert (tmp_path / "b.py").read_text() == "y = 2\n"
        assert "Wrote 2" in result

    def test_creates_parent_directories(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        batch_write_files({"src/deep/nested/a.py": "pass\n"})
        assert (tmp_path / "src/deep/nested/a.py").exists()

    def test_preview_mode_stages_all_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)

        from src.tools.patch_manager import PatchManager
        from src.tools.edit_tools import set_active_patch_manager

        pm = PatchManager()
        set_active_patch_manager(pm)
        try:
            result = batch_write_files({"a.py": "x=1\n", "b.py": "y=2\n"})
            assert "staged" in result.lower()
            # Both files queued in the patch manager
            assert len(pm.pending) == 2
            # Nothing written to disk
            assert not (tmp_path / "a.py").exists()
            assert not (tmp_path / "b.py").exists()
        finally:
            set_active_patch_manager(None)

    def test_validates_all_paths_before_writing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        # One valid, one invalid — nothing should be written
        with pytest.raises(PermissionError):
            batch_write_files({"good.py": "x=1\n", "/etc/bad.py": "boom"})
        assert not (tmp_path / "good.py").exists()


# ---------------------------------------------------------------------------
# rename_symbol
# ---------------------------------------------------------------------------


class TestRenameSymbol:
    def test_raises_on_empty_old_name(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="old_name"):
            rename_symbol("", "NewName")

    def test_raises_on_non_identifier_new_name(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError, match="new_name"):
            rename_symbol("OldName", "new-name-invalid")

    def test_same_name_returns_nothing_to_do(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = rename_symbol("Foo", "Foo")
        assert "nothing to do" in result.lower()

    def test_no_occurrences_returns_not_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.py").write_text("class Bar: pass\n")
        result = rename_symbol("Foo", "NewFoo")
        assert "No occurrences" in result

    def test_renames_in_single_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.py").write_text(
            "class PatchManager:\n    def stage(self): pass\n\npm = PatchManager()\n"
        )
        result = rename_symbol("PatchManager", "ChangeManager")
        assert "ChangeManager" in (tmp_path / "a.py").read_text()
        assert "PatchManager" not in (tmp_path / "a.py").read_text()
        assert "a.py" in result

    def test_renames_across_multiple_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "patch_manager.py").write_text("class PatchManager: pass\n")
        (tmp_path / "executor.py").write_text("from patch_manager import PatchManager\npm = PatchManager()\n")
        (tmp_path / "unrelated.py").write_text("class Other: pass\n")
        rename_symbol("PatchManager", "ChangeManager")
        assert "ChangeManager" in (tmp_path / "patch_manager.py").read_text()
        assert "ChangeManager" in (tmp_path / "executor.py").read_text()
        # Unrelated file untouched
        assert "PatchManager" not in (tmp_path / "unrelated.py").read_text()

    def test_word_boundary_no_false_positives(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.py").write_text(
            "class PatchManager: pass\n"
            "class ActivePatchManager: pass\n"
            "pm = PatchManager()\n"
        )
        rename_symbol("PatchManager", "ChangeManager")
        text = (tmp_path / "a.py").read_text()
        # The whole-word version is renamed
        assert "pm = ChangeManager()" in text
        # The compound name is preserved — word boundary stops at 'e' in 'ActivePatch'
        assert "ActivePatchManager" in text

    def test_preview_mode_stages_not_writes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.py").write_text("class Foo: pass\nfoo = Foo()\n")

        from src.tools.patch_manager import PatchManager
        from src.tools.edit_tools import set_active_patch_manager

        pm = PatchManager()
        set_active_patch_manager(pm)
        try:
            result = rename_symbol("Foo", "Bar")
            # Staged for review
            assert "staged for review" in result
            assert len(pm.pending) == 1
            # Original on disk unchanged
            assert "Foo" in (tmp_path / "a.py").read_text()
        finally:
            set_active_patch_manager(None)
