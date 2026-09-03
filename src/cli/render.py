"""
Terminal rendering for the Pearl CLI.

Kept separate from the command logic so the output format can be tested
without running an agent, and so a non-TTY (pipe, CI log) degrades to
plain text rather than emitting escape codes into a file.
"""
from __future__ import annotations

import os
import sys
from typing import Any

# ── Colour ───────────────────────────────────────────────────────────────────
#
# Enabled only for a real terminal.  Honours NO_COLOR (the de-facto standard)
# and FORCE_COLOR so output piped to a file or a CI log stays readable.


def _supports_colour() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stdout.isatty()


_COLOUR = _supports_colour()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOUR else text


def dim(t: str) -> str:
    return _c("2", t)


def bold(t: str) -> str:
    return _c("1", t)


def green(t: str) -> str:
    return _c("32", t)


def red(t: str) -> str:
    return _c("31", t)


def yellow(t: str) -> str:
    return _c("33", t)


def cyan(t: str) -> str:
    return _c("36", t)


def magenta(t: str) -> str:
    return _c("35", t)


# ── Symbols ──────────────────────────────────────────────────────────────────
#
# Unicode where the terminal can render it; ASCII otherwise. A Windows
# console in a legacy code page raises on box-drawing characters, which
# would crash the CLI purely for decoration.


def _unicode_ok() -> bool:
    encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
    return "utf" in encoding


_UNI = _unicode_ok()

TICK = "✓" if _UNI else "+"
CROSS = "✗" if _UNI else "x"
ARROW = "→" if _UNI else "->"
BULLET = "•" if _UNI else "-"
RULE = "─" if _UNI else "-"


def rule(width: int = 60) -> str:
    return dim(RULE * width)


def header(text: str) -> str:
    return f"\n{bold(text)}\n{rule()}"


# ── Stage banner ─────────────────────────────────────────────────────────────

_STAGE_LABELS: dict[str, str] = {
    "planning": "Planning",
    "executing": "Executing",
    "tool_started": "Running",
    "tool_completed": "Done",
    "replanning": "Replanning",
    "checkpoint_created": "Checkpoint saved",
    "awaiting_approval": "Approval required",
    "verifying": "Verifying",
    "reflecting": "Reflecting",
    "task_completed": "Complete",
    "rejected": "Rejected",
    "cancelled": "Cancelled",
}


def stage(name: str, detail: str = "") -> str:
    """Render one pipeline stage line."""
    label = _STAGE_LABELS.get(name, name.replace("_", " ").title())
    line = f"{cyan(ARROW)} {label}"
    if detail:
        line += dim(f" — {detail}")
    return line


# ── Diff ─────────────────────────────────────────────────────────────────────


def diff(text: str, max_lines: int = 200) -> str:
    """
    Colourise a unified diff.

    Truncated at `max_lines` so a large refactor cannot flood the
    terminal — the count of hidden lines is reported instead.
    """
    if not text.strip():
        return dim("(no textual diff)")

    lines = text.splitlines()
    shown, hidden = lines[:max_lines], len(lines) - max_lines

    out: list[str] = []
    for line in shown:
        if line.startswith("+++") or line.startswith("---"):
            out.append(bold(line))
        elif line.startswith("@@"):
            out.append(cyan(line))
        elif line.startswith("+"):
            out.append(green(line))
        elif line.startswith("-"):
            out.append(red(line))
        else:
            out.append(line)

    if hidden > 0:
        out.append(dim(f"... {hidden} more line(s) not shown"))
    return "\n".join(out)


# ── Report sections ──────────────────────────────────────────────────────────


def steps(step_list: list[Any]) -> str:
    if not step_list:
        return dim("(no steps executed)")
    out: list[str] = []
    for s in step_list:
        ok = getattr(s, "succeeded", False)
        mark = green(TICK) if ok else red(CROSS)
        name = getattr(s, "tool_name", "?")
        summary = (getattr(s, "summary", "") or "")[:100]
        out.append(f"  {mark} {bold(name)} {dim(summary)}")
    return "\n".join(out)


def verification(v: dict[str, Any] | None) -> str:
    """Render the verification block, or a clear 'not run' note."""
    if not v:
        return dim("  Verification: not run (no files changed)")

    status = v.get("status", "?")
    if status == "SUCCESS":
        badge = green(f"{TICK} {status}")
    elif status == "FAILED":
        badge = red(f"{CROSS} {status}")
    else:
        badge = yellow(f"! {status}")

    out = [f"  Verification: {badge}"]

    run = v.get("tests_run", 0)
    if run:
        passed, failed = v.get("tests_passed", 0), v.get("tests_failed", 0)
        colour = green if not failed else red
        out.append(f"    Tests: {colour(f'{passed}/{run} passed')}")
    else:
        out.append(dim("    Tests: none selected for these files"))

    if v.get("changed_files"):
        out.append(dim(f"    Changed: {', '.join(v['changed_files'][:5])}"))

    # Unexpected files are a correctness signal, not noise — a task that
    # modified something it never planned to touch is not a clean result.
    if v.get("unexpected_files"):
        out.append(
            "    " + yellow(f"Unexpected: {', '.join(v['unexpected_files'][:5])}")
        )
    return "\n".join(out)


def reflection(r: Any) -> str:
    """Render the reflection verdict — the agent's own judgement."""
    if r is None:
        return dim("  Reflection: not run")

    status = getattr(r, "status", "?")
    confidence = getattr(r, "confidence", 0.0)

    if status == "complete":
        badge = green(f"{TICK} complete")
    elif status == "blocked":
        badge = red(f"{CROSS} blocked")
    else:
        badge = yellow(f"! {status}")

    out = [f"  Reflection: {badge} {dim(f'({confidence:.0%} confidence)')}"]

    reason = getattr(r, "reason", "")
    if reason:
        out.append(dim(f"    {reason}"))

    missing = getattr(r, "missing_requirements", None) or []
    if missing:
        out.append("    " + yellow(f"Still needed: {', '.join(missing)}"))
    return "\n".join(out)


def report(rep: Any, verification_data: dict[str, Any] | None = None) -> str:
    """Render a full ExecutionReport."""
    ok = getattr(rep, "succeeded", False)
    reason = getattr(rep, "stop_reason", "?")
    step_list = getattr(rep, "steps", [])

    title = green(f"{TICK} Completed") if ok else red(f"{CROSS} {reason}")
    out = [
        header("Result"),
        f"  {title} {dim(f'({len(step_list)} step(s))')}",
    ]

    replans = getattr(rep, "replans_used", 0)
    if replans:
        out.append("  " + yellow(f"Replanned {replans} time(s)"))

    out.append("")
    out.append(steps(step_list))
    out.append("")
    out.append(verification(verification_data))
    out.append(reflection(getattr(rep, "llm_reflection", None)))
    return "\n".join(out)
