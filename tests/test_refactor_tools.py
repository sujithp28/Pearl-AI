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

        from src.tools.patch_manager import ChangeManager
        from src.tools.edit_tools import set_active_patch_manager

        pm = ChangeManager()
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
        result = rename_symbol("NewFoo", "NewFoo")
        assert "nothing to do" in result.lower()

    def test_no_occurrences_returns_not_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.py").write_text("class Bar: pass\n")
        # Old and new must differ, or this exercises the same-name guard
        # above instead of the not-found path it is named for.
        result = rename_symbol("Foo", "NewFoo")
        assert "No occurrences" in result

    # These fixtures deliberately use invented names rather than real
    # Pearl classes. They previously renamed PatchManager -> ChangeManager,
    # and when that rename was actually performed on the codebase, the
    # tool rewrote these literals too — collapsing both sides so the test
    # renamed a symbol to itself and asserted the result was both present
    # and absent. An automated rename cannot tell a reference from a
    # string that merely happens to spell an identifier, so test data
    # must not spell one that exists.

    def test_renames_in_single_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.py").write_text(
            "class OldWidget:\n    def stage(self): pass\n\npm = OldWidget()\n"
        )
        result = rename_symbol("OldWidget", "NewWidget")
        assert "NewWidget" in (tmp_path / "a.py").read_text()
        assert "OldWidget" not in (tmp_path / "a.py").read_text()
        assert "a.py" in result

    def test_renames_across_multiple_files(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "widget.py").write_text("class OldWidget: pass\n")
        (tmp_path / "consumer.py").write_text("from widget import OldWidget\npm = OldWidget()\n")
        (tmp_path / "unrelated.py").write_text("class Other: pass\n")
        rename_symbol("OldWidget", "NewWidget")
        assert "NewWidget" in (tmp_path / "widget.py").read_text()
        assert "NewWidget" in (tmp_path / "consumer.py").read_text()
        # Unrelated file untouched
        assert "OldWidget" not in (tmp_path / "unrelated.py").read_text()

    def test_word_boundary_no_false_positives(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.py").write_text(
            "class OldWidget: pass\n"
            "class ActiveOldWidget: pass\n"
            "pm = OldWidget()\n"
        )
        rename_symbol("OldWidget", "NewWidget")
        text = (tmp_path / "a.py").read_text()
        # The whole-word version is renamed
        assert "pm = NewWidget()" in text
        # The compound name is preserved — the word boundary stops the
        # match, so ActiveOldWidget is not silently rewritten.
        assert "ActiveOldWidget" in text

    def test_preview_mode_stages_not_writes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "a.py").write_text("class NewFoo: pass\nfoo = NewFoo()\n")

        from src.tools.patch_manager import ChangeManager
        from src.tools.edit_tools import set_active_patch_manager

        pm = ChangeManager()
        set_active_patch_manager(pm)
        try:
            result = rename_symbol("NewFoo", "Bar")
            # Staged for review
            assert "staged for review" in result
            assert len(pm.pending) == 1
            # Original on disk unchanged
            assert "NewFoo" in (tmp_path / "a.py").read_text()
        finally:
            set_active_patch_manager(None)
