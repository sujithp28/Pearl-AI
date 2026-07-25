"""
Tests for workspace checkpoints (`src/tools/checkpoints.py`).

These run against a real `git` binary and a real temp workspace —
the whole feature is shell-outs to git, so faking that would test
nothing. Two regressions are pinned explicitly because both were
found by running the mechanics before writing the code, and both
would have been silent corruption rather than a visible failure.
"""

from __future__ import annotations

import subprocess

import pytest

from src.tools.checkpoints import (
    PEARL_DIR,
    CheckpointError,
    CheckpointManager,
)

pytestmark = pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git binary not available",
)


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "a.txt").write_text("original\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("orig2\n")
    return tmp_path


@pytest.fixture
def manager(workspace):
    return CheckpointManager(workspace)


# ---------------------------------------------------------------------
# Creating checkpoints
# ---------------------------------------------------------------------


def test_create_returns_a_checkpoint(manager):
    checkpoint = manager.create("first")

    assert checkpoint is not None
    assert checkpoint.label == "first"
    assert len(checkpoint.id) == 40
    assert checkpoint.short_id == checkpoint.id[:8]


def test_create_initialises_the_store_on_first_use(manager, workspace):
    assert not (workspace / PEARL_DIR).exists()

    manager.create("first")

    assert (workspace / PEARL_DIR / "shadow.git" / "HEAD").exists()


def test_create_returns_none_when_nothing_changed(manager):
    manager.create("first")

    assert manager.create("second") is None


def test_create_records_a_new_checkpoint_after_a_change(manager, workspace):
    manager.create("first")
    (workspace / "a.txt").write_text("changed\n")

    assert manager.create("second") is not None
    assert len(manager.list()) == 2


def test_works_without_global_git_config(manager, monkeypatch, tmp_path):
    # A machine with no user.name/user.email set must still be able to
    # checkpoint — the identity is supplied per-commit.
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "nonexistent"))

    assert manager.create("first") is not None


# ---------------------------------------------------------------------
# The shadow store must not snapshot itself
# ---------------------------------------------------------------------


def test_checkpoints_never_include_pearls_own_directory(manager, workspace):
    """
    Regression: without an exclude, the shadow repo commits its own
    object store and `index.lock`, which grows unboundedly and then
    corrupts the index on the first restore.
    """

    manager.create("first")

    listed = manager._run("ls-tree", "-r", "--name-only", "HEAD")
    tracked = listed.stdout.splitlines()

    assert tracked, "expected the workspace files to be tracked"
    assert not any(path.startswith(PEARL_DIR) for path in tracked)


def test_repeated_checkpoints_do_not_corrupt_the_store(manager, workspace):
    # The corruption above only surfaced on the second/third cycle.
    for index in range(3):
        (workspace / "a.txt").write_text(f"v{index}\n")
        assert manager.create(f"cp{index}") is not None

    assert len(manager.list()) == 3


# ---------------------------------------------------------------------
# Restoring
# ---------------------------------------------------------------------


def test_restore_reverts_a_modified_file(manager, workspace):
    checkpoint = manager.create("before")
    (workspace / "a.txt").write_text("MODIFIED\n")

    manager.restore(checkpoint.id)

    assert (workspace / "a.txt").read_text() == "original\n"


def test_restore_brings_back_a_deleted_file(manager, workspace):
    checkpoint = manager.create("before")
    (workspace / "sub" / "b.txt").unlink()

    manager.restore(checkpoint.id)

    assert (workspace / "sub" / "b.txt").read_text() == "orig2\n"


def test_restore_removes_a_file_created_after_the_checkpoint(manager, workspace):
    """
    Regression: `git checkout <sha> -- .` restores modified and
    deleted files but leaves newly created ones in place — so "Pearl
    created a file", the most common thing to want undone, would not
    actually be undone.
    """

    checkpoint = manager.create("before")
    (workspace / "created.py").write_text("print('new')\n")

    manager.restore(checkpoint.id)

    assert not (workspace / "created.py").exists()


def test_restore_reports_exactly_what_it_changed(manager, workspace):
    checkpoint = manager.create("before")
    (workspace / "a.txt").write_text("MODIFIED\n")
    (workspace / "created.py").write_text("new\n")

    report = manager.restore(checkpoint.id)

    assert "a.txt" in report.restored
    assert "created.py" in report.removed
    assert report.changed_anything


def test_restore_to_an_older_checkpoint_works(manager, workspace):
    first = manager.create("first")
    (workspace / "a.txt").write_text("v2\n")
    manager.create("second")
    (workspace / "a.txt").write_text("v3\n")

    manager.restore(first.id)

    assert (workspace / "a.txt").read_text() == "original\n"


def test_restore_is_idempotent(manager, workspace):
    checkpoint = manager.create("before")
    (workspace / "a.txt").write_text("MODIFIED\n")

    manager.restore(checkpoint.id)
    second = manager.restore(checkpoint.id)

    assert (workspace / "a.txt").read_text() == "original\n"
    assert not second.changed_anything


# ---------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------


def test_preview_reports_without_changing_anything(manager, workspace):
    """
    Restoring deletes files created since the checkpoint, so a caller
    must be able to show the user that first — the same reason Pearl
    shows a diff before writing.
    """

    checkpoint = manager.create("before")
    (workspace / "a.txt").write_text("MODIFIED\n")
    (workspace / "created.py").write_text("new\n")

    report = manager.preview_restore(checkpoint.id)

    assert "a.txt" in report.restored
    assert "created.py" in report.removed
    # Nothing actually happened.
    assert (workspace / "a.txt").read_text() == "MODIFIED\n"
    assert (workspace / "created.py").exists()


# ---------------------------------------------------------------------
# Listing and error handling
# ---------------------------------------------------------------------


def test_list_is_empty_before_any_checkpoint(manager):
    assert manager.list() == []


def test_list_is_newest_first(manager, workspace):
    manager.create("oldest")
    (workspace / "a.txt").write_text("x\n")
    manager.create("newest")

    assert [c.label for c in manager.list()] == ["newest", "oldest"]


def test_latest_returns_the_most_recent(manager, workspace):
    manager.create("first")
    (workspace / "a.txt").write_text("x\n")
    manager.create("second")

    assert manager.latest().label == "second"


def test_latest_is_none_before_any_checkpoint(manager):
    assert manager.latest() is None


def test_restoring_an_unknown_checkpoint_fails_clearly(manager):
    manager.create("first")

    with pytest.raises(CheckpointError, match="No such checkpoint"):
        manager.restore("0" * 40)


def test_restoring_with_no_store_fails_clearly(manager):
    with pytest.raises(CheckpointError, match="No checkpoints exist"):
        manager.restore("0" * 40)


# ---------------------------------------------------------------------
# Isolation from the user's own repository
# ---------------------------------------------------------------------


def test_the_users_git_repository_is_never_touched(workspace):
    """
    The whole point of a *shadow* repo: the user's history, branches
    and staging area must be exactly as they left them.
    """

    def user_git(*args):
        return subprocess.run(
            ["git", *args], cwd=workspace, capture_output=True, text=True
        )

    user_git("init", "-q")
    user_git("-c", "user.email=u@u", "-c", "user.name=User", "add", "a.txt")
    user_git(
        "-c",
        "user.email=u@u",
        "-c",
        "user.name=User",
        "commit",
        "-q",
        "-m",
        "user's commit",
    )

    before = user_git("log", "--oneline").stdout

    manager = CheckpointManager(workspace)
    checkpoint = manager.create("pearl checkpoint")
    (workspace / "a.txt").write_text("changed\n")
    manager.restore(checkpoint.id)

    after = user_git("log", "--oneline").stdout

    assert before == after
    assert "pearl checkpoint" not in after
    # And Pearl's checkpoints are not visible as user commits.
    assert user_git("log", "--oneline").stdout.count("\n") == 1


# ---------------------------------------------------------------------
# Wired into approval: an approved change is undoable
# ---------------------------------------------------------------------


def test_approving_a_patch_creates_a_restorable_checkpoint(workspace, monkeypatch):
    """
    The end-to-end promise: after Pearl writes a file, the user can
    get back the state from before it did.
    """

    from pathlib import Path

    from src.agent.dispatcher import ToolDispatcher
    from src.agent.executor import AutonomousExecutor
    from src.agent.planner import Planner
    from src.tools.edit_tools import create_file, set_active_patch_manager
    from src.tools.registry import ToolRegistry

    monkeypatch.setattr(Path, "cwd", lambda: workspace)

    registry = ToolRegistry()
    registry.register(create_file)
    dispatcher = ToolDispatcher(registry)
    planner = Planner(registry, dispatcher)

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        lambda prompt, cancel_check=None: {
            "steps": [
                {
                    "tool": "create_file",
                    "arguments": {
                        "path": str(workspace / "written.py"),
                        "content": "print('hi')\n",
                    },
                }
            ]
        },
    )

    manager = CheckpointManager(workspace)
    executor = AutonomousExecutor(planner, dispatcher, checkpoints=manager)

    try:
        paused = executor.run("create written.py")
        assert paused.stop_reason == "awaiting_approval"
        assert not (workspace / "written.py").exists()

        executor.approve()
        assert (workspace / "written.py").exists()

        # A checkpoint of the pre-write state exists...
        checkpoint = manager.list()[-1]

        # ...and restoring it undoes Pearl's write.
        manager.restore(checkpoint.id)
        assert not (workspace / "written.py").exists()
    finally:
        set_active_patch_manager(None)


def test_approval_still_succeeds_when_checkpointing_fails(workspace, monkeypatch):
    """
    Checkpointing is a safety net. A net that can't be strung up must
    not stop the user working — losing undo is a degradation, refusing
    the write would be a regression.
    """

    from pathlib import Path

    from src.agent.dispatcher import ToolDispatcher
    from src.agent.executor import AutonomousExecutor
    from src.agent.planner import Planner
    from src.tools.edit_tools import create_file, set_active_patch_manager
    from src.tools.registry import ToolRegistry

    monkeypatch.setattr(Path, "cwd", lambda: workspace)

    registry = ToolRegistry()
    registry.register(create_file)
    dispatcher = ToolDispatcher(registry)
    planner = Planner(registry, dispatcher)

    monkeypatch.setattr(
        planner.client,
        "generate_json",
        lambda prompt, cancel_check=None: {
            "steps": [
                {
                    "tool": "create_file",
                    "arguments": {
                        "path": str(workspace / "written.py"),
                        "content": "x\n",
                    },
                }
            ]
        },
    )

    class BrokenCheckpoints(CheckpointManager):
        def create(self, label):
            raise CheckpointError("git exploded")

    executor = AutonomousExecutor(
        planner, dispatcher, checkpoints=BrokenCheckpoints(workspace)
    )

    try:
        executor.run("create written.py")
        report = executor.approve()

        assert report.stop_reason == "completed"
        assert (workspace / "written.py").exists()
    finally:
        set_active_patch_manager(None)
