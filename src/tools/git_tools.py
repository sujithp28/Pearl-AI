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
        commit_hash, author, date, message = line.split(_LOG_FIELD_SEPARATOR, 3)

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
        raise GitError(f"Could not create branch '{name}': {result.stderr.strip()}")

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
            'execute_shell("git add <files>")) before committing.'
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


@tool(
    description=(
        "Stage (git add) specific files or directories. Pass an empty "
        "list to stage everything that is modified or new. Required "
        "before git_commit, which only commits what is already staged."
    ),
    parameters={"files": "list[str]"},
    returns="str",
)
def git_stage(files: list[str] | None = None) -> str:
    """
    Stage files for the next commit.

    When `files` is empty or None, stages all modified and untracked
    files (equivalent to ``git add .``). When non-empty, stages only
    the listed paths.

    Never stages files outside the workspace root.
    """

    _ensure_git_repo()

    if not files:
        logger.info("Staging all changes (git add .).")
        result = _run_git("add", ".")
    else:
        if not all(isinstance(f, str) and f for f in files):
            raise ValueError("Every entry in 'files' must be a non-empty string.")

        logger.info("Staging: %s", files)
        result = _run_git("add", "--", *files)

    if result.returncode != 0:
        raise GitError(f"git add failed: {result.stderr.strip()}")

    staged = git_status().get("staged", [])
    return f"Staged {len(staged)} file(s)."


@tool(
    description=(
        "Show line-level authorship for a file (git blame). Returns a "
        "list of annotated lines, each with the commit hash, author, "
        "date, and the line content. Useful for understanding when and "
        "why each line was last changed."
    ),
    parameters={"path": "str", "start_line": "int", "end_line": "int"},
    returns="list[dict]",
)
def git_blame(
    path: str,
    start_line: int = 1,
    end_line: int = 0,
) -> list[dict[str, Any]]:
    """
    Return per-line authorship annotation for `path`.

    Parameters
    ----------
    path:
        File to annotate, relative to the workspace root.
    start_line:
        First line to include (1-indexed).  Defaults to 1.
    end_line:
        Last line to include (1-indexed, inclusive).  0 means "all
        remaining lines" (the default).
    """

    _ensure_git_repo()

    if not path or not path.strip():
        raise ValueError("'path' must not be empty.")

    if start_line < 1:
        raise ValueError("'start_line' must be >= 1.")

    logger.info("Running git blame on '%s'.", path)

    args = ["blame", "--porcelain"]
    if end_line > 0:
        if end_line < start_line:
            raise ValueError("'end_line' must be >= 'start_line'.")
        args += [f"-L{start_line},{end_line}"]
    elif start_line > 1:
        args += [f"-L{start_line}"]

    args += ["--", path]

    result = _run_git(*args)

    if result.returncode != 0:
        raise GitError(f"git blame failed: {result.stderr.strip()}")

    return _parse_blame_porcelain(result.stdout)


def _parse_blame_porcelain(output: str) -> list[dict[str, Any]]:
    """
    Parse ``git blame --porcelain`` output into a list of annotated
    line records.

    Each record has: ``commit``, ``author``, ``date``, ``line_number``,
    ``content``.
    """

    records: list[dict[str, Any]] = []
    lines = output.splitlines()
    i = 0
    current: dict[str, Any] = {}

    while i < len(lines):
        line = lines[i]

        # Header line: "<40-char hash> <orig-line> <final-line> [<num-lines>]"
        if len(line) >= 40 and line[:40].isalnum() and " " in line[40:]:
            parts = line.split()
            current = {"commit": parts[0]}
            try:
                current["line_number"] = int(parts[2])
            except (IndexError, ValueError):
                current["line_number"] = 0
        elif line.startswith("author "):
            current["author"] = line[7:]
        elif line.startswith("author-time "):
            import datetime

            ts = int(line[12:])
            current["date"] = datetime.datetime.fromtimestamp(
                ts, tz=datetime.timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        elif line.startswith("\t"):
            current["content"] = line[1:]
            records.append(dict(current))

        i += 1

    return records


@tool(
    description=(
        "Summarize uncommitted workspace changes: what files changed, "
        "how many lines added/removed, and which files are new or deleted."
    ),
    returns="dict",
)
def summarize_changes() -> dict[str, Any]:
    """
    Return a structured summary of the current working-tree changes.

    Combines `git diff --stat` (unstaged) and `git diff --cached --stat`
    (staged) to give a complete picture of what has changed since the
    last commit, without reading any file content.

    Returns
    -------
    dict with keys:
      staged_files    list[str]  — files changed in the index (staging area)
      unstaged_files  list[str]  — files changed in the working tree
      untracked_files list[str]  — files not yet tracked by git
      insertions      int        — total lines added (staged + unstaged)
      deletions       int        — total lines removed (staged + unstaged)
      total_changed   int        — total files with any change
    """

    _ensure_git_repo()

    status = git_status()

    staged_files: list[str] = status.get("staged", [])
    unstaged_files: list[str] = status.get("unstaged", [])
    untracked_files: list[str] = status.get("untracked", [])

    insertions = 0
    deletions = 0

    for flag, args in (
        ("staged", ["diff", "--cached", "--numstat"]),
        ("unstaged", ["diff", "--numstat"]),
    ):
        result = _run_git(*args)
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2:
                    try:
                        insertions += int(parts[0]) if parts[0] != "-" else 0
                        deletions += int(parts[1]) if parts[1] != "-" else 0
                    except ValueError:
                        pass

    total_changed = len(set(staged_files) | set(unstaged_files))

    logger.info(
        "Summarized changes: %d staged, %d unstaged, %d untracked, +%d -%d",
        len(staged_files),
        len(unstaged_files),
        len(untracked_files),
        insertions,
        deletions,
    )

    return {
        "staged_files": staged_files,
        "unstaged_files": unstaged_files,
        "untracked_files": untracked_files,
        "insertions": insertions,
        "deletions": deletions,
        "total_changed": total_changed,
    }
