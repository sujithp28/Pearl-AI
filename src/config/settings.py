"""
Global configuration for Pearl.
"""

import os
from pathlib import Path


def load_env_file(path: Path) -> None:
    """
    Load `KEY=value` pairs from a `.env` file into the environment.

    A small stdlib replacement for `python-dotenv`, which was Pearl's
    only runtime dependency besides the LLM client. The file format
    Pearl actually uses is a handful of `KEY=value` lines; supporting
    that (plus comments, blank lines, `export ` prefixes and quoted
    values) is a few lines, and not worth a package.

    Existing environment variables always win, matching
    `python-dotenv`'s default. That is load-bearing, not cosmetic:
    CI and the end-to-end tests launch Pearl with an explicit
    `PEARL_LLM_PROVIDER` in the environment and rely on a developer's
    local `.env` not silently overriding it.

    Never raises: a missing or unreadable `.env` leaves every setting
    on its documented default rather than preventing startup.
    """

    try:
        if not path.is_file():
            return

        content = path.read_text(encoding="utf-8")
    except OSError:
        return

    for raw_line in content.splitlines():
        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[len("export ") :].lstrip()

        key, separator, value = line.partition("=")

        if not separator:
            continue

        key = key.strip()

        if not key:
            continue

        value = value.strip()

        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]

        os.environ.setdefault(key, value)


# Resolved relative to this file, not the process's cwd: Pearl's
# workspace root is wherever the *target* project lives (never
# guaranteed to be Pearl's own repo), so `.env` must not depend on
# cwd to be found — otherwise every provider setting silently falls
# back to its hardcoded default (e.g. the wrong Ollama model) when
# Pearl is pointed at an external project.
load_env_file(Path(__file__).resolve().parents[2] / ".env")


def _int_or_none(value: str | None) -> int | None:
    return int(value) if value else None


class Settings:
    # ==================================================
    # Project
    # ==================================================

    PROJECT_NAME = "Pearl"
    VERSION = "1.2.0-beta"

    # ==================================================
    # LLM Provider Selection
    # ==================================================

    # Which provider LLMClient() connects to by default.
    # One of: ollama, openai, openrouter, custom, claude, anthropic, gemini.
    LLM_PROVIDER = os.getenv("PEARL_LLM_PROVIDER", "ollama")

    # -- OpenAI --

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

    OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # -- Anthropic (Claude) --

    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

    ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")

    # -- Google (Gemini) --

    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    # -- OpenRouter --

    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

    OPENROUTER_BASE_URL = os.getenv(
        "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
    )

    OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "openrouter/auto")

    # -- Ollama --

    OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "ollama")

    OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")

    OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")

    # -- Any other OpenAI-compatible endpoint --

    CUSTOM_API_KEY = os.getenv("CUSTOM_API_KEY", "")

    CUSTOM_BASE_URL = os.getenv("CUSTOM_BASE_URL", "")

    CUSTOM_MODEL = os.getenv("CUSTOM_MODEL", "")

    # ==================================================
    # Model Routing (M6)
    # ==================================================
    #
    # Pearl can use a different provider/model for planning (JSON-
    # structured, needs accuracy) vs. chat (conversational, benefits
    # from speed). Each falls back to LLM_PROVIDER / the provider's
    # default model when unset.
    #
    # Example: use a large local model for planning and a fast remote
    # one for chat:
    #   PEARL_PLANNING_PROVIDER=ollama
    #   PEARL_PLANNING_MODEL=llama3:70b
    #   PEARL_CHAT_PROVIDER=openai
    #   PEARL_CHAT_MODEL=gpt-4o-mini

    PLANNING_PROVIDER = os.getenv("PEARL_PLANNING_PROVIDER", "")
    PLANNING_MODEL = os.getenv("PEARL_PLANNING_MODEL", "")

    CHAT_PROVIDER = os.getenv("PEARL_CHAT_PROVIDER", "")
    CHAT_MODEL = os.getenv("PEARL_CHAT_MODEL", "")

    # ==================================================
    # Generation
    # ==================================================

    MAX_NEW_TOKENS = 1024

    TEMPERATURE = 0.2

    # How many prior conversation turns `pearl/chat` sends back to the
    # model so it can follow the thread. Capped rather than unbounded:
    # every turn is re-sent on every message, so a long session would
    # otherwise grow the prompt (and the latency, which is already the
    # dominant cost on local models) without limit. 0 disables history
    # entirely, restoring the previous stateless behavior.
    CHAT_HISTORY_TURNS = int(os.getenv("PEARL_CHAT_HISTORY_TURNS", "10"))

    # ==================================================
    # Checkpoints (src/tools/checkpoints.py)
    # ==================================================

    # Auto-cleanup retention: checkpoints beyond this count, or older
    # than this many days, are hidden from listing and can no longer
    # be restored. 0 disables that bound (the other still applies).
    # Deletion is via metadata, never by rewriting the shadow git
    # history, so it can never corrupt the store — see
    # CheckpointManager's module docstring.
    CHECKPOINT_MAX_COUNT = int(os.getenv("PEARL_CHECKPOINT_MAX_COUNT", "50"))

    CHECKPOINT_MAX_AGE_DAYS = int(os.getenv("PEARL_CHECKPOINT_MAX_AGE_DAYS", "30"))

    # ==================================================
    # Shell execution (src/tools/shell_tools.py)
    # ==================================================

    SHELL_DEFAULT_TIMEOUT = int(os.getenv("PEARL_SHELL_TIMEOUT", "30"))

    SHELL_MAX_TIMEOUT = int(os.getenv("PEARL_SHELL_MAX_TIMEOUT", "300"))

    # CPU-time (seconds) and memory (MB) ceilings applied to every
    # `execute_shell`/`run_python` call that doesn't specify its own.
    # Unset (the default) means no limit — matches pre-Phase-26
    # behavior. Only enforced on POSIX (see shell_tools._resource_
    # limiter); silently not applied on platforms without the
    # `resource` module (e.g. Windows).
    SHELL_DEFAULT_CPU_SECONDS = _int_or_none(os.getenv("PEARL_SHELL_CPU_SECONDS"))

    SHELL_DEFAULT_MEMORY_MB = _int_or_none(os.getenv("PEARL_SHELL_MEMORY_MB"))

    # Comma-separated list of base commands `execute_shell` may run
    # (e.g. "git,ls,cat,python3"). Empty/unset (the default) uses
    # shell_tools' built-in practical allowlist; set to "*" to
    # disable allowlisting entirely (denylist-only — pre-Phase-26
    # behavior, not recommended).
    SHELL_ALLOWED_COMMANDS = os.getenv("PEARL_SHELL_ALLOWED_COMMANDS", "")

    # ==================================================
    # Personality (src/personality/)
    # ==================================================

    # One of: professional, friendly, cheeky, savage. Only affects the
    # wording of Pearl's own status/progress messages — never LLM
    # prompts, model output, planning, or tool selection. An
    # unrecognized value falls back to "professional" (see
    # PersonalityManager), not to this default.
    PERSONALITY = os.getenv("PERSONALITY", "cheeky")

    # One of: none, minimal, normal, fun. An unrecognized value falls
    # back to "minimal".
    EMOJI_MODE = os.getenv("EMOJI_MODE", "minimal")


settings = Settings()
