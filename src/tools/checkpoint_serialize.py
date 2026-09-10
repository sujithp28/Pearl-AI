"""
One definition of a checkpoint's wire shape, for every adapter.

These functions lived as private helpers inside `src/mcp/server.py`,
where only the MCP adapter could reach them. The HTTP adapter needed the
same shapes and could not import them, and copying them would have
created a second source of truth for keys a shipped client already
reads.

Both adapters import from here so the extension and the browser cannot
be served different field names for the same object.

Keys are camelCase because that is what the JSON-RPC protocol already
sends to the VS Code extension. Matching Python's snake_case here would
have meant either breaking that client or translating in one adapter and
not the other.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Import for annotations only. This module is pure serialization and
    # must not pull the checkpoint machinery in at runtime, which would
    # make it a cross-tool dependency rather than a shared helper.
    from src.tools.checkpoints import Checkpoint, RestoreReport


def checkpoint_to_dict(checkpoint: Checkpoint) -> dict[str, Any]:
    """
    Render one checkpoint for a client.

    `shortId` is carried explicitly rather than left for the client to
    slice, so every surface abbreviates a checkpoint the same way.
    """

    return {
        "id": checkpoint.id,
        "shortId": checkpoint.short_id,
        "label": checkpoint.label,
        "createdAt": checkpoint.created_at,
    }


def restore_report_to_dict(report: RestoreReport) -> dict[str, Any]:
    """
    Render what a restore did, or would do.

    `changedAnything` is computed here rather than left to the client
    because it gates the browser's confirmation prompt: a restore that
    only removes files still changed something, and a client checking
    `restored` alone would skip the prompt on the most destructive case.
    """

    return {
        "checkpointId": report.checkpoint_id,
        "restored": report.restored,
        "removed": report.removed,
        "changedAnything": report.changed_anything,
    }
