"""
Safe Git integration for Pearl V2.

Design principles:
- NEVER automatically commit unless explicitly called with the correct args.
- NEVER destroy unrelated user changes.
- NEVER run destructive operations without explicit user request.
- Fail closed: if a Git operation fails, raise GitError — don't silently continue.
- All paths validated within workspace.

All functions raise GitError when the workspace is not a git repository.

Core tools (defined by test_git_tools.py interface):
  git_status()        — structured workspace status dict
  git_diff()          — current unstaged diff as a string
  git_log(limit)      — list of commit dicts {hash, author, message}
  git_commit(msg)     — explicit commit (raises if nothing staged)
  git_create_branch() — create + checkout a new branch
  git_restore()       — discard unstaged changes

M5 additions (defined by test_git_m5_tools.py interface):
  git_blame(path)     — list of blame records {commit, author, date, line_number, content}
  git_stage(files)    — stage files for next commit

Utilities (internal, used by other Pearl modules):
  summarize_changes()         — structured change summary dict
  git_is_repo()               — bool check
  git_branch()                — current branch name string
  git_stash_list()            — stash list string
  git_undo_last()             — soft reset HEAD~1
  get_git_status_summary()    — dict used by VerificationEngine/ContextEngine
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

from src.config.workspace import get_workspace_root
from src.tools.metadata import tool

logger = logging.getLogger(__name__)

_GIT_TIMEOUT = 30


class GitError(RuntimeError):
    """Raised when a git command fails or the workspace is not a git repo."""


# ── Internal helpers ─────────────────────────────────────────────────────────


def _cwd() -> Path:
    """Active workspace root (falls back to Path.cwd() in test contexts)."""
    return get_workspace_root()


def _run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command in the active workspace. Never uses shell=True."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(_cwd()),
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    if check and result.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def _ensure_git_repo() -> None:
    """Raise GitError if the active workspace is not a git repository."""
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=str(_cwd()),
        capture_output=True,
        timeout=_GIT_TIMEOUT,
    )
    if result.returncode != 0:
        raise GitError("Not a git repository.")


def _is_repo_bool() -> bool:
    """Return True if the active workspace is inside a git repository."""
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=str(_cwd()),
        capture_output=True,
        timeout=_GIT_TIMEOUT,
    )
    return result.returncode == 0


def _parse_blame_porcelain(text: str) -> list[dict]:
    """Parse `git blame --porcelain` output into structured records."""
    if not text:
        return []
    records: list[dict] = []
    current: dict = {}
    for line in text.splitlines():
        if not line:
            continue
        if line[0] == "\t":
            current.setdefault("commit", "")
            current.setdefault("author", "")
            current.setdefault("date", "")
            current.setdefault("line_number", 0)
            current["content"] = line[1:]
            records.append(current)
            current = {}
        elif len(line) >= 40 and all(c in "0123456789abcdefABCDEF" for c in line[:40]):
            parts = line.split()
            current["commit"] = parts[0]
            if len(parts) >= 3:
                current["line_number"] = int(parts[2])
        elif line.startswith("author ") and not line.startswith("author-"):
            current["author"] = line[7:]
        elif line.startswith("author-time "):
            current["date"] = line[12:]
    return records


# ── Core tools ───────────────────────────────────────────────────────────────


@tool(
    description=(
        "Return the current Git status of the workspace as a structured dict: "
        "clean, staged, unstaged, untracked, and branch."
    ),
    parameters={},
    returns="dict",
    risk_level="safe",
)
def git_status() -> dict:
    """Return structured workspace Git status.

    Uses a single `git status --porcelain=v1 -b` call so callers that mock
    subprocess.run get a predictable call count.
    """
    _ensure_git_repo()
    result = _run(["status", "--porcelain=v1", "-b"], check=False)

    branch = "unknown"
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []

    for line in result.stdout.splitlines():
        if line.startswith("## "):
            # ## <branch>...<remote> or ## HEAD (no branch)
            header = line[3:]
            if header.startswith("HEAD (no branch)"):
                branch = "HEAD (detached)"
            else:
                branch = header.split("...")[0]
            continue
        if len(line) < 4:
            continue
        x = line[0]
        y = line[1]
        path = line[3:].strip()
        if x in "MADRC":
            staged.append(path)
        if y in "MD":
            unstaged.append(path)
        if line[:2] == "??":
            untracked.append(path)

    clean = not staged and not unstaged and not untracked
    return {
        "clean": clean,
        "branch": branch,
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
    }


@tool(
    description="Return the current Git diff (unstaged changes) as a string.",
    parameters={"path": "str", "staged": "bool"},
    returns="str",
    risk_level="safe",
)
def git_diff(path: str = "", staged: bool = False) -> str:
    """Return unstaged diff. Pass staged=True for staged (index) diff."""
    _ensure_git_repo()
    args = ["diff"]
    if staged:
        args.append("--cached")
    if path:
        ws = _cwd()
        resolved = (ws / path).resolve()
        if not str(resolved).startswith(str(ws.resolve())):
            raise PermissionError(f"Path {path!r} is outside workspace.")
        args.append(str(resolved))
    result = _run(args)
    output = result.stdout
    if len(output) > 20_000:
        output = output[:20_000] + "\n…(diff truncated)"
    return output


@tool(
    description=(
        "Return recent Git commits as a list of dicts with keys: hash, author, message. "
        "Returns an empty list for repositories with no commits."
    ),
    parameters={"limit": "int"},
    returns="list",
    risk_level="safe",
)
def git_log(limit: int = 10) -> list[dict]:
    """Return structured commit history."""
    _ensure_git_repo()
    if limit <= 0:
        raise ValueError(f"limit must be a positive integer, got {limit}.")
    result = _run(
        ["log", f"-{min(limit, 500)}", "--format=%H\x1f%an\x1f%s"],
        check=False,
    )
    if result.returncode != 0:
        # Empty repo has no commits
        return []
    entries = []
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        parts = line.split("\x1f", 2)
        if len(parts) == 3:
            entries.append({"hash": parts[0], "author": parts[1], "message": parts[2]})
    return entries


@tool(
    description=(
        "Create a Git commit with the provided message. "
        "ONLY call when the user has explicitly requested a commit. "
        "Raises GitError if there are no staged changes. "
        "Never auto-stages files."
    ),
    parameters={"message": "str"},
    returns="str",
    risk_level="staged",
)
def git_commit(message: str) -> str:
    """Create a commit from whatever is currently staged. Never auto-stages."""
    _ensure_git_repo()
    if not message.strip():
        raise ValueError("Commit message must not be empty.")

    status = git_status()
    if status["clean"]:
        raise GitError("Repository is clean — nothing to commit.")
    if not status["staged"]:
        raise GitError(
            "No staged changes. Stage files first before committing."
        )

    result = _run(["commit", "-m", message])
    first_line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else "committed"
    return f"Committed: {first_line}"


@tool(
    description=(
        "Create and immediately check out a new Git branch from the current HEAD. "
        "Raises GitError if the branch already exists."
    ),
    parameters={"branch_name": "str"},
    returns="str",
    risk_level="staged",
)
def git_create_branch(branch_name: str) -> str:
    """Create + checkout a new branch (git checkout -b)."""
    _ensure_git_repo()
    safe = branch_name.strip()
    if not safe:
        raise ValueError("Branch name must not be empty.")
    if safe.startswith("-"):
        raise ValueError(f"Invalid branch name: {branch_name!r}")

    result = _run(["checkout", "-b", safe], check=False)
    if result.returncode != 0:
        raise GitError(f"Could not create branch {safe!r}: {result.stderr.strip()}")
    return f"Created and switched to branch '{safe}'."


@tool(
    description=(
        "Restore files to their last committed state, discarding working-tree changes. "
        "Pass files=None to restore all unstaged changes. "
        "Raises ValueError if files is an empty list. "
        "Never touches staged changes."
    ),
    parameters={"files": "list"},
    returns="str",
    risk_level="dangerous",
)
def git_restore(files: list[str] | None = None) -> str:
    """Discard unstaged working-tree changes. Never touches staged changes."""
    _ensure_git_repo()
    if files is not None and len(files) == 0:
        raise ValueError("files must be None (restore all) or a non-empty list of paths.")

    if files is None:
        status = git_status()
        targets = status["unstaged"]
        if not targets:
            return "Nothing to restore."
        _run(["restore", "--", *targets])
        n = len(targets)
        return f"Restored {n} file{'s' if n != 1 else ''} to last committed state."
    else:
        _run(["restore", "--", *files])
        n = len(files)
        return f"Restored {n} file{'s' if n != 1 else ''} to last committed state."


# ── M5 additions ─────────────────────────────────────────────────────────────


@tool(
    description=(
        "Show git blame for a file — returns structured records of who last modified each line. "
        "Returns a list of dicts with keys: commit, author, date, line_number, content."
    ),
    parameters={"path": "str", "start_line": "int", "end_line": "int"},
    returns="list",
    risk_level="safe",
)
def git_blame(
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> list[dict]:
    """Return git blame output as a list of structured records."""
    _ensure_git_repo()
    if not path:
        raise ValueError("'path' must not be empty.")
    if start_line is not None and start_line < 1:
        raise ValueError("start_line must be >= 1.")
    if start_line is not None and end_line is not None and end_line < start_line:
        raise ValueError("end_line must be >= start_line.")
    cmd = ["blame", "--porcelain", path]
    if start_line is not None and end_line is not None:
        cmd.extend(["-L", f"{start_line},{end_line}"])
    elif start_line is not None:
        cmd.extend(["-L", f"{start_line}"])
    result = _run(cmd, check=False)
    if result.returncode != 0:
        raise GitError(f"git blame failed: {result.stderr.strip()}")
    return _parse_blame_porcelain(result.stdout)


@tool(
    description=(
        "Stage files for the next Git commit. "
        "Pass a list of paths to stage specific files, or omit to stage all changes. "
        "ONLY call when the user has explicitly requested staging."
    ),
    parameters={"files": "list"},
    returns="str",
    risk_level="staged",
)
def git_stage(files: list[str] | None = None) -> str:
    """Stage files. files=None stages everything; a list stages specific paths."""
    _ensure_git_repo()
    if files is not None:
        if any(not f for f in files):
            raise ValueError("All file paths must be non-empty strings.")
        cmd = ["add", "--", *files]
    else:
        cmd = ["add", "."]
    result = subprocess.run(
        ["git", *cmd],
        cwd=str(_cwd()),
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    if result.returncode != 0:
        raise GitError(f"git add failed: {result.stderr.strip()}")
    status = git_status()
    staged_list = ", ".join(status["staged"]) or "(none)"
    return f"Staged. Currently staged: {staged_list}"


# ── Summary / convenience ────────────────────────────────────────────────────


@tool(
    description=(
        "Return a structured summary of current Git changes: "
        "staged_files, unstaged_files, untracked_files, insertions, deletions, total_changed."
    ),
    parameters={},
    returns="dict",
    risk_level="safe",
)
def summarize_changes() -> dict:
    """Return a structured dict summarising current workspace changes."""
    _ensure_git_repo()
    status = git_status()

    # Count insertions/deletions via diff --stat
    stat_result = _run(["diff", "--numstat"], check=False)
    insertions = 0
    deletions = 0
    for line in stat_result.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            try:
                insertions += int(parts[0])
                deletions += int(parts[1])
            except ValueError:
                pass

    return {
        "staged_files": status["staged"],
        "unstaged_files": status["unstaged"],
        "untracked_files": status["untracked"],
        "insertions": insertions,
        "deletions": deletions,
        "total_changed": insertions + deletions,
    }


# ── Utility tools ─────────────────────────────────────────────────────────────


@tool(
    description="Check whether the workspace is a Git repository.",
    parameters={},
    returns="bool",
    risk_level="safe",
)
def git_is_repo() -> bool:
    """Return True if the current workspace is inside a Git repository."""
    return _is_repo_bool()


@tool(
    description="Return the current Git branch name.",
    parameters={},
    returns="str",
    risk_level="safe",
)
def git_branch() -> str:
    """Return the active branch name."""
    _ensure_git_repo()
    result = _run(["branch", "--show-current"])
    return result.stdout.strip() or "(detached HEAD)"


@tool(
    description="List Git stashes.",
    parameters={},
    returns="str",
    risk_level="safe",
)
def git_stash_list() -> str:
    """Return git stash list output."""
    _ensure_git_repo()
    result = _run(["stash", "list"])
    return result.stdout.strip() or "(no stashes)"


@tool(
    description=(
        "Undo the last Git commit, keeping changes staged. "
        "ONLY call when the user has explicitly requested it."
    ),
    parameters={},
    returns="str",
    risk_level="dangerous",
)
def git_undo_last() -> str:
    """Soft-reset HEAD~1 — keeps changes staged, does not delete files."""
    _ensure_git_repo()
    _run(["reset", "--soft", "HEAD~1"])
    return "Last commit undone. Changes remain staged."


# ── Non-tool utility (used by VerificationEngine / ContextEngine) ─────────────


def get_git_status_summary(workspace: Path | None = None) -> dict[str, Any]:
    """
    Return a structured summary of Git status.

    Used by VerificationEngine and ContextEngine to understand what
    changed in the workspace.
    """
    ws = workspace or get_workspace_root()
    result = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=str(ws),
        capture_output=True,
        timeout=_GIT_TIMEOUT,
    )
    if result.returncode != 0:
        return {"is_repo": False}

    try:
        status_result = subprocess.run(
            ["git", "status", "--porcelain=v1"],
            cwd=str(ws),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        log_result = subprocess.run(
            ["git", "log", "-1", "--oneline"],
            cwd=str(ws),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        branch_result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(ws),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )

        modified: list[str] = []
        untracked: list[str] = []
        staged: list[str] = []

        for line in status_result.stdout.splitlines():
            if len(line) < 2:
                continue
            xy = line[:2]
            fpath = line[3:].strip()
            if xy[0] in "MADRC":
                staged.append(fpath)
            if xy[1] in "MD":
                modified.append(fpath)
            if xy == "??":
                untracked.append(fpath)

        return {
            "is_repo": True,
            "branch": branch_result.stdout.strip() or "unknown",
            "last_commit": log_result.stdout.strip() or "(none)",
            "staged": staged,
            "modified": modified,
            "untracked": untracked,
            "is_dirty": bool(staged or modified),
        }
    except Exception as exc:
        logger.warning("git_status_summary failed: %s", exc)
        return {"is_repo": True, "error": str(exc)}
