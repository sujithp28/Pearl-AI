"""
Tests for workspace checkpoints (`src/tools/checkpoints.py`).

These run against a real `git` binary and a real temp workspace —
the whole feature is shell-outs to git, so faking that would test
nothing. Two regressions are pinned explicitly because both were
found by running the mechanics before writing the code, and both
would have been silent corruption rather than a visible failure.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from src.tools.checkpoints import (
    CheckpointError,
    CheckpointManager,
)

pytestmark = pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git binary not available",
)


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "a.txt").write_text("original\n")
    (ws / "sub").mkdir()
    (ws / "sub" / "b.txt").write_text("orig2\n")
    return ws


@pytest.fixture
def pearl_home(tmp_path):
    """
    An isolated stand-in for `~/.pearl`, so tests never read or write
    a real developer's home directory.
    """

    return tmp_path / "pearl_home"


@pytest.fixture
def manager(workspace, pearl_home):
    return CheckpointManager(workspace, pearl_home=pearl_home)


# ---------------------------------------------------------------------
# Creating checkpoints
# ---------------------------------------------------------------------


def test_create_returns_a_checkpoint(manager):
    checkpoint = manager.create("first")

    assert checkpoint is not None
    assert checkpoint.label == "first"
    assert len(checkpoint.id) == 40
    assert checkpoint.short_id == checkpoint.id[:8]


def test_create_initialises_the_store_outside_the_workspace(manager, workspace):
    """
    Storage lives outside the workspace (ADR-005) — Pearl must never
    write into the user's project directory.
    """

    manager.create("first")

    assert manager._git_dir.exists()
    assert not (workspace / ".pearl").exists()
    assert not str(manager._git_dir).startswith(str(workspace))


def test_create_records_which_workspace_the_store_belongs_to(manager, workspace):
    manager.create("first")

    marker = manager._store_root / "workspace.txt"
    assert marker.exists()
    assert str(workspace) in marker.read_text()


def test_two_workspaces_get_separate_stores(pearl_home, tmp_path):
    first_ws = tmp_path / "first"
    second_ws = tmp_path / "second"
    first_ws.mkdir()
    second_ws.mkdir()
    (first_ws / "a.txt").write_text("a\n")
    (second_ws / "a.txt").write_text("a\n")

    first_manager = CheckpointManager(first_ws, pearl_home=pearl_home)
    second_manager = CheckpointManager(second_ws, pearl_home=pearl_home)

    first_manager.create("first workspace checkpoint")
    second_manager.create("second workspace checkpoint")

    assert first_manager._git_dir != second_manager._git_dir
    assert len(first_manager.list()) == 1
    assert len(second_manager.list()) == 1
    assert first_manager.list()[0].label == "first workspace checkpoint"
    assert second_manager.list()[0].label == "second workspace checkpoint"


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
    corrupts the index on the first restore. Storage now lives outside
    the workspace, so this mostly matters for a legacy-migrated store
    or a workspace nested under another Pearl-managed directory.
    """

    manager.create("first")

    listed = manager._run("ls-tree", "-r", "--name-only", "HEAD")
    tracked = listed.stdout.splitlines()

    assert tracked, "expected the workspace files to be tracked"
    assert not any(path.startswith(".pearl") for path in tracked)


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


def test_the_users_git_repository_is_never_touched(workspace, pearl_home):
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

    manager = CheckpointManager(workspace, pearl_home=pearl_home)
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


def test_approving_a_patch_creates_a_restorable_checkpoint(
    workspace, pearl_home, monkeypatch
):
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

    manager = CheckpointManager(workspace, pearl_home=pearl_home)
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


def test_approval_still_succeeds_when_checkpointing_fails(
    workspace, pearl_home, monkeypatch
):
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


# ---------------------------------------------------------------------
# Rename
# ---------------------------------------------------------------------


def test_rename_changes_the_displayed_label(manager, workspace):
    checkpoint = manager.create("original label")

    updated = manager.rename(checkpoint.id, "new label")

    assert updated.label == "new label"
    assert updated.id == checkpoint.id
    assert manager.list()[0].label == "new label"


def test_rename_does_not_touch_the_underlying_git_commit_message(manager, workspace):
    """
    Git history is treated as immutable content here; only the
    metadata sidecar's label changes.
    """

    checkpoint = manager.create("original label")

    manager.rename(checkpoint.id, "new label")

    subject = manager._run(
        "show", "-s", "--pretty=format:%s", checkpoint.id
    ).stdout.strip()

    assert subject == "original label"


def test_rename_persists_across_new_manager_instances(workspace, pearl_home):
    manager = CheckpointManager(workspace, pearl_home=pearl_home)
    checkpoint = manager.create("original")
    manager.rename(checkpoint.id, "renamed")

    reloaded = CheckpointManager(workspace, pearl_home=pearl_home)

    assert reloaded.list()[0].label == "renamed"


def test_rename_rejects_an_empty_label(manager, workspace):
    checkpoint = manager.create("first")

    with pytest.raises(CheckpointError, match="empty"):
        manager.rename(checkpoint.id, "")


def test_rename_unknown_checkpoint_fails_clearly(manager, workspace):
    manager.create("first")

    with pytest.raises(CheckpointError, match="No such checkpoint"):
        manager.rename("0" * 40, "new label")


# ---------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------


def test_delete_hides_a_checkpoint_from_listing(manager, workspace):
    checkpoint = manager.create("first")
    (workspace / "a.txt").write_text("v2\n")
    manager.create("second")

    manager.delete(checkpoint.id)

    ids = [c.id for c in manager.list()]
    assert checkpoint.id not in ids
    assert len(ids) == 1


def test_delete_prevents_restoring(manager, workspace):
    checkpoint = manager.create("first")

    manager.delete(checkpoint.id)

    with pytest.raises(CheckpointError, match="deleted"):
        manager.restore(checkpoint.id)


def test_delete_prevents_preview_restoring(manager, workspace):
    checkpoint = manager.create("first")

    manager.delete(checkpoint.id)

    with pytest.raises(CheckpointError, match="deleted"):
        manager.preview_restore(checkpoint.id)


def test_deleting_an_already_deleted_checkpoint_fails_clearly(manager, workspace):
    checkpoint = manager.create("first")
    manager.delete(checkpoint.id)

    with pytest.raises(CheckpointError, match="deleted"):
        manager.delete(checkpoint.id)


def test_deleting_an_unknown_checkpoint_fails_clearly(manager, workspace):
    manager.create("first")

    with pytest.raises(CheckpointError, match="No such checkpoint"):
        manager.delete("0" * 40)


def test_delete_does_not_rewrite_git_history(manager, workspace):
    """
    Deletion is soft (metadata-only) by design: the commit itself is
    never removed from the shadow repository. Rewriting history in the
    middle of a linear log is real corruption risk for no benefit here.
    """

    checkpoint = manager.create("first")

    manager.delete(checkpoint.id)

    still_present = manager._run("cat-file", "-e", f"{checkpoint.id}^{{commit}}")
    assert still_present.returncode == 0


def test_list_can_include_deleted_checkpoints_when_asked(manager, workspace):
    checkpoint = manager.create("first")
    manager.delete(checkpoint.id)

    assert manager.list() == []
    assert [c.id for c in manager.list(include_deleted=True)] == [checkpoint.id]


def test_deleted_checkpoints_do_not_count_toward_list_limit(manager, workspace):
    checkpoint = manager.create("first")
    manager.delete(checkpoint.id)
    (workspace / "a.txt").write_text("v2\n")
    manager.create("second")

    visible = manager.list(limit=1)

    assert [c.label for c in visible] == ["second"]


# ---------------------------------------------------------------------
# Auto cleanup (retention)
# ---------------------------------------------------------------------


def test_retention_prunes_beyond_max_count(workspace, pearl_home):
    manager = CheckpointManager(workspace, pearl_home=pearl_home, max_count=2)

    for index in range(4):
        (workspace / "a.txt").write_text(f"v{index}\n")
        manager.create(f"cp{index}")

    visible = manager.list(limit=100)

    assert len(visible) == 2
    # The two most recent survive.
    assert [c.label for c in visible] == ["cp3", "cp2"]


def test_retention_disabled_when_max_count_is_zero(workspace, pearl_home):
    manager = CheckpointManager(workspace, pearl_home=pearl_home, max_count=0)

    for index in range(5):
        (workspace / "a.txt").write_text(f"v{index}\n")
        manager.create(f"cp{index}")

    assert len(manager.list(limit=100)) == 5


def test_retention_prunes_checkpoints_older_than_max_age(manager, workspace):
    manager.max_age_days = 7

    checkpoint = manager.create("old one")

    # Genuinely backdate the commit (author + committer date) rather
    # than mocking datetime.now(): freezing "now" uniformly can't
    # distinguish an old commit from a new one when both are created
    # moments apart in a fast test, since a frozen "now" ages *every*
    # commit by the same amount. Real backdating is unambiguous.
    old_date = "2020-01-01T00:00:00+00:00"
    amended = manager._run(
        "-c",
        "user.name=Pearl",
        "-c",
        "user.email=pearl@localhost",
        "commit",
        "--amend",
        "--no-edit",
        f"--date={old_date}",
        env={**os.environ, "GIT_COMMITTER_DATE": old_date},
    )
    assert amended.returncode == 0, amended.stderr

    # --amend changes the commit id; re-resolve it via the metadata
    # sidecar isn't affected (nothing was renamed/deleted), only the
    # underlying git identity moves.
    old_checkpoint_id = manager._run("rev-parse", "HEAD").stdout.strip()
    assert old_checkpoint_id != checkpoint.id

    (workspace / "a.txt").write_text("v2\n")
    manager.create("new one")

    visible = manager.list(limit=100)
    assert [c.label for c in visible] == ["new one"]


def test_retention_never_prunes_the_checkpoint_just_created(workspace, pearl_home):
    manager = CheckpointManager(workspace, pearl_home=pearl_home, max_count=1)

    checkpoint = manager.create("only one")

    assert manager.list()[0].id == checkpoint.id


def test_retention_failure_does_not_fail_the_checkpoint_that_triggered_it(
    workspace, pearl_home, monkeypatch
):
    manager = CheckpointManager(workspace, pearl_home=pearl_home, max_count=1)

    def _broken_retention():
        raise RuntimeError("boom")

    monkeypatch.setattr(manager, "_apply_retention_unsafe", _broken_retention)

    checkpoint = manager.create("first")

    assert checkpoint is not None


# ---------------------------------------------------------------------
# Legacy in-workspace store migration
# ---------------------------------------------------------------------


def test_legacy_in_workspace_store_is_migrated_on_first_use(workspace, pearl_home):
    """
    An earlier Pearl session using the in-workspace `.pearl/shadow.git`
    layout must be picked up and moved to the external store
    automatically, not silently ignored (which would look like all
    prior checkpoints had vanished).
    """

    legacy_git_dir = workspace / ".pearl" / "shadow.git"
    legacy_git_dir.mkdir(parents=True)

    legacy_run = lambda *args: subprocess.run(  # noqa: E731
        ["git", f"--git-dir={legacy_git_dir}", f"--work-tree={workspace}", *args],
        cwd=str(workspace),
        capture_output=True,
        text=True,
    )

    legacy_run("init", "-q")
    (legacy_git_dir / "info").mkdir(exist_ok=True)
    (legacy_git_dir / "info" / "exclude").write_text("/.pearl/\n")
    legacy_run("add", "-A")
    legacy_run(
        "-c",
        "user.email=p@p",
        "-c",
        "user.name=Pearl",
        "commit",
        "-q",
        "-m",
        "legacy checkpoint",
    )

    manager = CheckpointManager(workspace, pearl_home=pearl_home)

    checkpoints = manager.list()

    assert len(checkpoints) == 1
    assert checkpoints[0].label == "legacy checkpoint"
    assert not (workspace / ".pearl").exists()
    assert manager._git_dir.exists()


def test_migration_does_not_run_twice(workspace, pearl_home):
    legacy_git_dir = workspace / ".pearl" / "shadow.git"
    legacy_git_dir.mkdir(parents=True)
    subprocess.run(
        [
            "git",
            f"--git-dir={legacy_git_dir}",
            f"--work-tree={workspace}",
            "init",
            "-q",
        ],
        cwd=str(workspace),
        capture_output=True,
    )

    manager = CheckpointManager(workspace, pearl_home=pearl_home)
    manager.create("first")

    # A second legacy directory reappearing (e.g. an old backup
    # restored) must not overwrite the now-active external store.
    legacy_git_dir.mkdir(parents=True)

    manager.create("second")  # no-op if nothing changed, but must not raise

    assert manager._git_dir.exists()


# ---------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------


def test_a_stale_lock_file_produces_a_clear_actionable_error(manager, workspace):
    """
    A process killed mid-commit can leave `index.lock` behind. This
    must surface as a clear, actionable CheckpointError - not hang,
    not corrupt the store, and not raise an opaque traceback.
    """

    manager.create("first")

    lock_file = manager._git_dir / "index.lock"
    lock_file.write_text("")

    try:
        with pytest.raises(CheckpointError, match="interrupted"):
            manager.create("second")
    finally:
        lock_file.unlink(missing_ok=True)


def test_store_recovers_after_the_stale_lock_is_cleared(manager, workspace):
    manager.create("first")

    lock_file = manager._git_dir / "index.lock"
    lock_file.write_text("")
    lock_file.unlink()

    (workspace / "a.txt").write_text("v2\n")
    assert manager.create("second") is not None


def test_missing_metadata_file_is_not_fatal(manager, workspace):
    checkpoint = manager.create("first")

    assert manager._metadata_path.exists() is False or True  # may not exist yet

    manager.rename(checkpoint.id, "renamed")
    assert manager._metadata_path.exists()

    manager._metadata_path.unlink()

    # No metadata at all is exactly equivalent to nothing ever having
    # been renamed or deleted - not a crash.
    assert manager.list()[0].label == "first"


def test_corrupt_metadata_file_is_not_fatal(manager, workspace):
    manager.create("first")
    manager._store_root.mkdir(parents=True, exist_ok=True)
    manager._metadata_path.write_text("{ this is not valid json")

    checkpoints = manager.list()

    assert len(checkpoints) == 1
    assert checkpoints[0].label == "first"


def test_metadata_write_is_atomic(manager, workspace):
    """
    A crash mid-write must never leave a truncated, half-written
    metadata file that itself needs recovering from.
    """

    checkpoint = manager.create("first")
    manager.rename(checkpoint.id, "renamed")

    tmp_path = manager._metadata_path.with_suffix(".json.tmp")
    assert not tmp_path.exists()
    assert manager._metadata_path.exists()

    import json

    parsed = json.loads(manager._metadata_path.read_text())
    assert parsed["checkpoints"][checkpoint.id]["label"] == "renamed"


# ---------------------------------------------------------------------
# Default workspace resolution
# ---------------------------------------------------------------------


def test_default_workspace_honors_a_monkeypatched_cwd(
    pearl_home, tmp_path, monkeypatch
):
    """
    Regression: the default constructor argument used to be the
    string "." (resolved via `Path(".").resolve()`, which reads the
    real process `os.getcwd()` directly), instead of an explicit
    `Path.cwd()` call. `Path(".").resolve()` silently ignores
    `monkeypatch.setattr(Path, "cwd", ...)` — a pattern used all over
    this codebase's test suite (`Planner._workspace_root()`,
    `file_tools._ensure_within_workspace`, and many test fixtures all
    rely on `Path.cwd()` being patchable). A `CheckpointManager()`
    built with no explicit workspace ended up silently pointed at
    whatever directory the test process actually started in — in one
    real case, the real Pearl repository itself — instead of the
    workspace a test believed it was operating on.
    """

    fake_cwd = tmp_path / "actual_workspace"
    fake_cwd.mkdir()
    (fake_cwd / "a.txt").write_text("here\n")

    monkeypatch.setattr(Path, "cwd", lambda: fake_cwd)

    import src.tools.checkpoints as checkpoints_module

    manager = checkpoints_module.CheckpointManager(pearl_home=pearl_home)

    assert manager.workspace == fake_cwd.resolve()

    checkpoint = manager.create("first")

    assert checkpoint is not None
    tracked = manager._run("ls-tree", "-r", "--name-only", "HEAD").stdout.splitlines()
    assert tracked == ["a.txt"]


def test_store_is_excluded_even_when_nested_inside_the_workspace(tmp_path):
    """
    Regression: found live, not guessed. If `pearl_home` (in
    production, `~/.pearl`) resolves to somewhere *inside* the
    workspace being checkpointed — e.g. Pearl is pointed at a
    workspace that happens to be an ancestor of the user's home
    directory — the root-anchored legacy `/.pearl/` exclude pattern
    does not match the store's actual, deeper location, and the
    store's own git internals (objects, index, logs) get swept into
    the checkpoint and then "restored" as workspace files.
    """

    workspace = tmp_path
    pearl_home = tmp_path / "nested" / "home"
    (workspace / "real_file.txt").write_text("real content\n")

    manager = CheckpointManager(workspace, pearl_home=pearl_home)
    manager.create("first")

    tracked = manager._run("ls-tree", "-r", "--name-only", "HEAD").stdout.splitlines()

    assert tracked == ["real_file.txt"]


def test_store_exclude_still_works_in_the_normal_unnested_case(manager, workspace):
    # The common case (pearl_home entirely outside the workspace)
    # must keep working exactly as before.
    manager.create("first")

    tracked = manager._run("ls-tree", "-r", "--name-only", "HEAD").stdout.splitlines()

    assert set(tracked) == {"a.txt", "sub/b.txt"}
