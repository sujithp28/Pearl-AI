"""
Provider factory.

Maps a provider name (from configuration) to a constructed
LLMProvider instance, reading each provider's own settings.
"""

from __future__ import annotations

from src.config.settings import Settings
from src.llm.providers.base import LLMProvider
from src.llm.providers.claude import ClaudeProvider
from src.llm.providers.gemini import GeminiProvider
from src.llm.providers.openai_compatible import OpenAICompatibleProvider

SUPPORTED_PROVIDERS = (
    "omniroute",
    "openai",
    "openrouter",
    "ollama",
    "claude",
    "anthropic",
    "gemini",
)


def create_provider(name: str) -> LLMProvider:
    """
    Construct the LLMProvider backend for `name`.
    """

    normalized = name.strip().lower()

    if normalized == "omniroute":
        return OpenAICompatibleProvider(
            api_key=Settings.OMNIROUTE_API_KEY,
            base_url=Settings.OMNIROUTE_BASE_URL,
            model=Settings.OMNIROUTE_MODEL,
        )

    if normalized == "openai":
        return OpenAICompatibleProvider(
            api_key=Settings.OPENAI_API_KEY,
            base_url=Settings.OPENAI_BASE_URL,
            model=Settings.OPENAI_MODEL,
        )

    if normalized == "openrouter":
        return OpenAICompatibleProvider(
            api_key=Settings.OPENROUTER_API_KEY,
            base_url=Settings.OPENROUTER_BASE_URL,
            model=Settings.OPENROUTER_MODEL,
        )

    if normalized == "ollama":
        return OpenAICompatibleProvider(
            api_key=Settings.OLLAMA_API_KEY,
            base_url=Settings.OLLAMA_BASE_URL,
            model=Settings.OLLAMA_MODEL,
        )

    if normalized in ("claude", "anthropic"):
        return ClaudeProvider(
            api_key=Settings.ANTHROPIC_API_KEY,
            model=Settings.ANTHROPIC_MODEL,
        )

    if normalized == "gemini":
        return GeminiProvider(
            api_key=Settings.GEMINI_API_KEY,
            model=Settings.GEMINI_MODEL,
        )

    raise ValueError(
        f"Unknown LLM provider: {name!r}. "
        f"Supported providers: {', '.join(SUPPORTED_PROVIDERS)}"
    )
