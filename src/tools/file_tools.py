"""
File system tools for Pearl.

These tools provide safe, UTF-8 based file operations.
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.tools.metadata import tool

logger = logging.getLogger(__name__)


def _ensure_within_workspace(path: str) -> Path:
    """
    Resolve `path` and ensure it stays inside the current workspace root.

    Prevents path traversal ("..") and symlink escapes outside the
    current working directory.
    """

    workspace_root = Path.cwd().resolve()
    resolved = Path(path).resolve()

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
)
def read_file(path: str) -> str:
    """
    Read a UTF-8 text file.
    """

    file_path = _ensure_within_workspace(path)

    if not file_path.exists():
        raise FileNotFoundError(path)

    if not file_path.is_file():
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
