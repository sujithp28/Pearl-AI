"""
Provider factory.

Maps a provider name (from configuration) to a constructed
LLMProvider instance, reading each provider's own settings.
"""

from __future__ import annotations

from pathlib import Path

from src.config.settings import Settings
from src.llm.providers.base import LLMProvider

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
        if Settings.PEARL_INFERENCE_API_KEY:
            from src.llm.providers.pearl_inference import PearlInferenceProvider

            # Explicit key → remote inference (OpenRouter in dev,
            # api.pearl.ai in prod). ModelRouter overrides `model` per
            # task (chat vs planning).
            return PearlInferenceProvider(
                api_key=Settings.PEARL_INFERENCE_API_KEY,
                base_url=Settings.PEARL_INFERENCE_BASE_URL,
                model=Settings.PEARL_INFERENCE_CHAT_MODEL,
            )
        else:
            from src.llm.providers.local_inference import LocalInferenceProvider

            # No key → zero-configuration local inference.
            # Model auto-downloads from HuggingFace on first run (~491 MB).
            model_path = str(
                Path(Settings.LOCAL_MODEL_DIR) / Settings.LOCAL_MODEL_FILE
            )
            return LocalInferenceProvider(
                model_path=model_path,
                repo_id=Settings.LOCAL_MODEL_REPO,
                filename=Settings.LOCAL_MODEL_FILE,
                n_ctx=Settings.LOCAL_MODEL_CTX,
                n_threads=Settings.LOCAL_MODEL_THREADS or None,
            )

    if normalized in ("openai", "openrouter", "custom"):
        from src.llm.providers.openai_compatible import OpenAICompatibleProvider

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
        from src.llm.providers.claude import ClaudeProvider

        return ClaudeProvider(
            api_key=Settings.ANTHROPIC_API_KEY,
            model=Settings.ANTHROPIC_MODEL,
        )

    if normalized == "gemini":
        from src.llm.providers.gemini import GeminiProvider

        return GeminiProvider(
            api_key=Settings.GEMINI_API_KEY,
            model=Settings.GEMINI_MODEL,
        )

    # Deterministic/offline. Reachable only by asking for it by name —
    # never a fallback, so a misconfigured real provider can't quietly
    # start serving scripted answers.
    if normalized == "scripted":
        from src.llm.providers.scripted import ScriptedProvider

        return ScriptedProvider()

    raise ValueError(
        f"Unknown LLM provider: {name!r}. "
        f"Supported providers: {', '.join(SUPPORTED_PROVIDERS)}"
    )
