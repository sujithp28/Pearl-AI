"""
`PersonalityManager` — the one object the rest of Pearl talks to.

Everything else in this package (`personalities.py`, `emoji.py`,
`formatter.py`, `serious_mode.py`, `templates.py`) is a pure data
table or pure function; this is the thin, stateful-only-in-the-
"which personality is configured" sense object that ties them
together behind one method: `format(event_kind, ...)`.
"""

from __future__ import annotations

import logging
import threading

from src.config.settings import Settings
from src.personality.emoji import EmojiMode, EventKind, resolve_emoji_mode
from src.personality.formatter import format_event
from src.personality.personalities import (
    FALLBACK_PERSONALITY,
    Personality,
    resolve_personality,
)
from src.personality.serious_mode import is_serious

logger = logging.getLogger(__name__)


class PersonalityManager:
    """
    Resolves the configured personality/emoji mode and formats
    Pearl's status/progress messages accordingly.

    Read-only after construction except via the explicit
    `set_personality`/`set_emoji_mode` calls (e.g. a future settings
    UI) — there's no background polling or file watching, which is
    what "no restart required if practical" means here: change it by
    calling the setter, not by editing `.env` and hoping.

    Thread-safe: `format()` only reads immutable module-level data
    tables plus the two current settings, guarded by a lock only for
    the (rare) setter calls; concurrent `format()` calls never
    contend with each other.
    """

    def __init__(
        self,
        personality: str | None = None,
        emoji_mode: str | None = None,
    ) -> None:
        self._lock = threading.Lock()

        requested_personality = (
            personality if personality is not None else Settings.PERSONALITY
        )
        requested_emoji_mode = (
            emoji_mode if emoji_mode is not None else Settings.EMOJI_MODE
        )

        self._personality = self._validate_personality(requested_personality)
        self._emoji_mode = self._validate_emoji_mode(requested_emoji_mode)

    @staticmethod
    def _validate_personality(value: str) -> Personality:
        resolved = resolve_personality(value)

        if resolved.value != value.strip().lower():
            logger.warning(
                "Unrecognized PERSONALITY %r; falling back to %r.",
                value,
                FALLBACK_PERSONALITY.value,
            )

        return resolved

    @staticmethod
    def _validate_emoji_mode(value: str) -> EmojiMode:
        resolved = resolve_emoji_mode(value)

        if resolved.value != value.strip().lower():
            logger.warning(
                "Unrecognized EMOJI_MODE %r; falling back to %r.",
                value,
                resolved.value,
            )

        return resolved

    @property
    def personality(self) -> Personality:
        return self._personality

    @property
    def emoji_mode(self) -> EmojiMode:
        return self._emoji_mode

    def set_personality(self, value: str) -> None:
        """
        Change the active personality at runtime — no restart
        required.
        """

        with self._lock:
            self._personality = self._validate_personality(value)

    def set_emoji_mode(self, value: str) -> None:
        """
        Change the active emoji mode at runtime — no restart required.
        """

        with self._lock:
            self._emoji_mode = self._validate_emoji_mode(value)

    def format(
        self,
        event_kind: EventKind,
        *,
        serious: bool = False,
        **template_vars: object,
    ) -> str:
        """
        Render the configured personality's line for `event_kind`.

        `serious`, or `event_kind` being one that's always serious
        (see `serious_mode.ALWAYS_SERIOUS_EVENT_KINDS`), forces
        professional wording for this one call regardless of the
        configured personality — the configured personality itself is
        never changed by this.
        """

        personality = self._personality
        emoji_mode = self._emoji_mode

        if is_serious(event_kind, serious=serious):
            personality = Personality.PROFESSIONAL

        return format_event(event_kind, personality, emoji_mode, **template_vars)
