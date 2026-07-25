"""
Template rendering helpers shared by the personality engine.

Personality strings (`personalities.py`) are plain text today, but
future entries may want `{placeholders}` (e.g. a file count, a tool
name). `render()` supports that without requiring every template to
supply every possible placeholder — a template with no placeholders
at all (the common case right now) round-trips unchanged.
"""

from __future__ import annotations


class _SafeDict(dict):
    """
    A dict that leaves an unmatched `{placeholder}` in the output
    literally instead of raising `KeyError` — a template author
    forgetting to pass a variable should never crash message
    formatting, just look slightly odd, which is easy to spot and fix.
    """

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render(template: str, **kwargs: object) -> str:
    """
    Substitute `{name}`-style placeholders in `template` from
    `kwargs`. Missing placeholders are left as-is rather than raising;
    extra, unused `kwargs` are silently ignored (both deliberate: this
    only ever formats short, human-facing status text, never anything
    where a silent formatting quirk would be unsafe).
    """

    if "{" not in template:
        return template

    return template.format_map(_SafeDict(**kwargs))
