"""
Multi-file refactoring tools for Pearl (M4).

These tools complement the single-file symbol editor (symbol_editor.py)
with operations that span multiple files: atomic batch writes and
cross-file symbol rename.

All write operations route through the same preview/apply mechanism
(PatchManager) as every other editing tool, so changes are staged
and presented for user approval before reaching disk.
"""

from __future__ import annotations

import re
import logging
from pathlib import Path
from typing import Any

from src.config.workspace import get_workspace_root
from src.tools.edit_tools import get_active_patch_manager
from src.tools.file_tools import _ensure_within_workspace
from src.tools.metadata import tool

logger = logging.getLogger(__name__)

MAX_SEARCH_RESULTS = 200
MAX_FILES_PER_BATCH = 50


def _workspace_root() -> Path:
    """Return the active workspace root for the current thread."""
    return get_workspace_root()


def _python_files(root: Path) -> list[Path]:
    """Return all .py files under root, skipping common non-source dirs."""
    skip = {".git", "__pycache__", ".venv", "venv", "env", ".tox", "build", "dist"}
    results: list[Path] = []
    for p in root.rglob("*.py"):
        if not any(part in skip for part in p.parts):
            results.append(p)
    return results


# ---------------------------------------------------------------------------
# batch_write_files
# ---------------------------------------------------------------------------


@tool(
    description=(
        "Write multiple files atomically in a single operation. All paths are "
        "validated first — if any is invalid, nothing is written. In preview "
        "mode (autonomous runs) all files are staged in one PatchManager batch "
        "so they are reviewed together rather than one at a time."
    ),
    parameters={"files": "dict[str, str]"},
    returns="str",
)
def batch_write_files(files: dict[str, str]) -> str:
    """
    Write multiple files in one atomic preview batch.

    Parameters
    ----------
    files:
        Mapping of ``{relative_path: content}`` for each file to write.
        All paths must be within the workspace root.
    """

    if not files:
        raise ValueError("'files' must not be empty.")

    if len(files) > MAX_FILES_PER_BATCH:
        raise ValueError(
            f"Too many files in one batch ({len(files)} > {MAX_FILES_PER_BATCH}). "
            "Split into smaller operations."
        )

    # Validate all paths before touching anything — fail-fast, all-or-nothing.
    resolved: dict[Path, str] = {}
    for path_str, content in files.items():
        resolved[_ensure_within_workspace(path_str)] = content

    manager = get_active_patch_manager()

    if manager is not None:
        for file_path, content in resolved.items():
            original = (
                file_path.read_text(encoding="utf-8") if file_path.exists() else ""
            )
            manager.propose(str(file_path), original, content)

        logger.info(
            "batch_write_files: staged %d file(s) for preview.", len(resolved)
        )
        return f"Preview staged: {len(resolved)} file(s) queued for review."

    # Direct-write mode (human-in-the-loop CLI call).
    written: list[str] = []
    for file_path, content in resolved.items():
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        written.append(str(file_path))

    from src.tools.repo_tools import refresh_indexed_file

    for p in written:
        refresh_indexed_file(p)

    logger.info("batch_write_files: wrote %d file(s): %s", len(written), written)
    return f"Wrote {len(written)} file(s): {', '.join(written)}"


# ---------------------------------------------------------------------------
# rename_symbol
# ---------------------------------------------------------------------------


@tool(
    description=(
        "Rename a Python symbol (class, function, or variable) across all "
        "files in the workspace. Uses whole-word matching so 'Foo' in 'FooBar' "
        "is not replaced. All changes are staged together for review."
    ),
    parameters={"old_name": "str", "new_name": "str"},
    returns="str",
)
def rename_symbol(old_name: str, new_name: str) -> str:
    """
    Cross-file word-boundary rename for any Python identifier.

    Parameters
    ----------
    old_name:
        The identifier to rename (must be a valid Python identifier).
    new_name:
        The replacement identifier (must be a valid Python identifier).

    Returns a summary of which files were changed. All changes are
    routed through PatchManager so they appear in the patch preview.
    """

    if not old_name or not old_name.isidentifier():
        raise ValueError(f"'old_name' must be a valid Python identifier; got {old_name!r}.")

    if not new_name or not new_name.isidentifier():
        raise ValueError(f"'new_name' must be a valid Python identifier; got {new_name!r}.")

    if old_name == new_name:
        return "old_name and new_name are the same; nothing to do."

    pattern = re.compile(r"\b" + re.escape(old_name) + r"\b")
    root = _workspace_root()
    py_files = _python_files(root)

    if len(py_files) > MAX_SEARCH_RESULTS:
        py_files = py_files[:MAX_SEARCH_RESULTS]
        logger.warning(
            "rename_symbol: workspace has >%d Python files; capping scan.",
            MAX_SEARCH_RESULTS,
        )

    manager = get_active_patch_manager()
    changed: list[str] = []

    for fp in py_files:
        try:
            original = fp.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        if old_name not in original:
            continue

        updated = pattern.sub(new_name, original)

        if updated == original:
            continue

        rel = str(fp.relative_to(root))

        if manager is not None:
            manager.propose(str(fp), original, updated)
        else:
            fp.write_text(updated, encoding="utf-8")

            from src.tools.repo_tools import refresh_indexed_file

            refresh_indexed_file(str(fp))

        changed.append(rel)

    if not changed:
        return f"No occurrences of '{old_name}' found; nothing renamed."

    mode = "staged for review" if manager is not None else "written"
    logger.info(
        "rename_symbol: '%s' → '%s' in %d file(s): %s",
        old_name,
        new_name,
        len(changed),
        changed,
    )
    return (
        f"Renamed '{old_name}' → '{new_name}' in {len(changed)} file(s) "
        f"({mode}): {', '.join(changed)}"
    )
