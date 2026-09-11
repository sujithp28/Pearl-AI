"""
Editing a few lines must not rewrite every line ending in the file.

Python's text mode translates newlines in both directions. Reading a
CRLF file gives LF, and writing LF gives os.linesep — so on Windows a
one-line edit to an LF file used to come back as a whole-file CRLF
diff, and on POSIX the same edit to a CRLF file flattened it to LF.
Either way version control shows every line as changed and the real
edit is invisible inside it.

Every direct write path now goes through src/tools/file_io, which
disables both translations.
"""

from __future__ import annotations

import pytest

from src.config.workspace import set_workspace_root
from src.tools.edit_tools import (
    create_file,
    edit_lines,
    patch_file,
    replace_in_file,
    set_active_patch_manager,
)
from src.tools.file_io import dominant_newline, read_text, write_text
from src.tools.file_tools import append_file, write_file
from src.tools.patch_manager import ChangeManager


@pytest.fixture(autouse=True)
def _workspace(tmp_path):
    set_workspace_root(tmp_path)
    yield
    set_active_patch_manager(None)


def _raw(path) -> bytes:
    return path.read_bytes()


# ---------------------------------------------------------------------
# file_io itself
# ---------------------------------------------------------------------


def test_round_trip_preserves_crlf(tmp_path):
    target = tmp_path / "a.txt"
    target.write_bytes(b"one\r\ntwo\r\n")

    write_text(target, read_text(target))

    assert _raw(target) == b"one\r\ntwo\r\n"


def test_round_trip_preserves_lf(tmp_path):
    target = tmp_path / "a.txt"
    target.write_bytes(b"one\ntwo\n")

    write_text(target, read_text(target))

    assert _raw(target) == b"one\ntwo\n"


def test_dominant_newline_picks_the_majority():
    assert dominant_newline("a\r\nb\r\n") == "\r\n"
    assert dominant_newline("a\nb\n") == "\n"
    assert dominant_newline("no endings at all") == "\n"


# ---------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------


def test_write_file_writes_lf_verbatim(tmp_path):
    target = tmp_path / "a.txt"
    write_file(str(target), "one\ntwo\n")
    assert _raw(target) == b"one\ntwo\n"


def test_write_file_writes_crlf_verbatim(tmp_path):
    target = tmp_path / "a.txt"
    write_file(str(target), "one\r\ntwo\r\n")
    assert _raw(target) == b"one\r\ntwo\r\n"


def test_append_file_does_not_convert_existing_endings(tmp_path):
    target = tmp_path / "a.txt"
    target.write_bytes(b"one\r\n")

    append_file(str(target), "two\r\n")

    assert _raw(target) == b"one\r\ntwo\r\n"


def test_create_file_writes_lf_verbatim(tmp_path):
    target = tmp_path / "a.txt"
    create_file(str(target), "one\ntwo\n")
    assert _raw(target) == b"one\ntwo\n"


# ---------------------------------------------------------------------
# Line-based edits rebuild the file, so they must carry the ending
# ---------------------------------------------------------------------


def test_edit_lines_keeps_crlf_on_untouched_lines(tmp_path):
    target = tmp_path / "a.txt"
    target.write_bytes(b"one\r\ntwo\r\nthree\r\n")

    edit_lines(str(target), 2, 2, "TWO")

    assert _raw(target) == b"one\r\nTWO\r\nthree\r\n"


def test_edit_lines_keeps_lf_on_untouched_lines(tmp_path):
    target = tmp_path / "a.txt"
    target.write_bytes(b"one\ntwo\nthree\n")

    edit_lines(str(target), 2, 2, "TWO")

    assert _raw(target) == b"one\nTWO\nthree\n"


def test_patch_file_keeps_crlf(tmp_path):
    target = tmp_path / "a.txt"
    target.write_bytes(b"line1\r\nline2\r\n")

    patch_file(
        str(target), "\n".join(["@@ -1,2 +1,2 @@", " line1", "-line2", "+changed"])
    )

    assert _raw(target) == b"line1\r\nchanged\r\n"


def test_replace_in_file_keeps_crlf_elsewhere(tmp_path):
    target = tmp_path / "a.txt"
    target.write_bytes(b"alpha\r\nbeta\r\ngamma\r\n")

    replace_in_file(str(target), "beta", "BETA")

    assert _raw(target) == b"alpha\r\nBETA\r\ngamma\r\n"


# ---------------------------------------------------------------------
# The staged path must match the direct path exactly
# ---------------------------------------------------------------------


def test_approved_edit_writes_the_same_bytes_as_a_direct_edit(tmp_path):
    target = tmp_path / "a.txt"
    target.write_bytes(b"one\r\ntwo\r\nthree\r\n")

    manager = ChangeManager()
    set_active_patch_manager(manager)
    edit_lines(str(target), 2, 2, "TWO")
    set_active_patch_manager(None)

    assert _raw(target) == b"one\r\ntwo\r\nthree\r\n"  # staged, not written

    manager.apply_all()

    assert _raw(target) == b"one\r\nTWO\r\nthree\r\n"


def test_staging_a_crlf_file_is_not_mistaken_for_a_conflict(tmp_path):
    """
    The approval gate compares staged content against the file's real
    bytes. If staging read through newline translation and the gate read
    raw, every CRLF file in the workspace would look like it had been
    modified underneath and no approval would ever apply.
    """
    target = tmp_path / "a.txt"
    target.write_bytes(b"one\r\ntwo\r\n")

    manager = ChangeManager()
    set_active_patch_manager(manager)
    write_file(str(target), "one\r\nTWO\r\n")
    set_active_patch_manager(None)

    assert manager.stale_paths() == []
    manager.apply_all()
    assert _raw(target) == b"one\r\nTWO\r\n"
