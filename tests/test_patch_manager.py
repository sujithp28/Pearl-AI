import pytest

from src.tools.patch_manager import PatchManager, unified_diff


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
# PatchManager: proposing and inspecting
# ---------------------------------------------------------------------


def test_propose_stages_an_edit_without_touching_state():
    manager = PatchManager()

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
    manager = PatchManager()

    edit = manager.propose("new.txt", None, "hello\n")

    assert edit.is_new_file


def test_has_pending_false_when_empty():
    manager = PatchManager()

    assert not manager.has_pending()
    assert manager.pending == []


def test_affected_files_lists_every_staged_path_in_order():
    manager = PatchManager()

    manager.propose("a.txt", "1", "2")
    manager.propose("b.txt", None, "new")

    assert manager.affected_files() == ["a.txt", "b.txt"]


def test_combined_diff_concatenates_every_staged_diff():
    manager = PatchManager()

    manager.propose("a.txt", "1\n", "2\n")
    manager.propose("b.txt", "3\n", "4\n")

    combined = manager.combined_diff()

    assert "a.txt" in combined
    assert "b.txt" in combined


# ---------------------------------------------------------------------
# apply_all / discard_all
# ---------------------------------------------------------------------


def test_apply_all_writes_every_pending_edit_and_clears_pending(tmp_path):
    manager = PatchManager()

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
    manager = PatchManager()
    nested = tmp_path / "a" / "b" / "c.txt"

    manager.propose(str(nested), None, "deep\n")
    manager.apply_all()

    assert nested.read_text() == "deep\n"


def test_apply_all_on_empty_pending_writes_nothing_and_returns_empty(
    tmp_path,
):
    manager = PatchManager()

    assert manager.apply_all() == []


def test_discard_all_writes_nothing(tmp_path):
    manager = PatchManager()
    file_a = tmp_path / "a.txt"

    manager.propose(str(file_a), None, "should not appear\n")

    discarded = manager.discard_all()

    assert discarded == [str(file_a)]
    assert not file_a.exists()
    assert not manager.has_pending()


def test_discard_all_on_empty_pending_returns_empty():
    manager = PatchManager()

    assert manager.discard_all() == []
