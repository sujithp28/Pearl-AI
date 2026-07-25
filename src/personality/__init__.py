"""
Pearl's personality engine.

Gives Pearl's own status/progress messages a configurable voice
(`PERSONALITY=professional|friendly|cheeky|savage`) and emoji density
(`EMOJI_MODE=none|minimal|normal|fun`) — see `src/config/settings.py`.

Hard boundary, not a suggestion: this package only ever transforms
Pearl's own already-decided, already-authored status text (see
`personalities.py`) into its final display form. It never
constructs, sees, or modifies an LLM prompt or an LLM's response, and
it never influences planning, tool selection, tool execution, code
generation, or safety checks — those all run to completion, with
their real results, before anything here is ever called. If a future
change makes this package touch a prompt or a model response, that
change is out of scope for this package and belongs somewhere else.

Public surface: `PersonalityManager` (`manager.py`) is the only thing
most callers need — construct one (or accept the default, which reads
`Settings.PERSONALITY`/`Settings.EMOJI_MODE`) and call
`.format(event_kind, ...)`. Everything else here (`EventKind`,
`Personality`, `EmojiMode`, the formatter/template helpers) is
exported for testing and for anyone extending the personality set.
"""

from src.personality.emoji import EMOJI_MAP, EmojiMode, EventKind
from src.personality.formatter import (
    apply_emoji_safely,
    contains_code,
    enforce_emoji_limit,
    format_event,
)
from src.personality.manager import PersonalityManager
from src.personality.personalities import (
    DEFAULT_PERSONALITY,
    FALLBACK_PERSONALITY,
    PERSONALITY_TEMPLATES,
    Personality,
)
from src.personality.serious_mode import ALWAYS_SERIOUS_EVENT_KINDS, is_serious

__all__ = [
    "PersonalityManager",
    "Personality",
    "EventKind",
    "EmojiMode",
    "EMOJI_MAP",
    "PERSONALITY_TEMPLATES",
    "DEFAULT_PERSONALITY",
    "FALLBACK_PERSONALITY",
    "format_event",
    "apply_emoji_safely",
    "contains_code",
    "enforce_emoji_limit",
    "is_serious",
    "ALWAYS_SERIOUS_EVENT_KINDS",
]
