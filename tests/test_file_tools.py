from pathlib import Path

from src.tools.file_tools import (
    append_file,
    delete_file,
    file_exists,
    file_size,
    list_directory,
    make_directory,
    read_file,
    write_file,
)


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