"""
Shared block-boundary helpers for the regex-based parsers.

Every non-Python parser needs to answer the same question — where does
the construct starting on this line end — and there are only two shapes
of answer across the languages Pearl supports: brace-delimited (C, C++,
C#, Java, Go, Rust, Swift, Kotlin, JS/TS, PHP) and keyword-delimited
(Ruby's `end`). Both live here rather than being copied per parser,
which is how `js_ts_parser` and `java_parser` had already started to
drift apart.

These are estimates. A regex parser cannot know that a brace sits inside
a string literal or a comment, so a pathological file yields a wrong end
line — never an exception. Being approximately right on every file
matters more here than being exactly right on some and failing on the
rest, because the result feeds context selection, not codegen.
"""
from __future__ import annotations

# Cap the scan so one unbalanced brace cannot walk the rest of a large
# file looking for a close that never comes.
DEFAULT_MAX_SCAN = 300


def estimate_brace_block_end(
    lines: list[str],
    start_idx: int,
    max_scan: int = DEFAULT_MAX_SCAN,
) -> int:
    """
    Return the 1-indexed last line of a brace-delimited block that starts
    at `start_idx` (0-indexed).

    Counts braces rather than matching them, so a declaration whose body
    opens on the following line works as well as one that opens inline.
    """
    if start_idx >= len(lines):
        return start_idx + 1

    depth = 0
    seen_open = False
    limit = min(start_idx + max_scan, len(lines))

    for i in range(start_idx, limit):
        line = lines[i]
        depth += line.count("{") - line.count("}")

        if "{" in line:
            seen_open = True

        # Only a block that actually opened can close. Without this a
        # one-line declaration with no body (an interface method, a
        # forward declaration) would consume everything after it.
        if seen_open and depth <= 0:
            return i + 1

    # No body found on this line — treat it as a single-line declaration
    # rather than claiming it spans the whole scan window.
    if not seen_open:
        return start_idx + 1

    return limit


def estimate_keyword_block_end(
    lines: list[str],
    start_idx: int,
    openers: tuple[str, ...],
    closer: str = "end",
    max_scan: int = DEFAULT_MAX_SCAN,
) -> int:
    """
    Return the 1-indexed last line of a keyword-delimited block (Ruby).

    Tracks nesting by counting lines that open a new block against lines
    that are exactly the closing keyword, so nested defs inside a class
    do not end the outer block early.
    """
    if start_idx >= len(lines):
        return start_idx + 1

    depth = 0
    limit = min(start_idx + max_scan, len(lines))

    for i in range(start_idx, limit):
        stripped = lines[i].strip()

        if i == start_idx:
            depth = 1
            continue

        # A trailing-`end` one-liner ("def x; 1; end") opens and closes on
        # the same line, so check the closer before the opener.
        if stripped == closer or stripped.startswith(f"{closer} "):
            depth -= 1
            if depth <= 0:
                return i + 1
            continue

        if any(
            stripped == kw or stripped.startswith(f"{kw} ") for kw in openers
        ):
            depth += 1

    return limit
