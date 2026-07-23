"""
Repository intelligence tools for Pearl.

These tools index Python source files in the workspace and let Pearl
locate symbol definitions, find textual references to a symbol,
run general text search, and summarize the project layout.
"""

from __future__ import annotations

import ast
import logging
import re
from pathlib import Path
from typing import Any

from src.tools.file_tools import _ensure_within_workspace
from src.tools.metadata import tool

logger = logging.getLogger(__name__)

SOURCE_EXTENSIONS = (".py",)

IGNORED_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
}


def _ensure_workspace_dir(path: str) -> Path:
    """
    Resolve `path` within the workspace and ensure it is a directory.
    """

    root = _ensure_within_workspace(path)

    if not root.exists():
        raise FileNotFoundError(path)

    if not root.is_dir():
        raise NotADirectoryError(path)

    return root


def _iter_files(
    root: Path,
    extensions: tuple[str, ...] | None,
) -> list[Path]:
    """
    Recursively list files under `root`, skipping noisy directories.
    """

    files: list[Path] = []

    for item in sorted(root.rglob("*")):
        if not item.is_file():
            continue

        if any(
            part in IGNORED_DIRS
            for part in item.relative_to(root).parts
        ):
            continue

        if extensions is not None and item.suffix not in extensions:
            continue

        files.append(item)

    return files


def _index_symbols(root: Path) -> dict[str, list[dict[str, Any]]]:
    """
    Build a symbol index (name -> definition sites) for every Python
    file under `root`.
    """

    index: dict[str, list[dict[str, Any]]] = {}

    for file_path in _iter_files(root, SOURCE_EXTENSIONS):
        try:
            source = file_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(file_path))
        except (SyntaxError, UnicodeDecodeError) as exc:
            logger.warning(
                "Skipping unparsable file %s: %s", file_path, exc
            )
            continue

        for node in ast.walk(tree):
            if isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            ):
                kind = (
                    "class"
                    if isinstance(node, ast.ClassDef)
                    else "function"
                )

                index.setdefault(node.name, []).append(
                    {
                        "file": str(file_path.relative_to(root)),
                        "line": node.lineno,
                        "type": kind,
                    }
                )

    return index


def _search(
    root: Path,
    pattern: re.Pattern[str],
    extensions: tuple[str, ...] | None,
) -> list[dict[str, Any]]:
    """
    Search files under `root` for lines matching `pattern`.
    """

    matches: list[dict[str, Any]] = []

    for file_path in _iter_files(root, extensions):
        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue

        for line_number, line in enumerate(lines, start=1):
            if pattern.search(line):
                matches.append(
                    {
                        "file": str(file_path.relative_to(root)),
                        "line": line_number,
                        "text": line.strip(),
                    }
                )

    return matches


@tool(
    description=(
        "Index Python symbols (functions and classes) under a "
        "directory and return a summary of the index."
    ),
    parameters={
        "path": "str",
    },
    returns="dict",
)
def index_repository(path: str = ".") -> dict[str, Any]:
    """
    Build a symbol index for `path` and return summary counts.
    """

    root = _ensure_workspace_dir(path)

    logger.info("Indexing repository: %s", root)

    files = _iter_files(root, SOURCE_EXTENSIONS)
    index = _index_symbols(root)

    symbols_found = sum(len(locations) for locations in index.values())

    return {
        "root": str(root),
        "files_indexed": len(files),
        "unique_symbols": len(index),
        "symbols_found": symbols_found,
    }


@tool(
    description=(
        "Find where a symbol (function or class) is defined in the "
        "workspace."
    ),
    parameters={
        "name": "str",
        "path": "str",
    },
    returns="list[dict]",
)
def find_symbol(name: str, path: str = ".") -> list[dict[str, Any]]:
    """
    Return every definition site for `name`.
    """

    if not name:
        raise ValueError("`name` must not be empty.")

    root = _ensure_workspace_dir(path)

    logger.info("Finding symbol: %s", name)

    return _index_symbols(root).get(name, [])


@tool(
    description=(
        "Find every textual reference to a symbol across the "
        "workspace's source files."
    ),
    parameters={
        "symbol": "str",
        "path": "str",
    },
    returns="list[dict]",
)
def find_references(
    symbol: str,
    path: str = ".",
) -> list[dict[str, Any]]:
    """
    Return every source line that mentions `symbol` as a whole word.
    """

    if not symbol:
        raise ValueError("`symbol` must not be empty.")

    root = _ensure_workspace_dir(path)

    logger.info("Finding references to: %s", symbol)

    pattern = re.compile(rf"\b{re.escape(symbol)}\b")

    return _search(root, pattern, SOURCE_EXTENSIONS)


@tool(
    description="Search all workspace files for lines matching a text query.",
    parameters={
        "query": "str",
        "path": "str",
    },
    returns="list[dict]",
)
def search_text(query: str, path: str = ".") -> list[dict[str, Any]]:
    """
    Return every line containing `query` (plain substring match)
    across all files under `path`.
    """

    if not query:
        raise ValueError("`query` must not be empty.")

    root = _ensure_workspace_dir(path)

    logger.info("Searching text: %s", query)

    pattern = re.compile(re.escape(query))

    return _search(root, pattern, None)


@tool(
    description=(
        "Summarize the project: file counts, symbol counts, and "
        "top-level layout."
    ),
    parameters={
        "path": "str",
    },
    returns="dict",
)
def summarize_project(path: str = ".") -> dict[str, Any]:
    """
    Return a high-level summary of the project rooted at `path`.
    """

    root = _ensure_workspace_dir(path)

    logger.info("Summarizing project: %s", root)

    all_files = _iter_files(root, None)
    source_files = [
        file_path
        for file_path in all_files
        if file_path.suffix in SOURCE_EXTENSIONS
    ]
    index = _index_symbols(root)

    files_by_extension: dict[str, int] = {}

    for file_path in all_files:
        suffix = file_path.suffix or "(no extension)"
        files_by_extension[suffix] = files_by_extension.get(suffix, 0) + 1

    total_lines = 0

    for file_path in source_files:
        try:
            total_lines += len(
                file_path.read_text(encoding="utf-8").splitlines()
            )
        except UnicodeDecodeError:
            continue

    functions = sum(
        1
        for locations in index.values()
        for location in locations
        if location["type"] == "function"
    )

    classes = sum(
        1
        for locations in index.values()
        for location in locations
        if location["type"] == "class"
    )

    return {
        "root": str(root),
        "total_files": len(all_files),
        "source_files": len(source_files),
        "total_lines": total_lines,
        "files_by_extension": files_by_extension,
        "unique_symbols": len(index),
        "functions": functions,
        "classes": classes,
        "top_level_entries": sorted(
            item.name for item in root.iterdir()
        ),
    }
