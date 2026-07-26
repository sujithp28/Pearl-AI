import shutil

import pytest

from src.tools.file_tools import (
    append_file,
    copy_file,
    delete_file,
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


def test_delete_file_rejects_path_outside_workspace(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("data")

    try:
        with pytest.raises(PermissionError):
            delete_file(str(outside))

        assert outside.exists()
    finally:
        outside.unlink()


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
