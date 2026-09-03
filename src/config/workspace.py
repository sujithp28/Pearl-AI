"""Thread-local workspace root — explicit workspace propagation.

Replace ``Path.cwd()`` calls in tools with ``get_workspace_root()`` so
that concurrent Pearl sessions (one thread each) never bleed workspace
paths into one another.

Usage::

    # At the start of each autonomous run (in the session's thread):
    set_workspace_root(session.workspace)

    # Inside every tool that needs the workspace boundary:
    root = get_workspace_root()
"""

from __future__ import annotations

import threading
from pathlib import Path

_local = threading.local()


def set_workspace_root(path: Path) -> None:
    """Set the workspace root for the current thread."""
    _local.root = path.resolve()


def get_workspace_root() -> Path:
    """Return the workspace root for the current thread.

    Falls back to ``Path.cwd()`` in non-session contexts (tests, CLI entry
    points that never call ``set_workspace_root``).
    """
    root = getattr(_local, "root", None)
    if root is None:
        return Path.cwd().resolve()
    return root


def clear_workspace_root() -> None:
    """Clear the workspace root for the current thread.

    Useful in tests to reset between cases.
    """
    _local.__dict__.pop("root", None)
