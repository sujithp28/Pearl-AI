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
from src.llm.providers.pearl_inference import PearlInferenceProvider
from src.llm.providers.scripted import ScriptedProvider

SUPPORTED_PROVIDERS = (
    "pearl",
    "openai",
    "openrouter",
    "custom",
    "claude",
    "anthropic",
    "gemini",
    "scripted",
)


def create_provider(name: str) -> LLMProvider:
    """
    Construct the LLMProvider backend for `name`.
    """

    normalized = name.strip().lower()

    if normalized == "pearl":
        # Routes through Pearl's inference contract (OpenAI-compatible wire protocol).
        # Dev:  PEARL_INFERENCE_BASE_URL=https://openrouter.ai/api/v1
        # Prod: PEARL_INFERENCE_BASE_URL=https://api.pearl.ai/v1  [REQUIRES DEPLOYMENT]
        # ModelRouter overrides `model` per task (chat vs planning).
        return PearlInferenceProvider(
            api_key=Settings.PEARL_INFERENCE_API_KEY,
            base_url=Settings.PEARL_INFERENCE_BASE_URL,
            model=Settings.PEARL_INFERENCE_CHAT_MODEL,
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

    if normalized == "custom":
        return OpenAICompatibleProvider(
            api_key=Settings.CUSTOM_API_KEY,
            base_url=Settings.CUSTOM_BASE_URL,
            model=Settings.CUSTOM_MODEL,
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

    # Deterministic/offline. Reachable only by asking for it by name —
    # never a fallback, so a misconfigured real provider can't quietly
    # start serving scripted answers.
    if normalized == "scripted":
        return ScriptedProvider()

    raise ValueError(
        f"Unknown LLM provider: {name!r}. "
        f"Supported providers: {', '.join(SUPPORTED_PROVIDERS)}"
    )
