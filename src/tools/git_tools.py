"""
Git intelligence and safe-commit tools for Pearl.

These tools give Pearl first-class Git awareness (status, diff, log)
and the ability to make safe changes (branch, commit, restore) — all
as ordinary tools registered with the existing `ToolRegistry` /
`ToolDispatcher`, exactly like the file, shell, and repository tools.
No separate Git subsystem exists: the Planner can select these tools
the same way it selects any other.

Every destructive or history-changing operation here is deliberately
narrow: `git_commit` only ever commits what's already staged (and
refuses to run if there's nothing to commit), `git_restore` only ever
discards working-tree changes when explicitly asked, and neither this
module nor any tool in it force-pushes or deletes a branch.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

from src.tools.metadata import tool

logger = logging.getLogger(__name__)

GIT_TIMEOUT = 30

_LOG_FIELD_SEPARATOR = "\x1f"


class GitError(RuntimeError):
    """
    Raised for invalid or unsafe Git states (not a repository, a
    failed Git command, nothing to commit, nothing staged, ...).
    """


def _run_git(*args: str) -> subprocess.CompletedProcess:
    """
    Run a `git` subcommand in the current working directory.

    Never raises on a non-zero exit: callers inspect `returncode` /
    `stderr` themselves so they can surface clear, Git-specific error
    messages instead of a raw `CalledProcessError` traceback.
    """

    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
    )


def _ensure_git_repo() -> None:
    """
    Raise `GitError` if the current directory isn't inside a Git
    working tree.
    """

    result = _run_git("rev-parse", "--is-inside-work-tree")

    if result.returncode != 0 or result.stdout.strip() != "true":
        raise GitError(f"Not a git repository: {Path.cwd()}")


def _parse_branch_header(line: str) -> str:
    """
    Parse the `## ...` header line from `git status --porcelain
    --branch` into a short, human-readable branch label.
    """

    header = line[3:] if line.startswith("## ") else line[2:]

    if header.startswith("HEAD"):
        return "HEAD (detached)"

    return header.split("...", 1)[0]


@tool(
    description="Show unstaged changes in the working tree as a unified diff.",
    returns="str",
)
def git_diff() -> str:
    """
    Return the output of `git diff` (working tree vs. the index).
    """

    _ensure_git_repo()

    logger.info("Running git diff.")

    result = _run_git("diff")

    if result.returncode != 0:
        raise GitError(f"git diff failed: {result.stderr.strip()}")

    return result.stdout


@tool(
    description=(
        "Show the working tree status: current branch, staged, "
        "unstaged, and untracked files."
    ),
    returns="dict",
)
def git_status() -> dict[str, Any]:
    """
    Return a structured summary of `git status`.
    """

    _ensure_git_repo()

    logger.info("Running git status.")

    result = _run_git("status", "--porcelain=v1", "--branch")

    if result.returncode != 0:
        raise GitError(f"git status failed: {result.stderr.strip()}")

    lines = result.stdout.splitlines()

    has_branch_header = bool(lines) and lines[0].startswith("##")
    branch = _parse_branch_header(lines[0]) if has_branch_header else ""
    entries = lines[1:] if has_branch_header else lines

    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []

    for line in entries:
        if not line:
            continue

        code, path = line[:2], line[3:]

        if code == "??":
            untracked.append(path)
            continue

        if code[0] != " ":
            staged.append(path)

        if code[1] != " ":
            unstaged.append(path)

    return {
        "branch": branch,
        "clean": not (staged or unstaged or untracked),
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
    }


@tool(
    description="Show the most recent commits (hash, author, date, message).",
    parameters={"limit": "int"},
    returns="list[dict]",
)
def git_log(limit: int = 10) -> list[dict[str, Any]]:
    """
    Return the last `limit` commits reachable from HEAD.

    Returns an empty list (rather than raising) for a repository that
    doesn't have any commits yet.
    """

    _ensure_git_repo()

    if limit <= 0:
        raise ValueError("`limit` must be a positive integer.")

    logger.info("Running git log (limit=%d).", limit)

    result = _run_git(
        "log",
        f"-{limit}",
        f"--pretty=format:%H{_LOG_FIELD_SEPARATOR}%an"
        f"{_LOG_FIELD_SEPARATOR}%ad{_LOG_FIELD_SEPARATOR}%s",
        "--date=iso-strict",
    )

    if result.returncode != 0:
        if "does not have any commits yet" in result.stderr:
            return []

        raise GitError(f"git log failed: {result.stderr.strip()}")

    if not result.stdout.strip():
        return []

    commits = []

    for line in result.stdout.splitlines():
        commit_hash, author, date, message = line.split(
            _LOG_FIELD_SEPARATOR, 3
        )

        commits.append(
            {
                "hash": commit_hash,
                "author": author,
                "date": date,
                "message": message,
            }
        )

    return commits


@tool(
    description="Create a new branch from HEAD and switch to it.",
    parameters={"name": "str"},
    returns="str",
)
def git_create_branch(name: str) -> str:
    """
    Create branch `name` and check it out.

    Never deletes or force-updates an existing branch: if `name`
    already exists, this fails with a clear error instead.
    """

    _ensure_git_repo()

    if not name or not name.strip():
        raise ValueError("`name` must not be empty.")

    name = name.strip()

    if name.startswith("-"):
        raise ValueError("Branch name must not start with '-'.")

    logger.info("Creating branch: %s", name)

    result = _run_git("checkout", "-b", name)

    if result.returncode != 0:
        raise GitError(
            f"Could not create branch '{name}': {result.stderr.strip()}"
        )

    logger.info("Switched to new branch '%s'.", name)

    return f"Created and switched to branch '{name}'."


@tool(
    description=(
        "Commit currently staged changes. Fails clearly if there is "
        "nothing to commit or nothing staged."
    ),
    parameters={"message": "str"},
    returns="str",
)
def git_commit(message: str) -> str:
    """
    Commit the currently staged changes with `message`.

    Never commits when the working tree is clean, and never commits
    unstaged changes on the caller's behalf — stage them first (e.g.
    via `execute_shell("git add ...")`) and only then call this.
    """

    _ensure_git_repo()

    if not message or not message.strip():
        raise ValueError("`message` must not be empty.")

    status = git_status()

    if status["clean"]:
        raise GitError("Nothing to commit: the working tree is clean.")

    if not status["staged"]:
        raise GitError(
            "Nothing staged to commit. Stage changes (e.g. via "
            "execute_shell(\"git add <files>\")) before committing."
        )

    logger.info("Committing %d staged file(s).", len(status["staged"]))

    result = _run_git("commit", "-m", message)

    if result.returncode != 0:
        raise GitError(f"git commit failed: {result.stderr.strip()}")

    commit_hash = _run_git("rev-parse", "HEAD").stdout.strip()

    logger.info("Committed %s.", commit_hash[:8])

    return f"Committed {commit_hash[:8]}: {message}"


@tool(
    description=(
        "Discard uncommitted working-tree changes for the given files "
        "(or every modified file if none are given). Never touches "
        "staged changes or commit history."
    ),
    parameters={"files": "list[str]"},
    returns="str",
)
def git_restore(files: list[str] | None = None) -> str:
    """
    Discard unstaged working-tree changes.

    Only ever runs as an explicit, direct call — nothing else in
    Pearl invokes this as a side effect of another operation.
    """

    _ensure_git_repo()

    if files is None:
        status = git_status()
        targets = status["unstaged"]

        if not targets:
            return "Nothing to restore: no unstaged changes."
    else:
        if not files:
            raise ValueError("`files` must not be an empty list.")

        targets = files

    logger.info("Restoring: %s", targets)

    result = _run_git("restore", "--", *targets)

    if result.returncode != 0:
        raise GitError(f"git restore failed: {result.stderr.strip()}")

    return f"Restored {len(targets)} file(s): {', '.join(targets)}"
