import subprocess

import pytest

from src.tools.git_tools import (
    GitError,
    git_commit,
    git_create_branch,
    git_diff,
    git_log,
    git_restore,
    git_status,
)


def _git(*args):
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch):
    """
    Run every test with tmp_path as the current directory, since
    these tools (like the shell tools) operate on the process's cwd.
    """

    monkeypatch.chdir(tmp_path)


def _init_repo():
    _git("init", "-b", "main")
    _git("config", "user.email", "pearl@example.com")
    _git("config", "user.name", "Pearl Test")


def _commit_all(message="initial commit"):
    _git("add", "-A")
    _git("commit", "-m", message)
    return _git("rev-parse", "HEAD").stdout.strip()


# ---------------------------------------------------------------------
# Invalid repository
# ---------------------------------------------------------------------


def test_git_status_rejects_non_git_directory():
    with pytest.raises(GitError, match="Not a git repository"):
        git_status()


def test_git_diff_rejects_non_git_directory():
    with pytest.raises(GitError, match="Not a git repository"):
        git_diff()


def test_git_log_rejects_non_git_directory():
    with pytest.raises(GitError, match="Not a git repository"):
        git_log()


def test_git_create_branch_rejects_non_git_directory():
    with pytest.raises(GitError, match="Not a git repository"):
        git_create_branch("feature")


def test_git_commit_rejects_non_git_directory():
    with pytest.raises(GitError, match="Not a git repository"):
        git_commit("a message")


def test_git_restore_rejects_non_git_directory():
    with pytest.raises(GitError, match="Not a git repository"):
        git_restore()


# ---------------------------------------------------------------------
# Clean repository
# ---------------------------------------------------------------------


def test_git_status_reports_clean_repository(tmp_path):
    _init_repo()
    (tmp_path / "README.md").write_text("hello\n")
    _commit_all()

    status = git_status()

    assert status["clean"] is True
    assert status["staged"] == []
    assert status["unstaged"] == []
    assert status["untracked"] == []
    assert status["branch"] == "main"


def test_git_diff_empty_on_clean_repository(tmp_path):
    _init_repo()
    (tmp_path / "README.md").write_text("hello\n")
    _commit_all()

    assert git_diff() == ""


def test_git_commit_fails_on_clean_repository(tmp_path):
    _init_repo()
    (tmp_path / "README.md").write_text("hello\n")
    _commit_all()

    with pytest.raises(GitError, match="clean"):
        git_commit("nothing to do")


def test_git_restore_is_a_noop_on_clean_repository(tmp_path):
    _init_repo()
    (tmp_path / "README.md").write_text("hello\n")
    _commit_all()

    result = git_restore()

    assert "Nothing to restore" in result


# ---------------------------------------------------------------------
# Modified repository
# ---------------------------------------------------------------------


def test_git_status_reports_modified_and_untracked_files(tmp_path):
    _init_repo()
    (tmp_path / "a.txt").write_text("original\n")
    _commit_all()

    (tmp_path / "a.txt").write_text("changed\n")
    (tmp_path / "b.txt").write_text("new file\n")

    status = git_status()

    assert status["clean"] is False
    assert status["unstaged"] == ["a.txt"]
    assert status["untracked"] == ["b.txt"]
    assert status["staged"] == []


def test_git_diff_shows_unstaged_changes(tmp_path):
    _init_repo()
    (tmp_path / "a.txt").write_text("original\n")
    _commit_all()

    (tmp_path / "a.txt").write_text("changed\n")

    diff = git_diff()

    assert "a.txt" in diff
    assert "-original" in diff
    assert "+changed" in diff


# ---------------------------------------------------------------------
# No staged changes (git_commit safety)
# ---------------------------------------------------------------------


def test_git_commit_fails_with_unstaged_but_no_staged_changes(tmp_path):
    _init_repo()
    (tmp_path / "a.txt").write_text("original\n")
    _commit_all()

    (tmp_path / "a.txt").write_text("changed\n")

    with pytest.raises(GitError, match="staged"):
        git_commit("should not commit")


def test_git_commit_never_auto_stages(tmp_path):
    _init_repo()
    (tmp_path / "a.txt").write_text("original\n")
    _commit_all()

    (tmp_path / "a.txt").write_text("changed\n")

    with pytest.raises(GitError):
        git_commit("should fail, not silently stage")

    # The file must still be unstaged: git_commit must not have
    # run `git add` on our behalf.
    status = git_status()
    assert status["unstaged"] == ["a.txt"]
    assert status["staged"] == []


# ---------------------------------------------------------------------
# Commit success
# ---------------------------------------------------------------------


def test_git_commit_succeeds_with_staged_changes(tmp_path):
    _init_repo()
    (tmp_path / "a.txt").write_text("original\n")
    _commit_all()

    (tmp_path / "a.txt").write_text("changed\n")
    _git("add", "a.txt")

    result = git_commit("update a.txt")

    assert "Committed" in result
    assert git_status()["clean"] is True


def test_git_commit_creates_a_new_log_entry(tmp_path):
    _init_repo()
    (tmp_path / "a.txt").write_text("original\n")
    _commit_all("first commit")

    (tmp_path / "a.txt").write_text("changed\n")
    _git("add", "a.txt")
    git_commit("second commit")

    log = git_log(limit=5)

    assert len(log) == 2
    assert log[0]["message"] == "second commit"
    assert log[1]["message"] == "first commit"
    assert all("hash" in entry and "author" in entry for entry in log)


def test_git_commit_rejects_empty_message(tmp_path):
    _init_repo()
    (tmp_path / "a.txt").write_text("original\n")
    _commit_all()

    (tmp_path / "a.txt").write_text("changed\n")
    _git("add", "a.txt")

    with pytest.raises(ValueError):
        git_commit("")


# ---------------------------------------------------------------------
# git_log
# ---------------------------------------------------------------------


def test_git_log_empty_repository_returns_empty_list():
    _init_repo()

    assert git_log() == []


def test_git_log_respects_limit(tmp_path):
    _init_repo()

    for i in range(5):
        (tmp_path / "a.txt").write_text(f"version {i}\n")
        _commit_all(f"commit {i}")

    log = git_log(limit=2)

    assert len(log) == 2
    assert log[0]["message"] == "commit 4"


def test_git_log_rejects_non_positive_limit(tmp_path):
    _init_repo()
    (tmp_path / "a.txt").write_text("x\n")
    _commit_all()

    with pytest.raises(ValueError):
        git_log(limit=0)


# ---------------------------------------------------------------------
# Branch creation
# ---------------------------------------------------------------------


def test_git_create_branch_switches_to_new_branch(tmp_path):
    _init_repo()
    (tmp_path / "a.txt").write_text("x\n")
    _commit_all()

    result = git_create_branch("feature/awesome")

    assert "feature/awesome" in result
    assert git_status()["branch"] == "feature/awesome"


def test_git_create_branch_rejects_duplicate_name(tmp_path):
    _init_repo()
    (tmp_path / "a.txt").write_text("x\n")
    _commit_all()

    git_create_branch("feature")
    _git("checkout", "main")

    with pytest.raises(GitError):
        git_create_branch("feature")


def test_git_create_branch_rejects_empty_name(tmp_path):
    _init_repo()
    (tmp_path / "a.txt").write_text("x\n")
    _commit_all()

    with pytest.raises(ValueError):
        git_create_branch("   ")


def test_git_create_branch_rejects_flag_like_name(tmp_path):
    _init_repo()
    (tmp_path / "a.txt").write_text("x\n")
    _commit_all()

    with pytest.raises(ValueError):
        git_create_branch("-D")


# ---------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------


def test_git_restore_discards_all_unstaged_changes_by_default(tmp_path):
    _init_repo()
    (tmp_path / "a.txt").write_text("original\n")
    (tmp_path / "b.txt").write_text("original\n")
    _commit_all()

    (tmp_path / "a.txt").write_text("changed\n")
    (tmp_path / "b.txt").write_text("changed\n")

    result = git_restore()

    assert "2 file" in result
    assert (tmp_path / "a.txt").read_text() == "original\n"
    assert (tmp_path / "b.txt").read_text() == "original\n"


def test_git_restore_discards_only_specified_files(tmp_path):
    _init_repo()
    (tmp_path / "a.txt").write_text("original\n")
    (tmp_path / "b.txt").write_text("original\n")
    _commit_all()

    (tmp_path / "a.txt").write_text("changed\n")
    (tmp_path / "b.txt").write_text("changed\n")

    git_restore(files=["a.txt"])

    assert (tmp_path / "a.txt").read_text() == "original\n"
    assert (tmp_path / "b.txt").read_text() == "changed\n"


def test_git_restore_never_touches_staged_changes(tmp_path):
    _init_repo()
    (tmp_path / "a.txt").write_text("original\n")
    _commit_all()

    (tmp_path / "a.txt").write_text("changed\n")
    _git("add", "a.txt")

    git_restore()

    # Staged changes are untouched: restore only ever discards
    # *unstaged* working-tree modifications.
    status = git_status()
    assert status["staged"] == ["a.txt"]
    assert (tmp_path / "a.txt").read_text() == "changed\n"


def test_git_restore_rejects_empty_file_list(tmp_path):
    _init_repo()
    (tmp_path / "a.txt").write_text("x\n")
    _commit_all()

    with pytest.raises(ValueError):
        git_restore(files=[])


# ---------------------------------------------------------------------
# Detached HEAD
# ---------------------------------------------------------------------


def test_git_status_reports_detached_head(tmp_path):
    _init_repo()
    (tmp_path / "a.txt").write_text("first\n")
    first_commit = _commit_all("first")

    (tmp_path / "a.txt").write_text("second\n")
    _commit_all("second")

    _git("checkout", first_commit)

    status = git_status()

    assert status["branch"] == "HEAD (detached)"
    assert status["clean"] is True


def test_git_diff_still_works_in_detached_head(tmp_path):
    _init_repo()
    (tmp_path / "a.txt").write_text("first\n")
    first_commit = _commit_all("first")

    (tmp_path / "a.txt").write_text("second\n")
    _commit_all("second")

    _git("checkout", first_commit)
    (tmp_path / "a.txt").write_text("edited while detached\n")

    diff = git_diff()

    assert "a.txt" in diff


def test_git_create_branch_works_from_detached_head(tmp_path):
    _init_repo()
    (tmp_path / "a.txt").write_text("first\n")
    first_commit = _commit_all("first")

    (tmp_path / "a.txt").write_text("second\n")
    _commit_all("second")

    _git("checkout", first_commit)

    result = git_create_branch("rescued")

    assert "rescued" in result
    assert git_status()["branch"] == "rescued"
