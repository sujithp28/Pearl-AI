"""
Turns a resolved (personality, event kind) pair into final display
text, and applies emoji-placement rules safely to text in general.

Two responsibilities, kept separate:

- `format_event()` — the primary path: render one of Pearl's own
  fixed status/progress strings (see `personalities.py`) with the
  configured emoji mode. This is what `PersonalityManager` uses.
- `apply_emoji_safely()` — a general-purpose, markdown/code-block-
  aware emoji decorator for arbitrary text. Not currently wired into
  any free-form output (e.g. chat replies) — kept here, tested on its
  own, as the safe primitive a future integration would need, since
  "never decorate code blocks" is much harder to get right for
  arbitrary text than for a handful of fixed strings that are already
  authored without embedded code.

Both are pure string functions: no I/O, no LLM calls, no state —
matching the "formatter must execute in milliseconds" requirement
trivially, since there's nothing here slower than a few dict lookups
and regex scans over short strings.
"""

from __future__ import annotations

import re

from src.personality.emoji import EMOJI_MAP, EmojiMode, EventKind
from src.personality.personalities import PERSONALITY_TEMPLATES, Personality
from src.personality.templates import render

MAX_EMOJI_PER_MESSAGE = 2

# Broad but deliberately non-exhaustive: covers the common emoji
# blocks (pictographs, symbols, dingbats, flags) plus an optional
# trailing FE0F variation selector (used by e.g. "🛠️") as part of the
# *same* match — good enough to reliably find the emoji this package
# itself ever emits, which is the only text this module needs to
# police.
#
# Deliberately no trailing `+`: each match is exactly one emoji
# (optionally +FE0F), never a whole run collapsed into one match —
# "🧠🧠🧠" must produce three separate matches so repeats can actually
# be detected and dropped, not one opaque three-character blob that
# looks unique the first time it's seen.
_EMOJI_RUN_RE = re.compile(
    "[\U0001f300-\U0001faff☀-➿\U0001f1e6-\U0001f1ff⬀-⯿]️?",
    flags=re.UNICODE,
)

_FENCED_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def enforce_emoji_limit(text: str, max_emoji: int = MAX_EMOJI_PER_MESSAGE) -> str:
    """
    Keep at most `max_emoji` emoji runs in `text`, dropping repeats of
    an emoji already seen and anything past the limit. Defensive: the
    personality templates are hand-authored to already respect this,
    so this exists to make the *rule* enforceable and testable on its
    own, not because the templates are expected to violate it.
    """

    seen: set[str] = set()
    kept = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal kept

        emoji = match.group(0)

        if emoji in seen or kept >= max_emoji:
            return ""

        seen.add(emoji)
        kept += 1

        return emoji

    result = _EMOJI_RUN_RE.sub(_replace, text)

    # Collapse any double-spacing left behind by a removed emoji.
    return re.sub(r" {2,}", " ", result).strip()


def format_event(
    event_kind: EventKind,
    personality: Personality,
    emoji_mode: EmojiMode,
    **template_vars: object,
) -> str:
    """
    Render the configured personality's line for `event_kind`, with
    the leading category emoji applied per `emoji_mode`.
    """

    template = PERSONALITY_TEMPLATES[personality][event_kind]
    text = render(template, **template_vars)

    if emoji_mode is EmojiMode.NONE:
        return enforce_emoji_limit(text)

    emoji = EMOJI_MAP[event_kind]
    decorated = f"{emoji} {text}"

    return enforce_emoji_limit(decorated)


def apply_emoji_safely(text: str, emoji: str) -> str:
    """
    Prefix `text` with `emoji`, unless `text` is (or starts with) a
    fenced code block, JSON/YAML-shaped content, or is otherwise not
    a human-readable message — general-purpose primitive for a future
    caller that wants to decorate arbitrary text, not just Pearl's own
    fixed status strings. Never touches text *inside* a fenced or
    inline code span, wherever it appears in the message.
    """

    stripped = text.lstrip()

    if (
        stripped.startswith("```")
        or stripped.startswith("{")
        or stripped.startswith("[")
    ):
        return text

    return enforce_emoji_limit(f"{emoji} {text}")


def contains_code(text: str) -> bool:
    """
    Return whether `text` contains a fenced or inline code span —
    callers that decorate arbitrary text should check this first and
    skip decoration entirely for a message that's mostly/entirely
    code, rather than trying to decorate around it.
    """

    return bool(_FENCED_CODE_BLOCK_RE.search(text) or _INLINE_CODE_RE.search(text))
