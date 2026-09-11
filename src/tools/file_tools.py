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


def _active_patch_manager():
    """
    Return the active ChangeManager, or None when not in preview mode.

    Imported lazily for the same reason as `_refresh_repo_index`:
    `edit_tools` imports this module for path validation, so a
    top-level import here would be circular.
    """

    from src.tools.edit_tools import get_active_patch_manager

    return get_active_patch_manager()


def _read_for_staging(file_path: Path) -> str | None:
    """
    Return the current text of `file_path`, or None when it does not
    exist yet — the `original_content` a staged edit needs to render a
    diff. Unreadable bytes stage as a new file rather than failing the
    run: a diff that shows the whole content is a worse preview than
    no baseline, but refusing the edit outright is worse still.
    """

    if not file_path.exists():
        return None

    try:
        return file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        logger.warning(
            "Could not read %s for a staged diff; staging as new content.",
            file_path,
        )
        return None


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
    returns="None | str",
    risk_level="staged",
)
def write_file(path: str, content: str) -> None | str:
    """
    Write text to a UTF-8 file.

    In preview mode (an active ChangeManager) the write is staged for
    approval and a preview string is returned instead of touching disk.
    """

    file_path = _ensure_within_workspace(path)

    manager = _active_patch_manager()

    if manager is not None:
        logger.info("Previewing write_file: %s", file_path)

        original = _read_for_staging(file_path)
        manager.propose(str(file_path), original, content)

        verb = "create" if original is None else "overwrite"
        lines = len(content.splitlines())

        return f"Preview staged: {verb} '{path}' ({lines} line(s))."

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
    returns="None | str",
    risk_level="staged",
)
def append_file(path: str, content: str) -> None | str:
    """
    Append text to a file.

    In preview mode (an active ChangeManager) the append is staged for
    approval and a preview string is returned instead of touching disk.
    """

    file_path = _ensure_within_workspace(path)

    manager = _active_patch_manager()

    if manager is not None:
        logger.info("Previewing append_file: %s", file_path)

        original = _read_for_staging(file_path)
        manager.propose(str(file_path), original, (original or "") + content)

        lines = len(content.splitlines())

        return f"Preview staged: append to '{path}' ({lines} line(s))."

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
    risk_level="staged",
)
def make_directory(path: str) -> None:
    """
    Create a directory.

    Deliberately not staged for approval, unlike the write/delete tools
    in this module. A ChangeManager edit is one file's before-and-after
    text, and a directory has neither; representing it would mean a
    content-free pseudo-edit that renders as an empty diff. Creating an
    empty directory also destroys nothing, and every file placed inside
    it still goes through the approval gate — so the worst outcome of
    a rejected run is an unused empty directory.
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
    returns="None | str",
    risk_level="dangerous",
)
def delete_file(path: str) -> None | str:
    """
    Delete a file.

    In preview mode (an active ChangeManager) the removal is staged for
    approval and a preview string is returned instead of touching disk.
    """

    file_path = _ensure_within_workspace(path)

    if not file_path.exists():
        raise FileNotFoundError(path)

    manager = _active_patch_manager()

    if manager is not None:
        logger.info("Previewing delete_file: %s", file_path)

        manager.propose_deletion(str(file_path), _read_for_staging(file_path))

        return f"Preview staged: delete '{path}'."

    file_path.unlink()
    _refresh_repo_index(file_path)

    logger.info("Deleted file: %s", file_path)


@tool(
    description="Return the size of a file in bytes.",
    parameters={
        "path": "str",
    },
    returns="int",
    risk_level="safe",
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
    risk_level="safe",
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
    risk_level="dangerous",
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
    risk_level="dangerous",
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
