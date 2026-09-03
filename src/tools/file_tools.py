"""
File system tools for Pearl.

These tools provide safe, UTF-8 based file operations.
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.config.workspace import get_workspace_root
from src.tools.metadata import tool

logger = logging.getLogger(__name__)


def _ensure_within_workspace(path: str) -> Path:
    """
    Resolve `path` and ensure it stays inside the active workspace root.

    Prevents path traversal ("..") and symlink escapes outside the
    workspace.  Uses the thread-local workspace root set by the session,
    falling back to ``Path.cwd()`` in non-session contexts.

    Fails closed: any exception during resolution (e.g. embedded null bytes)
    is re-raised as ``PermissionError`` so callers never accidentally proceed
    on an un-validated path.
    """
    workspace_root = get_workspace_root()
    try:
        resolved = Path(path).resolve()
    except Exception as exc:
        raise PermissionError(f"Cannot resolve path {path!r}: {exc}") from exc

    if not resolved.is_relative_to(workspace_root):
        raise PermissionError(f"Path escapes workspace: {path}")

    return resolved


def _refresh_repo_index(file_path: Path) -> None:
    """
    Best-effort refresh of the repository index's cached entry for
    this file, after a write/delete here.

    Imported lazily: `repo_tools` imports this module for workspace
    path validation, so a top-level import here would be circular.
    """

    from src.tools.repo_tools import refresh_indexed_file

    refresh_indexed_file(str(file_path))


@tool(
    description="Read the contents of a UTF-8 text file.",
    parameters={
        "path": "str",
    },
    returns="str",
    risk_level="safe",
)
def read_file(path: str) -> str:
    """
    Read a UTF-8 text file.
    """

    file_path = _ensure_within_workspace(path)

    if not file_path.exists():
        raise FileNotFoundError(path)

    if not file_path.is_file():
        if file_path.is_dir():
            # Gracefully handle the common planner mistake of calling
            # read_file on a directory: return a listing instead of crashing.
            contents = sorted(item.name for item in file_path.iterdir())
            return f"[directory: {path}]\n" + "\n".join(contents)
        raise IsADirectoryError(path)

    logger.info("Reading file: %s", file_path)

    return file_path.read_text(encoding="utf-8")


@tool(
    description="Write text to a UTF-8 file. Creates the file if necessary.",
    parameters={
        "path": "str",
        "content": "str",
    },
    returns="None",
)
def write_file(path: str, content: str) -> None:
    """
    Write text to a UTF-8 file.
    """

    file_path = _ensure_within_workspace(path)

    if file_path.parent:
        file_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Writing file: %s", file_path)

    file_path.write_text(
        content,
        encoding="utf-8",
    )
    _refresh_repo_index(file_path)


@tool(
    description="Append text to a UTF-8 file.",
    parameters={
        "path": "str",
        "content": "str",
    },
    returns="None",
)
def append_file(path: str, content: str) -> None:
    """
    Append text to a file.
    """

    file_path = _ensure_within_workspace(path)

    if file_path.parent:
        file_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Appending file: %s", file_path)

    with file_path.open(
        "a",
        encoding="utf-8",
    ) as file:
        file.write(content)

    _refresh_repo_index(file_path)


@tool(
    description="Return whether a file exists.",
    parameters={
        "path": "str",
    },
    returns="bool",
    risk_level="safe",
)
def file_exists(path: str) -> bool:
    """
    Check if a file exists.
    """

    try:
        file_path = _ensure_within_workspace(path)
    except PermissionError:
        return False

    return file_path.exists()


@tool(
    description="List all files and directories inside a directory.",
    parameters={
        "path": "str",
    },
    returns="list[str]",
    risk_level="safe",
)
def list_directory(path: str = ".") -> list[str]:
    """
    List directory contents.
    """

    directory = _ensure_within_workspace(path)

    if not directory.exists():
        raise FileNotFoundError(path)

    if not directory.is_dir():
        raise NotADirectoryError(path)

    logger.info("Listing directory: %s", directory)

    return sorted(item.name for item in directory.iterdir())


@tool(
    description="Create a directory recursively if it does not exist.",
    parameters={
        "path": "str",
    },
    returns="None",
)
def make_directory(path: str) -> None:
    """
    Create a directory.
    """

    directory = _ensure_within_workspace(path)

    directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    logger.info("Created directory: %s", directory)


@tool(
    description="Delete a file.",
    parameters={
        "path": "str",
    },
    returns="None",
    risk_level="dangerous",
)
def delete_file(path: str) -> None:
    """
    Delete a file.
    """

    file_path = _ensure_within_workspace(path)

    if not file_path.exists():
        raise FileNotFoundError(path)

    file_path.unlink()
    _refresh_repo_index(file_path)

    logger.info("Deleted file: %s", file_path)


@tool(
    description="Return the size of a file in bytes.",
    parameters={
        "path": "str",
    },
    returns="int",
)
def file_size(path: str) -> int:
    """
    Return file size.
    """

    file_path = _ensure_within_workspace(path)

    if not file_path.exists():
        raise FileNotFoundError(path)

    return file_path.stat().st_size


@tool(
    description=(
        "Show a unified diff between two files in the workspace. "
        "Returns an empty string when the files are identical."
    ),
    parameters={
        "path_a": "str",
        "path_b": "str",
    },
    returns="str",
)
def diff_files(path_a: str, path_b: str) -> str:
    file_a = _ensure_within_workspace(path_a)
    file_b = _ensure_within_workspace(path_b)

    if not file_a.exists():
        raise FileNotFoundError(path_a)
    if not file_b.exists():
        raise FileNotFoundError(path_b)

    import difflib

    lines_a = file_a.read_text(encoding="utf-8").splitlines(keepends=True)
    lines_b = file_b.read_text(encoding="utf-8").splitlines(keepends=True)

    diff = difflib.unified_diff(
        lines_a,
        lines_b,
        fromfile=f"a/{path_a}",
        tofile=f"b/{path_b}",
    )

    return "".join(diff)


@tool(
    description="Rename or move a file to a new path within the workspace.",
    parameters={
        "source": "str",
        "destination": "str",
    },
    returns="None",
)
def rename_file(source: str, destination: str) -> None:
    src = _ensure_within_workspace(source)
    dst = _ensure_within_workspace(destination)

    if not src.exists():
        raise FileNotFoundError(source)
    if dst.exists():
        raise FileExistsError(destination)

    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)

    _refresh_repo_index(src)
    _refresh_repo_index(dst)

    logger.info("Renamed %s → %s", src, dst)


@tool(
    description="Copy a file to a new path within the workspace.",
    parameters={
        "source": "str",
        "destination": "str",
    },
    returns="None",
)
def copy_file(source: str, destination: str) -> None:
    src = _ensure_within_workspace(source)
    dst = _ensure_within_workspace(destination)

    if not src.exists():
        raise FileNotFoundError(source)
    if dst.exists():
        raise FileExistsError(destination)

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())

    _refresh_repo_index(dst)

    logger.info("Copied %s → %s", src, dst)
