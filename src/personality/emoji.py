"""
Event taxonomy and emoji mapping for Pearl's personality engine.

`EventKind` is the one vocabulary every other module in this package
(`personalities.py`, `serious_mode.py`, `formatter.py`) shares — a
plain, closed set of "what kind of status message is this" labels,
not a big if/else chain. Adding a new kind of status message Pearl
can express is: add one enum member here, one emoji mapping, and one
line per personality in `personalities.py` — nothing else changes.
"""

from __future__ import annotations

from enum import Enum


class EventKind(str, Enum):
    """
    What Pearl is telling the user about right now.

    The first 20 mirror the personality spec's emoji-mapping table
    one-to-one. The rest exist to cover Pearl's own progress/timeline
    states that don't map cleanly onto any of the original 20 (e.g.
    "a tool step is running" isn't specifically "testing" or "writing
    code" — it could be either): `EXECUTING`, `REPLANNING`,
    `CANCELLED`, `REJECTED` (from `AutonomousExecutor`'s progress
    events) and `PLAN_READY` (the VS Code extension's plan-preview
    timeline — "I've made a plan, here it is for your review", distinct
    from `PLANNING` itself, which is "still thinking").
    """

    PLANNING = "planning"
    SEARCHING = "searching"
    REPO_INDEX = "repo_index"
    WRITING_CODE = "writing_code"
    PATCH_GENERATION = "patch_generation"
    TESTING = "testing"
    FIXING = "fixing"
    GIT = "git"
    COMMIT = "commit"
    REVIEW = "review"
    SECURITY = "security"
    APPROVAL = "approval"
    SUCCESS = "success"
    COMPLETED = "completed"
    WARNING = "warning"
    FAILURE = "failure"
    DEBUGGING = "debugging"
    PERFORMANCE = "performance"
    REFACTORING = "refactoring"
    DEPLOYMENT = "deployment"

    # Pearl-specific additions, not in the original 20 — see class
    # docstring.
    EXECUTING = "executing"
    REPLANNING = "replanning"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    PLAN_READY = "plan_ready"
    #: A workspace snapshot was taken before an approved write reaches
    #: disk (Sprint 1: the Checkpoint System) — distinct from COMMIT,
    #: which is about the user's own git history, not Pearl's shadow
    #: undo store.
    CHECKPOINT = "checkpoint"


EMOJI_MAP: dict[EventKind, str] = {
    EventKind.PLANNING: "🧠",
    EventKind.SEARCHING: "🔍",
    EventKind.REPO_INDEX: "📚",
    EventKind.WRITING_CODE: "✍️",
    EventKind.PATCH_GENERATION: "🛠️",
    EventKind.TESTING: "🧪",
    EventKind.FIXING: "🔧",
    EventKind.GIT: "🌿",
    EventKind.COMMIT: "📦",
    EventKind.REVIEW: "👀",
    EventKind.SECURITY: "🛡️",
    EventKind.APPROVAL: "⏳",
    EventKind.SUCCESS: "✅",
    EventKind.COMPLETED: "🎉",
    EventKind.WARNING: "⚠️",
    EventKind.FAILURE: "❌",
    EventKind.DEBUGGING: "🐞",
    EventKind.PERFORMANCE: "⚡",
    EventKind.REFACTORING: "♻️",
    EventKind.DEPLOYMENT: "🚀",
    EventKind.EXECUTING: "⚙️",
    EventKind.REPLANNING: "🔄",
    EventKind.CANCELLED: "🛑",
    EventKind.REJECTED: "🗑️",
    EventKind.PLAN_READY: "📋",
    EventKind.CHECKPOINT: "💾",
}


class EmojiMode(str, Enum):
    """
    How freely emoji are allowed to appear at all. Independent of
    `Personality` — e.g. `professional` + `normal` emoji mode is a
    valid, common combination (calm wording, but still a leading
    category emoji).
    """

    NONE = "none"
    MINIMAL = "minimal"
    NORMAL = "normal"
    FUN = "fun"


DEFAULT_EMOJI_MODE = EmojiMode.MINIMAL

_VALID_EMOJI_MODES = {mode.value for mode in EmojiMode}


def resolve_emoji_mode(value: str | None) -> EmojiMode:
    """
    Parse a configured emoji-mode string, falling back to
    `DEFAULT_EMOJI_MODE` for anything unrecognized (including empty
    or `None`) rather than raising.
    """

    normalized = (value or "").strip().lower()

    if normalized in _VALID_EMOJI_MODES:
        return EmojiMode(normalized)

    return DEFAULT_EMOJI_MODE
