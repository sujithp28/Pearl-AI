"""
Byte-faithful text I/O for Pearl's workspace file tools.

Python's default text mode translates newlines in both directions:
reading turns ``\r\n`` into ``\n``, and writing turns ``\n`` into
``os.linesep``.  That round trip silently rewrites every line ending
in a file Pearl only meant to edit a few lines of — LF files become
CRLF on Windows, CRLF files become LF on POSIX — producing a diff the
user never asked for and a whole-file change in version control.

``read_text``/``write_text`` here disable both translations
(``newline=""``), so a string read from a file and written back is
identical to what was on disk.  Every write path that touches the
workspace uses these instead of ``Path.read_text``/``Path.write_text``.
"""

from __future__ import annotations

from pathlib import Path


def read_text(path: Path | str, encoding: str = "utf-8") -> str:
    """
    Read `path` as text without translating line endings.
    """

    with open(path, encoding=encoding, newline="") as handle:
        return handle.read()


def write_text(path: Path | str, content: str, encoding: str = "utf-8") -> None:
    """
    Write `content` to `path` verbatim, without translating line endings.
    """

    with open(path, "w", encoding=encoding, newline="") as handle:
        handle.write(content)


def append_text(path: Path | str, content: str, encoding: str = "utf-8") -> None:
    """
    Append `content` to `path` verbatim, without translating line endings.
    """

    with open(path, "a", encoding=encoding, newline="") as handle:
        handle.write(content)


def dominant_newline(text: str) -> str:
    """
    Return the line ending `text` predominantly uses: ``"\r\n"`` when
    CRLF endings outnumber bare LF ones, ``"\n"`` otherwise (including
    for text with no line endings at all).

    Used when a tool has to rebuild a file from ``splitlines()`` output,
    which discards the endings it split on.
    """

    crlf = text.count("\r\n")

    if crlf and crlf >= text.count("\n") - crlf:
        return "\r\n"

    return "\n"
