import pytest

from src.tools.edit_tools import (
    create_file,
    edit_lines,
    get_active_patch_manager,
    patch_file,
    replace_in_file,
    set_active_patch_manager,
)
from src.tools.patch_manager import ChangeManager


def _write_lf(path, text: str) -> None:
    """
    Write `text` byte-for-byte, with no newline translation, so a
    fixture that says LF is LF on Windows too. The editing tools now
    preserve whatever endings a file actually has, which makes a
    platform-translated fixture untestable.
    """

    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch):
    """
    Run every test with tmp_path as the workspace root, since these
    tools are workspace-contained like the file tools.
    """

    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def _no_leaked_patch_manager():
    """
    Guarantee preview mode starts (and ends) off for every test, so a
    test that forgets to clean up its own `set_active_patch_manager`
    call can't leak preview mode into an unrelated test.
    """

    assert get_active_patch_manager() is None
    yield
    set_active_patch_manager(None)


# ---------------------------------------------------------------------
# create_file
# ---------------------------------------------------------------------


def test_create_file_writes_content(tmp_path):
    file = tmp_path / "new.py"

    create_file(str(file), "print('hi')\n")

    assert file.read_text() == "print('hi')\n"


def test_create_file_makes_parent_directories(tmp_path):
    file = tmp_path / "pkg" / "module.py"

    create_file(str(file), "x = 1\n")

    assert file.exists()


def test_create_file_rejects_existing_file(tmp_path):
    file = tmp_path / "existing.txt"
    file.write_text("original")

    with pytest.raises(FileExistsError):
        create_file(str(file), "overwritten")

    assert file.read_text() == "original"


# ---------------------------------------------------------------------
# replace_in_file
# ---------------------------------------------------------------------


def test_replace_in_file_replaces_all_occurrences(tmp_path):
    file = tmp_path / "code.py"
    file.write_text("foo = 1\nfoo = foo + 1\n")

    replaced = replace_in_file(str(file), "foo", "bar")

    assert replaced == 3
    assert file.read_text() == "bar = 1\nbar = bar + 1\n"


def test_replace_in_file_respects_count(tmp_path):
    file = tmp_path / "code.py"
    file.write_text("foo foo foo")

    replaced = replace_in_file(str(file), "foo", "bar", count=2)

    assert replaced == 2
    assert file.read_text() == "bar bar foo"


def test_replace_in_file_returns_zero_when_not_found(tmp_path):
    file = tmp_path / "code.py"
    file.write_text("hello world")

    replaced = replace_in_file(str(file), "missing", "x")

    assert replaced == 0
    assert file.read_text() == "hello world"


def test_replace_in_file_rejects_empty_search(tmp_path):
    file = tmp_path / "code.py"
    file.write_text("hello")

    with pytest.raises(ValueError):
        replace_in_file(str(file), "", "x")


def test_replace_in_file_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        replace_in_file(str(tmp_path / "missing.py"), "a", "b")


# ---------------------------------------------------------------------
# edit_lines
# ---------------------------------------------------------------------


def test_edit_lines_replaces_range(tmp_path):
    file = tmp_path / "code.py"
    file.write_text("line1\nline2\nline3\nline4\n")

    edit_lines(str(file), 2, 3, "new2\nnew3")

    assert file.read_text() == "line1\nnew2\nnew3\nline4\n"


def test_edit_lines_deletes_range_with_empty_content(tmp_path):
    file = tmp_path / "code.py"
    file.write_text("line1\nline2\nline3\n")

    edit_lines(str(file), 2, 2, "")

    assert file.read_text() == "line1\nline3\n"


def test_edit_lines_preserves_missing_trailing_newline(tmp_path):
    file = tmp_path / "code.py"
    file.write_text("line1\nline2")

    edit_lines(str(file), 1, 1, "changed1")

    assert file.read_text() == "changed1\nline2"


def test_edit_lines_rejects_invalid_range(tmp_path):
    file = tmp_path / "code.py"
    file.write_text("line1\nline2\n")

    with pytest.raises(ValueError):
        edit_lines(str(file), 3, 5, "x")


# ---------------------------------------------------------------------
# patch_file
# ---------------------------------------------------------------------


def test_patch_file_applies_single_hunk(tmp_path):
    file = tmp_path / "code.py"
    file.write_text("line1\nline2\nline3\nline4\n")

    patch = "\n".join(
        [
            "@@ -1,4 +1,5 @@",
            " line1",
            "-line2",
            "+line2 modified",
            "+line2.5",
            " line3",
            " line4",
        ]
    )

    patch_file(str(file), patch)

    assert file.read_text() == ("line1\nline2 modified\nline2.5\nline3\nline4\n")


def test_patch_file_applies_multiple_hunks_with_offset(tmp_path):
    file = tmp_path / "code.py"
    file.write_text("a\nb\nc\nd\ne\n")

    patch = "\n".join(
        [
            "@@ -1,2 +1,3 @@",
            " a",
            "+a.5",
            " b",
            "@@ -4,2 +5,2 @@",
            " d",
            "-e",
            "+e modified",
        ]
    )

    patch_file(str(file), patch)

    assert file.read_text() == "a\na.5\nb\nc\nd\ne modified\n"


def test_patch_file_rejects_context_mismatch(tmp_path):
    file = tmp_path / "code.py"
    file.write_text("line1\nline2\nline3\n")

    patch = "\n".join(
        [
            "@@ -1,1 +1,1 @@",
            "-does not match",
            "+replacement",
        ]
    )

    with pytest.raises(ValueError):
        patch_file(str(file), patch)


def test_patch_file_missing_file_raises(tmp_path):
    patch = "\n".join(["@@ -1,1 +1,1 @@", "-a", "+b"])

    with pytest.raises(FileNotFoundError):
        patch_file(str(tmp_path / "missing.py"), patch)


# ---------------------------------------------------------------------
# Preview mode
# ---------------------------------------------------------------------


def test_create_file_preview_mode_stages_instead_of_writing(tmp_path):
    file = tmp_path / "new.py"
    manager = ChangeManager()
    set_active_patch_manager(manager)

    result = create_file(str(file), "print('hi')\n")

    assert not file.exists()
    assert manager.has_pending()
    assert manager.pending[0].path == str(file)
    assert manager.pending[0].original_content is None
    assert manager.pending[0].updated_content == "print('hi')\n"
    assert isinstance(result, str)
    assert "Preview" in result


def test_create_file_preview_mode_still_rejects_existing_file(tmp_path):
    file = tmp_path / "existing.txt"
    file.write_text("original")
    manager = ChangeManager()
    set_active_patch_manager(manager)

    with pytest.raises(FileExistsError):
        create_file(str(file), "overwritten")

    assert not manager.has_pending()
    assert file.read_text() == "original"


def test_replace_in_file_preview_mode_stages_instead_of_writing(tmp_path):
    file = tmp_path / "code.py"
    _write_lf(file, "foo = 1\n")
    manager = ChangeManager()
    set_active_patch_manager(manager)

    replaced = replace_in_file(str(file), "foo", "bar")

    assert replaced == 1
    assert file.read_text() == "foo = 1\n"
    assert manager.pending[0].updated_content == "bar = 1\n"
    assert manager.pending[0].original_content == "foo = 1\n"


def test_edit_lines_preview_mode_stages_instead_of_writing(tmp_path):
    file = tmp_path / "code.py"
    _write_lf(file, "line1\nline2\nline3\n")
    manager = ChangeManager()
    set_active_patch_manager(manager)

    result = edit_lines(str(file), 2, 2, "changed")

    assert file.read_text() == "line1\nline2\nline3\n"
    assert manager.pending[0].updated_content == "line1\nchanged\nline3\n"
    assert isinstance(result, str)


def test_patch_file_preview_mode_stages_instead_of_writing(tmp_path):
    file = tmp_path / "code.py"
    _write_lf(file, "line1\nline2\n")
    manager = ChangeManager()
    set_active_patch_manager(manager)

    patch = "\n".join(["@@ -1,2 +1,2 @@", " line1", "-line2", "+changed"])
    result = patch_file(str(file), patch)

    assert file.read_text() == "line1\nline2\n"
    assert manager.pending[0].updated_content == "line1\nchanged\n"
    assert isinstance(result, str)


def test_patch_file_preview_mode_still_validates_the_patch(tmp_path):
    file = tmp_path / "code.py"
    file.write_text("line1\nline2\n")
    manager = ChangeManager()
    set_active_patch_manager(manager)

    patch = "\n".join(["@@ -1,1 +1,1 @@", "-does not match", "+x"])

    with pytest.raises(ValueError):
        patch_file(str(file), patch)

    assert not manager.has_pending()


def test_multiple_edit_tools_stage_into_the_same_batch(tmp_path):
    existing = tmp_path / "existing.py"
    existing.write_text("old\n")
    manager = ChangeManager()
    set_active_patch_manager(manager)

    create_file(str(tmp_path / "new.py"), "print(1)\n")
    replace_in_file(str(existing), "old", "new")

    assert len(manager.pending) == 2
    assert manager.affected_files() == [
        str(tmp_path / "new.py"),
        str(existing),
    ]


def test_deactivating_preview_mode_restores_direct_writes(tmp_path):
    file = tmp_path / "new.py"
    manager = ChangeManager()
    set_active_patch_manager(manager)
    set_active_patch_manager(None)

    create_file(str(file), "print('hi')\n")

    assert file.read_text() == "print('hi')\n"
    assert not manager.has_pending()


# ---------------------------------------------------------------------
# Direct writes refresh the repository index (no stale find_symbol
# results after create_file/edit_lines/replace_in_file/patch_file).
# ---------------------------------------------------------------------


def test_create_file_refreshes_repository_index(tmp_path):
    from src.tools.repo_tools import find_symbol

    # Prime the cache for this root before the file exists.
    find_symbol("brand_new")

    create_file(str(tmp_path / "new.py"), "def brand_new():\n    return 1\n")

    assert find_symbol("brand_new") == [
        {"file": "new.py", "line": 1, "type": "function"}
    ]


def test_edit_lines_refreshes_repository_index(tmp_path):
    from src.tools.repo_tools import find_symbol

    file = tmp_path / "module.py"
    file.write_text("def old_name():\n    return 1\n")
    find_symbol("old_name")

    edit_lines(str(file), 1, 1, "def new_name():")

    assert find_symbol("new_name") == [
        {"file": "module.py", "line": 1, "type": "function"}
    ]
    assert find_symbol("old_name") == []


class TestPatchManagerIsEmpty:
    def test_is_empty_true_when_no_edits_staged(self):
        pm = ChangeManager()
        assert pm.is_empty is True

    def test_is_empty_false_after_staging_an_edit(self, tmp_path):
        pm = ChangeManager()
        pm.propose(str(tmp_path / "f.py"), None, "content")
        assert pm.is_empty is False

    def test_is_empty_true_after_discard(self, tmp_path):
        pm = ChangeManager()
        pm.propose(str(tmp_path / "f.py"), None, "content")
        pm.discard_all()
        assert pm.is_empty is True

    def test_is_empty_true_after_apply(self, tmp_path):
        pm = ChangeManager()
        pm.propose(str(tmp_path / "f.py"), None, "content")
        pm.apply_all()
        assert pm.is_empty is True

    def test_is_empty_complements_has_pending(self, tmp_path):
        pm = ChangeManager()
        assert pm.is_empty is not pm.has_pending()
        pm.propose(str(tmp_path / "f.py"), None, "content")
        assert pm.is_empty is not pm.has_pending()

    def test_len_zero_does_not_make_patch_manager_falsy(self):
        # __len__ must not affect truthiness: an empty ChangeManager is still
        # a valid object and must not be treated as falsy by callers using
        # `pm or fallback` patterns (regression guard for executor __init__).
        pm = ChangeManager()
        assert len(pm) == 0
        assert bool(pm) is True  # must remain truthy despite __len__ == 0
