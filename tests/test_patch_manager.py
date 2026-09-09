import unittest.mock as mock
from pathlib import Path

import pytest

from src.tools.patch_manager import ChangeManager, unified_diff

# ---------------------------------------------------------------------
# unified_diff
# ---------------------------------------------------------------------


def test_unified_diff_shows_added_and_removed_lines():
    diff = unified_diff("a.txt", "line1\nline2\n", "line1\nline2 changed\n")

    assert "a/a.txt" in diff
    assert "b/a.txt" in diff
    assert "-line2" in diff
    assert "+line2 changed" in diff


def test_unified_diff_for_new_file_uses_dev_null():
    diff = unified_diff("new.txt", None, "hello\n")

    assert "/dev/null" in diff
    assert "+hello" in diff


def test_unified_diff_empty_for_identical_content():
    diff = unified_diff("a.txt", "same\n", "same\n")

    assert diff == ""


# ---------------------------------------------------------------------
# ChangeManager: proposing and inspecting
# ---------------------------------------------------------------------


def test_propose_stages_an_edit_without_touching_state():
    manager = ChangeManager()

    edit = manager.propose("a.txt", "old\n", "new\n")

    assert manager.has_pending()
    assert manager.pending == [edit]
    assert edit.path == "a.txt"
    assert edit.original_content == "old\n"
    assert edit.updated_content == "new\n"
    assert "-old" in edit.diff
    assert "+new" in edit.diff
    assert not edit.is_new_file


def test_propose_marks_new_files():
    manager = ChangeManager()

    edit = manager.propose("new.txt", None, "hello\n")

    assert edit.is_new_file


def test_has_pending_false_when_empty():
    manager = ChangeManager()

    assert not manager.has_pending()
    assert manager.pending == []


def test_affected_files_lists_every_staged_path_in_order():
    manager = ChangeManager()

    manager.propose("a.txt", "1", "2")
    manager.propose("b.txt", None, "new")

    assert manager.affected_files() == ["a.txt", "b.txt"]


def test_combined_diff_concatenates_every_staged_diff():
    manager = ChangeManager()

    manager.propose("a.txt", "1\n", "2\n")
    manager.propose("b.txt", "3\n", "4\n")

    combined = manager.combined_diff()

    assert "a.txt" in combined
    assert "b.txt" in combined


# ---------------------------------------------------------------------
# apply_all / discard_all
# ---------------------------------------------------------------------


def test_apply_all_writes_every_pending_edit_and_clears_pending(tmp_path):
    manager = ChangeManager()

    file_a = tmp_path / "a.txt"
    file_a.write_text("old\n")
    file_b = tmp_path / "sub" / "b.txt"

    manager.propose(str(file_a), "old\n", "new\n")
    manager.propose(str(file_b), None, "created\n")

    applied = manager.apply_all()

    assert set(applied) == {str(file_a), str(file_b)}
    assert file_a.read_text() == "new\n"
    assert file_b.read_text() == "created\n"
    assert not manager.has_pending()


def test_apply_all_creates_parent_directories(tmp_path):
    manager = ChangeManager()
    nested = tmp_path / "a" / "b" / "c.txt"

    manager.propose(str(nested), None, "deep\n")
    manager.apply_all()

    assert nested.read_text() == "deep\n"


def test_apply_all_on_empty_pending_writes_nothing_and_returns_empty(
    tmp_path,
):
    manager = ChangeManager()

    assert manager.apply_all() == []


def test_discard_all_writes_nothing(tmp_path):
    manager = ChangeManager()
    file_a = tmp_path / "a.txt"

    manager.propose(str(file_a), None, "should not appear\n")

    discarded = manager.discard_all()

    assert discarded == [str(file_a)]
    assert not file_a.exists()
    assert not manager.has_pending()


def test_discard_all_on_empty_pending_returns_empty():
    manager = ChangeManager()

    assert manager.discard_all() == []


# ---------------------------------------------------------------------
# Atomicity: apply_all rollback on partial failure
# ---------------------------------------------------------------------


def test_apply_all_succeeds_all_or_none_on_success(tmp_path):
    """All files written when no error occurs."""
    manager = ChangeManager()
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    manager.propose(str(a), None, "alpha\n")
    manager.propose(str(b), None, "beta\n")

    applied = manager.apply_all()

    assert set(applied) == {str(a), str(b)}
    assert a.read_text() == "alpha\n"
    assert b.read_text() == "beta\n"
    assert not manager.has_pending()


def test_apply_all_rollback_restores_existing_file_on_failure(tmp_path, monkeypatch):
    """If the second write fails, the first write is rolled back."""
    manager = ChangeManager()
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("original\n")

    manager.propose(str(a), "original\n", "modified\n")
    manager.propose(str(b), None, "new\n")

    call_count = {"n": 0}
    real_write = Path.write_text

    def _fail_second(self, text, **kw):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("simulated disk full")
        return real_write(self, text, **kw)

    monkeypatch.setattr(Path, "write_text", _fail_second)

    with pytest.raises(OSError, match="simulated disk full"):
        manager.apply_all()

    # a.txt must be restored to its original content.
    assert a.read_text() == "original\n"
    # b.txt must not exist (it was the failing write, and rollback removes it).
    assert not b.exists()
    # Pending list must remain intact so the caller can inspect.
    assert manager.has_pending()


def test_apply_all_rollback_deletes_new_file_on_failure(tmp_path, monkeypatch):
    """A new file that was written before the failure is deleted on rollback."""
    manager = ChangeManager()
    a = tmp_path / "new_a.txt"
    b = tmp_path / "new_b.txt"

    manager.propose(str(a), None, "aaa\n")
    manager.propose(str(b), None, "bbb\n")

    call_count = {"n": 0}
    real_write = Path.write_text

    def _fail_second(self, text, **kw):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise OSError("disk error")
        return real_write(self, text, **kw)

    monkeypatch.setattr(Path, "write_text", _fail_second)

    with pytest.raises(OSError):
        manager.apply_all()

    # First new file was written then rolled back (deleted).
    assert not a.exists()
    assert not b.exists()


def test_apply_all_pending_cleared_only_on_success(tmp_path):
    """Pending list is NOT cleared when apply_all() raises."""
    manager = ChangeManager()
    manager.propose(str(tmp_path / "x.txt"), None, "x\n")

    with mock.patch.object(Path, "write_text", side_effect=OSError("fail")):
        with pytest.raises(OSError):
            manager.apply_all()

    assert manager.has_pending()


def test_apply_all_empty_returns_empty_list(tmp_path):
    """Calling apply_all() with no pending edits is a no-op."""
    manager = ChangeManager()
    assert manager.apply_all() == []


# ---------------------------------------------------------------------
# Deletions
#
# A deletion is a staged edit whose `updated_content` is None. It rides
# the same batch as content edits so the user reviews everything a run
# wants to do to their workspace in one approval, not two.
# ---------------------------------------------------------------------


def test_propose_deletion_stages_without_touching_disk(tmp_path):
    victim = tmp_path / "gone.txt"
    victim.write_text("keep me\n", encoding="utf-8")

    manager = ChangeManager()
    edit = manager.propose_deletion(str(victim), "keep me\n")

    assert victim.exists()
    assert edit.is_deletion
    assert not edit.is_new_file
    assert edit.updated_content is None


def test_deletion_diff_removes_every_line(tmp_path):
    manager = ChangeManager()
    edit = manager.propose_deletion(str(tmp_path / "gone.txt"), "one\ntwo\n")

    assert "-one" in edit.diff
    assert "-two" in edit.diff
    assert "/dev/null" in edit.diff


def test_apply_all_unlinks_a_staged_deletion(tmp_path):
    victim = tmp_path / "gone.txt"
    victim.write_text("bye\n", encoding="utf-8")

    manager = ChangeManager()
    manager.propose_deletion(str(victim), "bye\n")

    assert manager.apply_all() == [str(victim)]
    assert not victim.exists()


def test_deletion_and_edit_apply_in_one_batch(tmp_path):
    victim = tmp_path / "gone.txt"
    victim.write_text("bye\n", encoding="utf-8")
    kept = tmp_path / "kept.txt"

    manager = ChangeManager()
    manager.propose_deletion(str(victim), "bye\n")
    manager.propose(str(kept), None, "hello\n")

    manager.apply_all()

    assert not victim.exists()
    assert kept.read_text(encoding="utf-8") == "hello\n"


def test_rollback_restores_a_deleted_file_when_a_later_write_fails(tmp_path):
    """
    The dangerous ordering: a deletion succeeds, then a write in the
    same batch fails. The deleted file must come back.
    """
    victim = tmp_path / "gone.txt"
    victim.write_text("precious\n", encoding="utf-8")

    manager = ChangeManager()
    manager.propose_deletion(str(victim), "precious\n")
    manager.propose(str(tmp_path / "next.txt"), None, "x\n")

    # Fail only the first write (next.txt), so the rollback's own
    # restoring write is still allowed to succeed.
    real_write = Path.write_text
    calls = {"n": 0}

    def _fail_first(self, text, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full")
        return real_write(self, text, **kw)

    with mock.patch.object(Path, "write_text", _fail_first):
        with pytest.raises(OSError):
            manager.apply_all()

    assert victim.read_text(encoding="utf-8") == "precious\n"


def test_discard_all_reports_a_staged_deletion(tmp_path):
    manager = ChangeManager()
    manager.propose_deletion(str(tmp_path / "gone.txt"), "x\n")

    assert manager.discard_all() == [str(tmp_path / "gone.txt")]
    assert not manager.has_pending()
