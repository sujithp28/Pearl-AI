"""
Code editing tools for Pearl.

These tools create and modify source files: whole-file creation,
search/replace editing, line-based editing, and unified-diff
patching. All writes stay within the current workspace and touch
only what changed, preserving the rest of the file's formatting.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from src.tools.file_tools import _ensure_within_workspace
from src.tools.metadata import tool

logger = logging.getLogger(__name__)

_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@")


def _read_lines(file_path: Path) -> tuple[list[str], bool]:
    """
    Read `file_path` as lines, plus whether it ends with a newline.
    """

    text = file_path.read_text(encoding="utf-8")
    trailing_newline = text == "" or text.endswith("\n")

    return text.splitlines(), trailing_newline


def _write_lines(
    file_path: Path,
    lines: list[str],
    trailing_newline: bool,
) -> None:
    """
    Write `lines` back to `file_path`, restoring the trailing newline.
    """

    content = "\n".join(lines)

    if trailing_newline and lines:
        content += "\n"

    file_path.write_text(content, encoding="utf-8")


def _parse_hunks(patch: str) -> list[dict[str, Any]]:
    """
    Parse a unified diff into a list of hunks.
    """

    hunks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in patch.splitlines():
        match = _HUNK_HEADER_RE.match(line)

        if match:
            if current is not None:
                hunks.append(current)

            current = {"old_start": int(match.group(1)), "lines": []}
            continue

        if current is None:
            # Skip file header lines ("---"/"+++") before the first hunk.
            continue

        if line.startswith("\\"):
            # "\ No newline at end of file" marker.
            continue

        current["lines"].append(line)

    if current is not None:
        hunks.append(current)

    if not hunks:
        raise ValueError("No valid diff hunks found in patch.")

    return hunks


def _apply_hunks(
    original_lines: list[str],
    hunks: list[dict[str, Any]],
) -> list[str]:
    """
    Apply parsed hunks to `original_lines`, returning the result.
    """

    result = list(original_lines)
    offset = 0

    for hunk in hunks:
        old_segment: list[str] = []
        new_segment: list[str] = []

        for line in hunk["lines"]:
            tag, content = (line[0], line[1:]) if line else (" ", "")

            if tag == " ":
                old_segment.append(content)
                new_segment.append(content)
            elif tag == "-":
                old_segment.append(content)
            elif tag == "+":
                new_segment.append(content)
            else:
                raise ValueError(f"Invalid diff line: {line!r}")

        start = hunk["old_start"] - 1 + offset
        end = start + len(old_segment)

        if result[start:end] != old_segment:
            raise ValueError(
                "Patch does not apply: context mismatch near "
                f"line {hunk['old_start']}."
            )

        result[start:end] = new_segment
        offset += len(new_segment) - len(old_segment)

    return result


@tool(
    description=(
        "Create a new UTF-8 text file. Fails if the file already exists."
    ),
    parameters={
        "path": "str",
        "content": "str",
    },
    returns="None",
)
def create_file(path: str, content: str = "") -> None:
    """
    Create a new file. Raises if the file already exists.
    """

    file_path = _ensure_within_workspace(path)

    if file_path.exists():
        raise FileExistsError(path)

    if file_path.parent:
        file_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Creating file: %s", file_path)

    file_path.write_text(content, encoding="utf-8")


@tool(
    description=(
        "Replace occurrences of a search string with a replacement "
        "string in a file."
    ),
    parameters={
        "path": "str",
        "search": "str",
        "replacement": "str",
        "count": "int",
    },
    returns="int",
)
def replace_in_file(
    path: str,
    search: str,
    replacement: str,
    count: int = -1,
) -> int:
    """
    Replace occurrences of `search` with `replacement` in a file.

    Returns the number of replacements made.
    """

    if not search:
        raise ValueError("`search` must not be empty.")

    file_path = _ensure_within_workspace(path)

    if not file_path.exists():
        raise FileNotFoundError(path)

    text = file_path.read_text(encoding="utf-8")

    occurrences = text.count(search)

    if occurrences == 0:
        return 0

    replaced = occurrences if count < 0 else min(count, occurrences)

    logger.info(
        "Replacing %d occurrence(s) of %r in %s",
        replaced,
        search,
        file_path,
    )

    file_path.write_text(
        text.replace(search, replacement, count),
        encoding="utf-8",
    )

    return replaced


@tool(
    description=(
        "Replace a 1-indexed, inclusive range of lines in a file with "
        "new content."
    ),
    parameters={
        "path": "str",
        "start_line": "int",
        "end_line": "int",
        "new_content": "str",
    },
    returns="None",
)
def edit_lines(
    path: str,
    start_line: int,
    end_line: int,
    new_content: str,
) -> None:
    """
    Replace lines `start_line`-`end_line` (1-indexed, inclusive)
    with `new_content`.
    """

    file_path = _ensure_within_workspace(path)

    if not file_path.exists():
        raise FileNotFoundError(path)

    lines, trailing_newline = _read_lines(file_path)

    if start_line < 1 or end_line < start_line or end_line > len(lines):
        raise ValueError(
            f"Invalid line range {start_line}-{end_line} for a "
            f"{len(lines)}-line file."
        )

    replacement = new_content.splitlines() if new_content else []

    logger.info(
        "Editing lines %d-%d in %s", start_line, end_line, file_path
    )

    lines[start_line - 1 : end_line] = replacement

    _write_lines(file_path, lines, trailing_newline)


@tool(
    description="Apply a unified diff patch to a file.",
    parameters={
        "path": "str",
        "patch": "str",
    },
    returns="None",
)
def patch_file(path: str, patch: str) -> None:
    """
    Apply a unified diff `patch` to an existing file.
    """

    file_path = _ensure_within_workspace(path)

    if not file_path.exists():
        raise FileNotFoundError(path)

    lines, trailing_newline = _read_lines(file_path)

    hunks = _parse_hunks(patch)
    patched_lines = _apply_hunks(lines, hunks)

    logger.info("Patching file: %s", file_path)

    _write_lines(file_path, patched_lines, trailing_newline)
