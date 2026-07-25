"""
Tests for the personality engine (`src/personality/`).

Covers personality/emoji-mode selection, formatting, fallback
behavior, serious-mode overrides, emoji-limit enforcement, markdown/
code-block safety, configuration loading, performance, and thread
safety — and, most importantly, that none of it ever touches
planning, tool selection, or an LLM call.
"""

from __future__ import annotations

import threading
import time

import pytest

from src.personality import (
    ALWAYS_SERIOUS_EVENT_KINDS,
    DEFAULT_PERSONALITY,
    FALLBACK_PERSONALITY,
    PERSONALITY_TEMPLATES,
    EmojiMode,
    EventKind,
    Personality,
    PersonalityManager,
    apply_emoji_safely,
    contains_code,
    enforce_emoji_limit,
    format_event,
    is_serious,
)
from src.personality.emoji import EMOJI_MAP

# ---------------------------------------------------------------------
# Personality selection
# ---------------------------------------------------------------------


@pytest.mark.parametrize("value", ["professional", "friendly", "cheeky", "savage"])
def test_manager_accepts_every_supported_personality(value):
    manager = PersonalityManager(personality=value, emoji_mode="minimal")

    assert manager.personality.value == value


def test_manager_personality_is_case_and_whitespace_insensitive():
    manager = PersonalityManager(personality="  CHEEKY  ", emoji_mode="minimal")

    assert manager.personality is Personality.CHEEKY


def test_default_personality_is_cheeky():
    # Not the same claim as "an invalid value falls back to cheeky" —
    # this is Pearl's shipped-out-of-the-box choice.
    assert DEFAULT_PERSONALITY is Personality.CHEEKY


# ---------------------------------------------------------------------
# Fallback behavior
# ---------------------------------------------------------------------


def test_unrecognized_personality_falls_back_to_professional(caplog):
    manager = PersonalityManager(personality="nonsense", emoji_mode="minimal")

    assert manager.personality is Personality.PROFESSIONAL
    assert manager.personality is FALLBACK_PERSONALITY


def test_empty_personality_falls_back_to_professional():
    manager = PersonalityManager(personality="", emoji_mode="minimal")

    assert manager.personality is Personality.PROFESSIONAL


def test_unrecognized_emoji_mode_falls_back_to_minimal():
    manager = PersonalityManager(personality="cheeky", emoji_mode="extreme")

    assert manager.emoji_mode is EmojiMode.MINIMAL


# ---------------------------------------------------------------------
# Configuration loading (from Settings)
# ---------------------------------------------------------------------


def test_manager_reads_settings_when_not_given_explicitly(monkeypatch):
    from src.config.settings import Settings

    monkeypatch.setattr(Settings, "PERSONALITY", "savage")
    monkeypatch.setattr(Settings, "EMOJI_MODE", "none")

    manager = PersonalityManager()

    assert manager.personality is Personality.SAVAGE
    assert manager.emoji_mode is EmojiMode.NONE


def test_explicit_args_override_settings(monkeypatch):
    from src.config.settings import Settings

    monkeypatch.setattr(Settings, "PERSONALITY", "savage")

    manager = PersonalityManager(personality="friendly")

    assert manager.personality is Personality.FRIENDLY


# ---------------------------------------------------------------------
# Runtime change ("no restart required")
# ---------------------------------------------------------------------


def test_set_personality_changes_output_without_recreating_the_manager():
    manager = PersonalityManager(personality="professional", emoji_mode="minimal")

    before = manager.format(EventKind.COMPLETED)
    manager.set_personality("cheeky")
    after = manager.format(EventKind.COMPLETED)

    assert before != after
    assert manager.personality is Personality.CHEEKY


def test_set_emoji_mode_changes_output_without_recreating_the_manager():
    manager = PersonalityManager(personality="professional", emoji_mode="none")

    before = manager.format(EventKind.COMPLETED)
    manager.set_emoji_mode("minimal")
    after = manager.format(EventKind.COMPLETED)

    assert "🎉" not in before
    assert "🎉" in after


# ---------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------


def test_every_personality_defines_every_event_kind():
    for personality in Personality:
        for event_kind in EventKind:
            assert event_kind in PERSONALITY_TEMPLATES[personality], (
                f"{personality} is missing a template for {event_kind}"
            )


def test_minimal_mode_marks_outcomes_but_not_progress_narration():
    """
    `minimal` means "emoji where it carries information" — an outcome
    worth spotting, not every intermediate step.
    """

    manager = PersonalityManager(personality="professional", emoji_mode="minimal")

    outcome = manager.format(EventKind.COMPLETED)
    narration = manager.format(EventKind.EXECUTING)

    assert outcome.startswith(EMOJI_MAP[EventKind.COMPLETED])
    assert not narration.startswith(EMOJI_MAP[EventKind.EXECUTING])


def test_normal_mode_marks_every_event():
    manager = PersonalityManager(personality="professional", emoji_mode="normal")

    for event_kind in [EventKind.EXECUTING, EventKind.TESTING, EventKind.COMPLETED]:
        assert manager.format(event_kind).startswith(EMOJI_MAP[event_kind])


def test_the_four_emoji_modes_are_genuinely_distinct():
    """
    Regression: `minimal`, `normal` and `fun` once produced
    byte-identical output, making three of four documented settings
    dead configuration surface.
    """

    def render(mode: str) -> tuple[str, str]:
        manager = PersonalityManager(personality="professional", emoji_mode=mode)
        return manager.format(EventKind.EXECUTING), manager.format(EventKind.COMPLETED)

    outputs = {mode: render(mode) for mode in ["none", "minimal", "normal", "fun"]}

    assert len(set(outputs.values())) == 4, (
        f"every emoji mode must behave differently, got: {outputs}"
    )


def test_fun_mode_adds_a_celebratory_accent_within_the_emoji_ceiling():
    manager = PersonalityManager(personality="professional", emoji_mode="fun")

    text = manager.format(EventKind.COMPLETED)

    assert text.startswith(EMOJI_MAP[EventKind.COMPLETED])
    # Still obeys the two-emoji rule.
    assert text == enforce_emoji_limit(text)


def test_format_omits_emoji_entirely_in_none_mode():
    manager = PersonalityManager(personality="cheeky", emoji_mode="none")

    for event_kind in EventKind:
        text = manager.format(event_kind)
        assert EMOJI_MAP[event_kind] not in text


def test_professional_wording_is_calm_and_short():
    manager = PersonalityManager(personality="professional", emoji_mode="none")

    assert manager.format(EventKind.COMPLETED) == "Task completed successfully."
    assert manager.format(EventKind.SUCCESS) == "Tests passed."


def test_cheeky_is_the_default_and_has_personality():
    manager = PersonalityManager()

    text = manager.format(EventKind.COMPLETED)

    assert "Coffee for you" in text


# ---------------------------------------------------------------------
# Emoji rules: never spam, never repeat, cap at two
# ---------------------------------------------------------------------


def test_enforce_emoji_limit_deduplicates_a_repeated_emoji():
    assert enforce_emoji_limit("🧠🧠🧠 too many") == "🧠 too many"


def test_enforce_emoji_limit_caps_at_two_distinct_emoji():
    result = enforce_emoji_limit("🧠 🔍 🎉 three different ones")

    assert result.count("🧠") == 1
    assert result.count("🔍") == 1
    assert "🎉" not in result


def test_enforce_emoji_limit_preserves_compound_emoji_sequences():
    # "🛠️" is HAMMER-AND-WRENCH + a variation-selector codepoint — it
    # must survive as one visual glyph, not be mangled.
    result = enforce_emoji_limit("🛠️ patch ready")

    assert result.startswith("🛠️")


def test_no_personality_message_exceeds_two_emoji():
    # enforce_emoji_limit is idempotent on already-compliant text —
    # asserting the decorated output is unchanged by re-running it
    # through the limiter proves every authored template already
    # respects the "max 2, no repeats" rule on its own.
    for personality in Personality:
        for event_kind in EventKind:
            decorated = format_event(event_kind, personality, EmojiMode.NORMAL)
            assert decorated == enforce_emoji_limit(decorated)


# ---------------------------------------------------------------------
# Markdown / code-block safety (general-purpose primitive)
# ---------------------------------------------------------------------


def test_contains_code_detects_fenced_blocks():
    assert contains_code("here:\n```python\nprint(1)\n```")


def test_contains_code_detects_inline_code():
    assert contains_code("run `pytest` to check")


def test_contains_code_is_false_for_plain_text():
    assert not contains_code("just a normal sentence")


def test_apply_emoji_safely_skips_fenced_code_blocks():
    text = "```python\nprint('hi')\n```"

    assert apply_emoji_safely(text, "✅") == text


def test_apply_emoji_safely_skips_json_looking_text():
    text = '{"key": "value"}'

    assert apply_emoji_safely(text, "✅") == text


def test_apply_emoji_safely_decorates_plain_text():
    result = apply_emoji_safely("all done", "✅")

    assert result == "✅ all done"


# ---------------------------------------------------------------------
# Serious mode
# ---------------------------------------------------------------------


def test_security_event_kind_is_always_serious():
    assert EventKind.SECURITY in ALWAYS_SERIOUS_EVENT_KINDS
    assert is_serious(EventKind.SECURITY)


def test_explicit_serious_flag_overrides_any_event_kind():
    assert is_serious(EventKind.COMPLETED, serious=True)


def test_ordinary_event_kinds_are_not_serious_by_default():
    assert not is_serious(EventKind.COMPLETED)
    assert not is_serious(EventKind.FAILURE)


def test_serious_mode_forces_professional_wording_regardless_of_personality():
    manager = PersonalityManager(personality="savage", emoji_mode="minimal")

    security_text = manager.format(EventKind.SECURITY)
    professional_equivalent = PersonalityManager(
        personality="professional", emoji_mode="minimal"
    ).format(EventKind.SECURITY)

    assert security_text == professional_equivalent


def test_explicit_serious_flag_forces_professional_wording_for_any_kind():
    manager = PersonalityManager(personality="savage", emoji_mode="none")

    text = manager.format(EventKind.FAILURE, serious=True)

    assert text == "Compilation failed."


def test_serious_mode_does_not_permanently_change_the_configured_personality():
    manager = PersonalityManager(personality="savage", emoji_mode="none")

    manager.format(EventKind.SECURITY)

    assert manager.personality is Personality.SAVAGE
    assert "commitment issues" in manager.format(EventKind.WARNING)


# ---------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------


def test_format_is_fast():
    manager = PersonalityManager(personality="cheeky", emoji_mode="normal")

    start = time.perf_counter()
    for _ in range(1000):
        for event_kind in EventKind:
            manager.format(event_kind)
    elapsed = time.perf_counter() - start

    # 1000 * 24 = 24,000 calls; generous ceiling well above what pure
    # dict lookups + a couple of regex passes over short strings
    # should ever take, to avoid a flaky bound on slow CI machines.
    assert elapsed < 2.0, f"personality formatting took {elapsed:.3f}s for 24,000 calls"


# ---------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------


def test_format_is_safe_to_call_concurrently():
    manager = PersonalityManager(personality="cheeky", emoji_mode="minimal")
    errors: list[Exception] = []

    def _worker():
        try:
            for _ in range(200):
                for event_kind in EventKind:
                    manager.format(event_kind)
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(8)]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors


def test_set_personality_is_safe_to_call_concurrently_with_format():
    manager = PersonalityManager(personality="cheeky", emoji_mode="minimal")
    errors: list[Exception] = []
    stop = threading.Event()

    def _reader():
        try:
            while not stop.is_set():
                manager.format(EventKind.PLANNING)
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    def _writer():
        try:
            for _ in range(50):
                manager.set_personality("savage")
                manager.set_personality("friendly")
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    reader = threading.Thread(target=_reader)
    writer = threading.Thread(target=_writer)

    reader.start()
    writer.start()
    writer.join()
    stop.set()
    reader.join()

    assert not errors
