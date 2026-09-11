"""
Time-of-check/time-of-use regression tests for the approval gate.

Pearl stages an edit, shows the user a diff against the file as it was
at that moment, and writes only after they approve. Between those two
points the file can change — the user saves in their editor, a build
step rewrites it, `git checkout` swaps the branch. Writing the staged
content anyway would silently discard whatever landed in between.

These tests pin the guarantee: if a target file moved after staging,
the apply is refused before anything is written, and the workspace is
left exactly as it was.
"""

from __future__ import annotations

import pytest

from src.tools.edit_tools import set_active_patch_manager
from src.tools.file_tools import write_file
from src.tools.patch_manager import ChangeManager, StaleFileError
from tests.conftest import write_lf


@pytest.fixture
def staging(tmp_path, monkeypatch):
    from src.config.workspace import set_workspace_root

    set_workspace_root(tmp_path)
    manager = ChangeManager()
    set_active_patch_manager(manager)
    try:
        yield manager
    finally:
        set_active_patch_manager(None)


# ---------------------------------------------------------------------
# stale_paths
# ---------------------------------------------------------------------


def test_unmodified_file_is_not_stale(tmp_path):
    target = tmp_path / "a.py"
    write_lf(target, "one\n")

    manager = ChangeManager()
    manager.propose(str(target), "one\n", "two\n")

    assert manager.stale_paths() == []


def test_file_edited_after_staging_is_stale(tmp_path):
    target = tmp_path / "a.py"
    write_lf(target, "one\n")

    manager = ChangeManager()
    manager.propose(str(target), "one\n", "two\n")

    write_lf(target, "the user typed this\n")

    assert manager.stale_paths() == [str(target)]


def test_file_deleted_after_staging_is_stale(tmp_path):
    target = tmp_path / "a.py"
    write_lf(target, "one\n")

    manager = ChangeManager()
    manager.propose(str(target), "one\n", "two\n")

    target.unlink()

    assert manager.stale_paths() == [str(target)]


def test_new_file_created_by_someone_else_after_staging_is_stale(tmp_path):
    target = tmp_path / "new.py"

    manager = ChangeManager()
    manager.propose(str(target), None, "pearl wrote this\n")

    write_lf(target, "the user got there first\n")

    assert manager.stale_paths() == [str(target)]


def test_two_edits_to_one_file_in_a_batch_are_not_a_conflict(tmp_path):
    """
    A batch that edits the same file twice builds the second edit on the
    first one's output, not on disk. Comparing both against disk would
    report a conflict that does not exist.
    """
    target = tmp_path / "a.py"
    write_lf(target, "one\n")

    manager = ChangeManager()
    manager.propose(str(target), "one\n", "two\n")
    manager.propose(str(target), "two\n", "three\n")

    assert manager.stale_paths() == []

    manager.apply_all()

    assert target.read_text(encoding="utf-8") == "three\n"


# ---------------------------------------------------------------------
# apply_all refuses
# ---------------------------------------------------------------------


def test_apply_all_refuses_to_overwrite_a_concurrently_modified_file(tmp_path):
    target = tmp_path / "a.py"
    write_lf(target, "original\n")

    manager = ChangeManager()
    manager.propose(str(target), "original\n", "pearl's version\n")

    write_lf(target, "the user's newer version\n")

    with pytest.raises(StaleFileError) as exc:
        manager.apply_all()

    assert str(target) in exc.value.paths
    # The user's content survives untouched.
    assert target.read_text(encoding="utf-8") == "the user's newer version\n"
    # Nothing was applied, so the batch is still there to inspect.
    assert manager.has_pending()


def test_a_stale_file_blocks_the_whole_batch_before_any_write(tmp_path):
    """
    Approval is all-or-nothing. One conflicted file must not leave the
    other files in the batch half-applied.
    """
    conflicted = tmp_path / "conflicted.py"
    clean = tmp_path / "clean.py"
    write_lf(conflicted, "original\n")
    write_lf(clean, "untouched\n")

    manager = ChangeManager()
    manager.propose(str(clean), "untouched\n", "edited\n")
    manager.propose(str(conflicted), "original\n", "edited\n")

    write_lf(conflicted, "changed underneath\n")

    with pytest.raises(StaleFileError):
        manager.apply_all()

    assert clean.read_text(encoding="utf-8") == "untouched\n"
    assert conflicted.read_text(encoding="utf-8") == "changed underneath\n"


def test_apply_succeeds_when_nothing_changed_underneath(tmp_path):
    target = tmp_path / "a.py"
    write_lf(target, "original\n")

    manager = ChangeManager()
    manager.propose(str(target), "original\n", "updated\n")

    assert manager.apply_all() == [str(target)]
    assert target.read_text(encoding="utf-8") == "updated\n"


def test_staged_deletion_of_a_modified_file_is_refused(tmp_path):
    victim = tmp_path / "victim.py"
    write_lf(victim, "delete me\n")

    manager = ChangeManager()
    manager.propose_deletion(str(victim), "delete me\n")

    write_lf(victim, "actually I need this now\n")

    with pytest.raises(StaleFileError):
        manager.apply_all()

    assert victim.exists()
    assert victim.read_text(encoding="utf-8") == "actually I need this now\n"


# ---------------------------------------------------------------------
# Through the real tool path
# ---------------------------------------------------------------------


def test_write_file_staged_then_user_edits_then_apply_is_refused(tmp_path, staging):
    target = tmp_path / "module.py"
    write_lf(target, "value = 1\n")

    write_file(str(target), "value = 2\n")

    assert not staging.is_empty
    assert target.read_text(encoding="utf-8") == "value = 1\n"

    # The user saves their own change while the diff is on screen.
    write_lf(target, "value = 99  # mine\n")

    with pytest.raises(StaleFileError):
        staging.apply_all()

    assert target.read_text(encoding="utf-8") == "value = 99  # mine\n"


def test_concurrent_modification_from_another_thread_is_caught(tmp_path):
    """
    The realistic shape of the race: a second thread writes the file
    while the approval is in flight.
    """
    import threading

    target = tmp_path / "a.py"
    write_lf(target, "original\n")

    manager = ChangeManager()
    manager.propose(str(target), "original\n", "pearl's version\n")

    done = threading.Event()

    def _other_writer() -> None:
        write_lf(target, "another thread's version\n")
        done.set()

    thread = threading.Thread(target=_other_writer)
    thread.start()
    thread.join()
    assert done.is_set()

    with pytest.raises(StaleFileError):
        manager.apply_all()

    assert target.read_text(encoding="utf-8") == "another thread's version\n"


# ---------------------------------------------------------------------
# The staleness check must not fire on changes that never happened
# ---------------------------------------------------------------------


def test_a_brand_new_file_is_not_reported_as_a_conflict(tmp_path, staging):
    """
    A tool staging a file that does not exist yet must record its
    baseline as "absent", not as an empty file. Claiming an empty file
    was there makes every newly created file look like it changed
    underneath, and refuses an apply that was never in conflict.
    """
    from src.tools.refactor_tools import batch_write_files

    target = tmp_path / "brand_new.py"
    batch_write_files({str(target): "x = 1\n"})

    assert staging.pending[0].original_content is None
    assert staging.stale_paths() == []

    staging.apply_all()

    assert target.read_text(encoding="utf-8") == "x = 1\n"


def test_a_file_with_mixed_line_endings_is_not_a_phantom_conflict(tmp_path, staging):
    """
    Line-based edits rebuild the file from splitlines() output. If the
    staged "original" is that reconstruction rather than the real text,
    a file with mixed endings never matches what the gate re-reads, and
    a correct edit is refused.
    """
    from src.tools.edit_tools import edit_lines
    from src.tools.file_io import read_text

    target = tmp_path / "mixed.txt"
    target.write_bytes(b"one\r\ntwo\nthree\r\n")

    edit_lines(str(target), 2, 2, "TWO")

    assert staging.pending[0].original_content == read_text(target)
    assert staging.stale_paths() == []

    staging.apply_all()

    assert target.read_bytes() == b"one\r\nTWO\r\nthree\r\n"


def test_every_write_tool_stages_a_baseline_the_gate_accepts(tmp_path, staging):
    """
    The category test. Each staging tool must record a baseline that
    matches what ChangeManager re-reads — otherwise that tool's edits
    can never be approved. Checked across the tools rather than one at
    a time, so a new one is covered when it is written.

    One file per tool: in preview mode every tool reads its baseline
    from disk, so two tools staging edits to the same file would each
    record the pre-run content and the second would silently drop the
    first. The plan validator already refuses two writes to one path in
    a single plan, which is where that case is handled.
    """
    from src.tools.edit_tools import create_file, edit_lines, patch_file
    from src.tools.file_tools import append_file, delete_file, write_file

    for name in ("appended.txt", "lined.txt", "patched.txt", "doomed.txt"):
        write_lf(tmp_path / name, "line1\nline2\n")

    write_file(str(tmp_path / "written.txt"), "a\n")
    create_file(str(tmp_path / "created.txt"), "b\n")
    append_file(str(tmp_path / "appended.txt"), "line3\n")
    edit_lines(str(tmp_path / "lined.txt"), 1, 1, "LINE1")
    patch_file(
        str(tmp_path / "patched.txt"),
        "\n".join(["@@ -2,1 +2,1 @@", "-line2", "+LINE2"]),
    )
    delete_file(str(tmp_path / "doomed.txt"))

    assert len(staging.pending) == 6
    assert staging.stale_paths() == [], (
        "a staging tool recorded a baseline the approval gate rejects"
    )

    staging.apply_all()

    assert (tmp_path / "written.txt").read_text(encoding="utf-8") == "a\n"
    assert (tmp_path / "created.txt").read_text(encoding="utf-8") == "b\n"
    assert (tmp_path / "appended.txt").read_text(encoding="utf-8").endswith("line3\n")
    assert (tmp_path / "lined.txt").read_text(encoding="utf-8").startswith("LINE1")
    assert "LINE2" in (tmp_path / "patched.txt").read_text(encoding="utf-8")
    assert not (tmp_path / "doomed.txt").exists()


def test_two_tools_editing_one_file_in_a_run_is_caught_not_silently_lost(
    tmp_path, staging
):
    """
    Each tool takes its baseline from disk, so a second tool staging an
    edit to the same file records the pre-run content. Applying both in
    order would write the first edit and then overwrite it with content
    that never contained it — the first edit silently lost.

    The plan validator refuses this shape upstream. The approval gate is
    the backstop: it reports a conflict rather than dropping a change.
    """
    from src.tools.edit_tools import edit_lines
    from src.tools.file_tools import append_file

    target = tmp_path / "a.txt"
    write_lf(target, "line1\nline2\n")

    append_file(str(target), "line3\n")
    edit_lines(str(target), 1, 1, "LINE1")

    assert staging.stale_paths() == [str(target)]

    with pytest.raises(StaleFileError):
        staging.apply_all()

    # The file is untouched — no half-applied batch.
    assert target.read_text(encoding="utf-8") == "line1\nline2\n"
