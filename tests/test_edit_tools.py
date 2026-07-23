import pytest

from src.tools.edit_tools import (
    create_file,
    edit_lines,
    patch_file,
    replace_in_file,
)


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch):
    """
    Run every test with tmp_path as the workspace root, since these
    tools are workspace-contained like the file tools.
    """

    monkeypatch.chdir(tmp_path)


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

    assert file.read_text() == (
        "line1\nline2 modified\nline2.5\nline3\nline4\n"
    )


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
