"""
Automatic serious-mode detection.

Humor is disabled — forced to `Personality.PROFESSIONAL` — for a
fixed set of situations, regardless of the configured personality.
This is deliberately a closed, explicit set rather than a heuristic
scan of free text: guessing "does this message sound serious" from
content is unreliable and would be exactly the kind of expensive,
unpredictable processing the formatter is required to avoid (see
`formatter.py`'s performance note). Callers that know they're in one
of these situations say so explicitly, either via `EventKind.SECURITY`
or an explicit `serious=True` on `PersonalityManager.format()`.
"""

from __future__ import annotations

from src.personality.emoji import EventKind

# Event kinds that are inherently serious regardless of context.
# `EventKind.SECURITY` covers "running a security check" narration
# broadly; an explicit `serious=True` argument covers the rest of the
# spec's list (data loss, production outages, destructive operations,
# authentication failures, privacy events, financial operations,
# infrastructure/backup failures) — those aren't tied to any one
# `EventKind`, so they're not enumerable here the same way.
ALWAYS_SERIOUS_EVENT_KINDS = frozenset({EventKind.SECURITY})


def is_serious(event_kind: EventKind, *, serious: bool = False) -> bool:
    """
    Return whether serious mode should override the configured
    personality for this message.
    """

    return serious or event_kind in ALWAYS_SERIOUS_EVENT_KINDS
