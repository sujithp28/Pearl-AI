"""
Mutating git operations must not reach the repository unapproved.

`git_restore` discards uncommitted working-tree changes and cannot be
undone. It used to run the moment the planner called it: it writes
through subprocess rather than ChangeManager, so the diff-and-approve
gate never saw it, and an autonomous run could destroy a developer's
unsaved work with no prompt at all. `git_commit`, `git_stage` and
`git_create_branch` had the same shape.

Git writes are not diffable, so they ride CommandApprovalManager — the
gate shell commands already use — rather than ChangeManager. Reads are
unaffected: they never touch the repository.
"""

from __future__ import annotations

import subprocess

import pytest

from src.tools.command_approval import CommandApprovalManager
from src.tools.git_tools import (
    git_commit,
    git_create_branch,
    git_diff,
    git_restore,
    git_stage,
    git_status,
)
from src.tools.shell_tools import set_active_command_approver


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    from src.config.workspace import clear_workspace_root, set_workspace_root

    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    (tmp_path / "tracked.txt").write_text("committed\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "initial")

    set_workspace_root(tmp_path)
    yield tmp_path
    set_active_command_approver(None)
    clear_workspace_root()


@pytest.fixture
def approver():
    """An active approver — what an autonomous run installs."""
    manager = CommandApprovalManager(runner=lambda request: None)
    set_active_command_approver(manager)
    yield manager
    set_active_command_approver(None)


# ---------------------------------------------------------------------
# The destructive one
# ---------------------------------------------------------------------


def test_git_restore_does_not_discard_work_without_approval(repo, approver):
    """
    The P0 case: uncommitted work must survive a git_restore that was
    never approved.
    """
    target = repo / "tracked.txt"
    target.write_text("work in progress I have not committed\n", encoding="utf-8")

    result = git_restore()

    assert approver.has_pending()
    assert "Approval required" in result
    assert target.read_text(encoding="utf-8") == (
        "work in progress I have not committed\n"
    )


def test_git_restore_of_named_files_is_also_gated(repo, approver):
    target = repo / "tracked.txt"
    target.write_text("mine\n", encoding="utf-8")

    result = git_restore(["tracked.txt"])

    assert approver.has_pending()
    assert "Approval required" in result
    assert target.read_text(encoding="utf-8") == "mine\n"


def test_the_staged_command_is_the_real_git_command(repo, approver):
    (repo / "tracked.txt").write_text("mine\n", encoding="utf-8")

    git_restore(["tracked.txt"])

    command = approver.pending[0].request.command
    assert command.startswith("git restore")
    assert "tracked.txt" in command


# ---------------------------------------------------------------------
# The rest of the mutating set
# ---------------------------------------------------------------------


def test_git_stage_is_gated(repo, approver):
    (repo / "new.txt").write_text("x\n", encoding="utf-8")

    result = git_stage(["new.txt"])

    assert approver.has_pending()
    assert "Approval required" in result
    assert git_status()["staged"] == []


def test_git_commit_is_gated(repo, approver):
    (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
    _git(repo, "add", ".")

    result = git_commit("a message")

    assert approver.has_pending()
    assert "Approval required" in result
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True
    )
    assert log.stdout.count("\n") == 1  # still just the initial commit


def test_git_create_branch_is_gated(repo, approver):
    result = git_create_branch("feature/x")

    assert approver.has_pending()
    assert "Approval required" in result
    branches = subprocess.run(
        ["git", "branch"], cwd=repo, capture_output=True, text=True
    )
    assert "feature/x" not in branches.stdout


# ---------------------------------------------------------------------
# Reads are untouched, and direct calls still work
# ---------------------------------------------------------------------


def test_read_only_git_tools_are_not_gated(repo, approver):
    (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")

    assert git_status()["unstaged"]
    assert git_diff()
    assert not approver.has_pending()


def test_without_an_approver_git_restore_still_works(repo):
    """
    A direct CLI or tools/call invocation has a human present already.
    Gating must not break it.
    """
    target = repo / "tracked.txt"
    target.write_text("scratch\n", encoding="utf-8")

    result = git_restore()

    assert "Restored" in result
    assert target.read_text(encoding="utf-8") == "committed\n"


def test_without_an_approver_git_stage_still_works(repo):
    (repo / "new.txt").write_text("x\n", encoding="utf-8")

    git_stage(["new.txt"])

    assert "new.txt" in git_status()["staged"]
