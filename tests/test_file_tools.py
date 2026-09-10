import shutil
import sys

import pytest

from src.tools.file_tools import (
    append_file,
    copy_file,
    delete_file,
    diff_files,
    file_exists,
    file_size,
    list_directory,
    make_directory,
    read_file,
    rename_file,
    write_file,
)


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch):
    """
    Run every test with tmp_path as the workspace root, since
    write/append/delete/make_directory now reject paths outside cwd.
    """

    monkeypatch.chdir(tmp_path)


def test_write_and_read(tmp_path):
    file = tmp_path / "sample.txt"

    write_file(str(file), "hello")

    assert read_file(str(file)) == "hello"


def test_append(tmp_path):
    file = tmp_path / "append.txt"

    write_file(str(file), "Hello")

    append_file(str(file), " World")

    assert read_file(str(file)) == "Hello World"


def test_exists(tmp_path):
    file = tmp_path / "exists.txt"

    write_file(str(file), "test")

    assert file_exists(str(file))


def test_directory_listing(tmp_path):
    write_file(str(tmp_path / "a.txt"), "a")
    write_file(str(tmp_path / "b.txt"), "b")

    files = list_directory(str(tmp_path))

    assert "a.txt" in files
    assert "b.txt" in files


def test_make_directory(tmp_path):
    directory = tmp_path / "new"

    make_directory(str(directory))

    assert directory.exists()


def test_delete_file(tmp_path):
    file = tmp_path / "delete.txt"

    write_file(str(file), "abc")

    delete_file(str(file))

    assert not file.exists()


def test_file_size(tmp_path):
    file = tmp_path / "size.txt"

    write_file(str(file), "hello")

    assert file_size(str(file)) == 5


def test_write_file_rejects_path_traversal(tmp_path):
    outside = tmp_path.parent / "escape.txt"

    with pytest.raises(PermissionError):
        write_file("../escape.txt", "pwned")

    assert not outside.exists()


def test_ensure_within_workspace_fails_closed_on_null_byte(tmp_path):
    # A null-byte path can't resolve cleanly; must raise PermissionError, not
    # propagate an unexpected ValueError (fail-closed, not fail-open).
    from src.config.workspace import clear_workspace_root, set_workspace_root
    from src.tools.file_tools import _ensure_within_workspace

    set_workspace_root(tmp_path)
    try:
        with pytest.raises(PermissionError):
            _ensure_within_workspace("foo\x00bar")
    finally:
        clear_workspace_root()


def test_prefix_collision_blocked(tmp_path):
    # /workspace_evil should NOT pass a startswith("/workspace") check.
    # is_relative_to() uses path segments so /workspace_evil is blocked.
    sibling = tmp_path.parent / (tmp_path.name + "_evil")
    sibling.mkdir(exist_ok=True)
    evil = sibling / "secret.txt"
    evil.write_text("top secret")
    from src.config.workspace import clear_workspace_root, set_workspace_root
    from src.tools.file_tools import _ensure_within_workspace

    set_workspace_root(tmp_path)
    try:
        with pytest.raises(PermissionError):
            _ensure_within_workspace(str(evil))
    finally:
        evil.unlink(missing_ok=True)
        sibling.rmdir()
        clear_workspace_root()


def test_delete_file_rejects_path_outside_workspace(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("data")

    try:
        with pytest.raises(PermissionError):
            delete_file(str(outside))

        assert outside.exists()
    finally:
        outside.unlink()


@pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks require elevated privileges on Windows"
)
def test_write_file_rejects_symlink_escape(tmp_path):
    outside_dir = tmp_path.parent / f"{tmp_path.name}_outside"
    outside_dir.mkdir()

    try:
        link = tmp_path / "link"
        link.symlink_to(outside_dir)

        with pytest.raises(PermissionError):
            write_file(str(link / "evil.txt"), "pwned")

        assert not (outside_dir / "evil.txt").exists()
    finally:
        shutil.rmtree(outside_dir)


# ---------------------------------------------------------------------
# Read-side workspace confinement: read_file/file_exists/file_size/
# list_directory must reject paths outside the workspace exactly like
# the write-side tools already did, instead of allowing arbitrary
# filesystem reads.
# ---------------------------------------------------------------------


def test_read_file_rejects_path_outside_workspace(tmp_path):
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("top secret")

    try:
        with pytest.raises(PermissionError):
            read_file(str(outside))
    finally:
        outside.unlink()


def test_read_file_rejects_path_traversal(tmp_path):
    with pytest.raises(PermissionError):
        read_file("../escape.txt")


def test_file_exists_returns_false_for_path_outside_workspace(tmp_path):
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("top secret")

    try:
        assert file_exists(str(outside)) is False
    finally:
        outside.unlink()


def test_file_exists_still_works_for_missing_file_inside_workspace(tmp_path):
    assert file_exists(str(tmp_path / "does-not-exist.txt")) is False


def test_file_size_rejects_path_outside_workspace(tmp_path):
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("top secret")

    try:
        with pytest.raises(PermissionError):
            file_size(str(outside))
    finally:
        outside.unlink()


def test_list_directory_rejects_path_outside_workspace(tmp_path):
    with pytest.raises(PermissionError):
        list_directory(str(tmp_path.parent))


def test_read_file_still_works_inside_workspace(tmp_path):
    file = tmp_path / "inside.txt"
    write_file(str(file), "hello")

    assert read_file(str(file)) == "hello"


# ---------------------------------------------------------------------
# rename_file (Task 26)
# ---------------------------------------------------------------------


class TestRenameFile:
    def test_renames_file_within_workspace(self, tmp_path):
        src = tmp_path / "old.txt"
        dst = tmp_path / "new.txt"
        src.write_text("content")

        rename_file(str(src), str(dst))

        assert not src.exists()
        assert dst.read_text() == "content"

    def test_rename_creates_destination_parent_dirs(self, tmp_path):
        src = tmp_path / "file.txt"
        dst = tmp_path / "nested" / "dir" / "file.txt"
        src.write_text("x")

        rename_file(str(src), str(dst))

        assert dst.exists()
        assert not src.exists()

    def test_rename_raises_if_source_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            rename_file(str(tmp_path / "missing.txt"), str(tmp_path / "dst.txt"))

    def test_rename_raises_if_destination_exists(self, tmp_path):
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("a")
        dst.write_text("b")

        with pytest.raises(FileExistsError):
            rename_file(str(src), str(dst))

        assert src.exists()  # source untouched on failure

    def test_rename_rejects_source_outside_workspace(self, tmp_path):
        outside = tmp_path.parent / "outside.txt"
        outside.write_text("x")
        try:
            with pytest.raises(PermissionError):
                rename_file(str(outside), str(tmp_path / "dst.txt"))
        finally:
            outside.unlink(missing_ok=True)

    def test_rename_rejects_destination_outside_workspace(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("x")
        outside = tmp_path.parent / "escape.txt"

        with pytest.raises(PermissionError):
            rename_file(str(src), str(outside))

        assert src.exists()  # source not moved


# ---------------------------------------------------------------------
# copy_file (Task 27)
# ---------------------------------------------------------------------


class TestCopyFile:
    def test_copies_file_within_workspace(self, tmp_path):
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("hello")

        copy_file(str(src), str(dst))

        assert src.exists()  # original preserved
        assert dst.read_text() == "hello"

    def test_copy_creates_destination_parent_dirs(self, tmp_path):
        src = tmp_path / "file.txt"
        dst = tmp_path / "nested" / "copy.txt"
        src.write_text("data")

        copy_file(str(src), str(dst))

        assert dst.exists()
        assert src.exists()

    def test_copy_raises_if_source_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            copy_file(str(tmp_path / "missing.txt"), str(tmp_path / "dst.txt"))

    def test_copy_raises_if_destination_exists(self, tmp_path):
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("a")
        dst.write_text("b")

        with pytest.raises(FileExistsError):
            copy_file(str(src), str(dst))

        assert dst.read_text() == "b"  # destination untouched

    def test_copy_preserves_binary_content(self, tmp_path):
        src = tmp_path / "data.bin"
        dst = tmp_path / "data_copy.bin"
        src.write_bytes(bytes(range(256)))

        copy_file(str(src), str(dst))

        assert dst.read_bytes() == bytes(range(256))

    def test_copy_rejects_source_outside_workspace(self, tmp_path):
        outside = tmp_path.parent / "outside.txt"
        outside.write_text("x")
        try:
            with pytest.raises(PermissionError):
                copy_file(str(outside), str(tmp_path / "dst.txt"))
        finally:
            outside.unlink(missing_ok=True)

    def test_copy_rejects_destination_outside_workspace(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("x")
        outside = tmp_path.parent / "escape.txt"

        with pytest.raises(PermissionError):
            copy_file(str(src), str(outside))


# ---------------------------------------------------------------------
# diff_files (Task 34)
# ---------------------------------------------------------------------


class TestDiffFiles:
    def test_identical_files_return_empty_string(self, tmp_path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("hello\nworld\n")
        b.write_text("hello\nworld\n")

        assert diff_files(str(a), str(b)) == ""

    def test_diff_shows_changed_line(self, tmp_path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("line1\nline2\nline3\n")
        b.write_text("line1\nchanged\nline3\n")

        result = diff_files(str(a), str(b))

        assert "-line2" in result
        assert "+changed" in result

    def test_diff_has_unified_format_header(self, tmp_path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("x\n")
        b.write_text("y\n")

        result = diff_files(str(a), str(b))

        assert result.startswith("---")
        assert "+++" in result

    def test_raises_if_first_file_missing(self, tmp_path):
        b = tmp_path / "b.txt"
        b.write_text("x")

        with pytest.raises(FileNotFoundError):
            diff_files(str(tmp_path / "missing.txt"), str(b))

    def test_raises_if_second_file_missing(self, tmp_path):
        a = tmp_path / "a.txt"
        a.write_text("x")

        with pytest.raises(FileNotFoundError):
            diff_files(str(a), str(tmp_path / "missing.txt"))

    def test_rejects_first_path_outside_workspace(self, tmp_path):
        outside = tmp_path.parent / "outside.txt"
        outside.write_text("x")
        b = tmp_path / "b.txt"
        b.write_text("y")
        try:
            with pytest.raises(PermissionError):
                diff_files(str(outside), str(b))
        finally:
            outside.unlink(missing_ok=True)

    def test_rejects_second_path_outside_workspace(self, tmp_path):
        a = tmp_path / "a.txt"
        a.write_text("x")
        outside = tmp_path.parent / "outside.txt"
        outside.write_text("y")
        try:
            with pytest.raises(PermissionError):
                diff_files(str(a), str(outside))
        finally:
            outside.unlink(missing_ok=True)


# ---------------------------------------------------------------------
# Approval gate — write_file / append_file must stage, not write, when
# a ChangeManager is active (autonomous mode).
# ---------------------------------------------------------------------


@pytest.fixture
def _staging():
    """
    Activate preview mode for one test and guarantee it is off again
    afterwards, so a failure can't leak staging into unrelated tests.
    """

    from src.tools.edit_tools import (
        get_active_patch_manager,
        set_active_patch_manager,
    )
    from src.tools.patch_manager import ChangeManager

    assert get_active_patch_manager() is None
    manager = ChangeManager()
    set_active_patch_manager(manager)
    try:
        yield manager
    finally:
        set_active_patch_manager(None)


def test_write_file_stages_new_file(tmp_path, _staging):
    file = tmp_path / "staged.txt"

    result = write_file(str(file), "content\n")

    assert not file.exists()
    assert _staging.pending[0].path == str(file)
    assert _staging.pending[0].original_content is None
    assert _staging.pending[0].updated_content == "content\n"
    assert "Preview" in result


def test_write_file_stages_overwrite_with_original(tmp_path, _staging):
    file = tmp_path / "existing.txt"
    file.write_text("before\n", encoding="utf-8")

    write_file(str(file), "after\n")

    assert file.read_text(encoding="utf-8") == "before\n"
    assert _staging.pending[0].original_content == "before\n"
    assert _staging.pending[0].updated_content == "after\n"


def test_append_file_stages_onto_existing_content(tmp_path, _staging):
    file = tmp_path / "log.txt"
    file.write_text("first\n", encoding="utf-8")

    write_result = append_file(str(file), "second\n")

    assert file.read_text(encoding="utf-8") == "first\n"
    assert _staging.pending[0].original_content == "first\n"
    assert _staging.pending[0].updated_content == "first\nsecond\n"
    assert "Preview" in write_result


def test_append_file_stages_new_file(tmp_path, _staging):
    file = tmp_path / "fresh.txt"

    append_file(str(file), "line\n")

    assert not file.exists()
    assert _staging.pending[0].original_content is None
    assert _staging.pending[0].updated_content == "line\n"


def test_write_file_writes_directly_without_manager(tmp_path):
    """Direct/CLI use is unchanged: no manager active means write now."""
    file = tmp_path / "direct.txt"

    assert write_file(str(file), "x\n") is None
    assert file.read_text(encoding="utf-8") == "x\n"


def test_delete_file_stages_instead_of_removing(tmp_path, _staging):
    victim = tmp_path / "victim.txt"
    victim.write_text("precious\n", encoding="utf-8")

    result = delete_file(str(victim))

    assert victim.exists()
    assert _staging.pending[0].is_deletion
    assert _staging.pending[0].original_content == "precious\n"
    assert "Preview" in result


def test_delete_file_staged_diff_shows_the_removed_content(tmp_path, _staging):
    victim = tmp_path / "victim.txt"
    victim.write_text("precious\n", encoding="utf-8")

    delete_file(str(victim))

    assert "-precious" in _staging.pending[0].diff


def test_delete_file_missing_still_raises_while_staging(tmp_path, _staging):
    """A delete of a file that isn't there is an error, not a staged no-op."""
    with pytest.raises(FileNotFoundError):
        delete_file(str(tmp_path / "absent.txt"))

    assert not _staging.has_pending()


def test_delete_file_removes_directly_without_manager(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("x\n", encoding="utf-8")

    assert delete_file(str(victim)) is None
    assert not victim.exists()
