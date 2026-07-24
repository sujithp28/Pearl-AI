from types import SimpleNamespace

import pytest

from src.config.settings import Settings
from src.llm.client import LLMClient
from src.llm.providers import (
    ClaudeProvider,
    GeminiProvider,
    LLMProvider,
    OpenAICompatibleProvider,
    create_provider,
)
from src.llm.providers.factory import SUPPORTED_PROVIDERS


def _make_openai_response(content: str):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


# ---------------------------------------------------------------------
# LLMProvider (abstract base)
# ---------------------------------------------------------------------


def test_llm_provider_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        LLMProvider()


def test_llm_provider_default_transient_errors_is_empty_tuple():
    assert LLMProvider.TRANSIENT_ERRORS == ()


# ---------------------------------------------------------------------
# OpenAICompatibleProvider
# ---------------------------------------------------------------------


def test_openai_compatible_provider_complete_returns_stripped_text():
    provider = OpenAICompatibleProvider(
        api_key="key", base_url="http://localhost:1234/v1", model="test-model"
    )

    provider.client.chat.completions.create = lambda **kwargs: _make_openai_response(
        "  hello  "
    )

    result = provider.complete(
        [{"role": "user", "content": "hi"}], temperature=0.2, max_tokens=100
    )

    assert result == "hello"


def test_openai_compatible_provider_rejects_empty_choices():
    provider = OpenAICompatibleProvider(
        api_key="key", base_url="http://localhost:1234/v1", model="test-model"
    )

    provider.client.chat.completions.create = lambda **kwargs: SimpleNamespace(
        choices=[]
    )

    with pytest.raises(ValueError):
        provider.complete([{"role": "user", "content": "hi"}], 0.2, 100)


def test_openai_compatible_provider_rejects_none_content():
    provider = OpenAICompatibleProvider(
        api_key="key", base_url="http://localhost:1234/v1", model="test-model"
    )

    provider.client.chat.completions.create = lambda **kwargs: _make_openai_response(
        None
    )

    with pytest.raises(ValueError):
        provider.complete([{"role": "user", "content": "hi"}], 0.2, 100)


def test_openai_compatible_provider_declares_transient_errors():
    from openai import (
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
    )

    assert OpenAICompatibleProvider.TRANSIENT_ERRORS == (
        APIConnectionError,
        APITimeoutError,
        RateLimitError,
        InternalServerError,
    )


# ---------------------------------------------------------------------
# ClaudeProvider / GeminiProvider (optional dependencies)
# ---------------------------------------------------------------------


def test_claude_provider_raises_clear_error_without_sdk():
    with pytest.raises(ImportError, match="anthropic"):
        ClaudeProvider(api_key="key", model="claude-sonnet-4-5")


def test_gemini_provider_raises_clear_error_without_sdk():
    with pytest.raises(ImportError, match="google-generativeai"):
        GeminiProvider(api_key="key", model="gemini-2.5-flash")


# ---------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["openai", "openrouter", "ollama", "custom"],
)
def test_create_provider_builds_openai_compatible_backends(name, monkeypatch):
    monkeypatch.setattr(Settings, "OPENAI_API_KEY", "dummy-key")
    monkeypatch.setattr(Settings, "OPENROUTER_API_KEY", "dummy-key")
    monkeypatch.setattr(Settings, "CUSTOM_API_KEY", "dummy-key")
    monkeypatch.setattr(Settings, "CUSTOM_BASE_URL", "http://localhost:9999/v1")
    monkeypatch.setattr(Settings, "CUSTOM_MODEL", "custom-model")

    provider = create_provider(name)

    assert isinstance(provider, OpenAICompatibleProvider)


def test_create_provider_is_case_and_whitespace_insensitive(monkeypatch):
    monkeypatch.setattr(Settings, "OPENAI_API_KEY", "dummy-key")

    provider = create_provider("  OpenAI  ")

    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.model == Settings.OPENAI_MODEL


@pytest.mark.parametrize("name", ["claude", "anthropic"])
def test_create_provider_maps_claude_aliases_without_sdk(name):
    with pytest.raises(ImportError):
        create_provider(name)


def test_create_provider_gemini_without_sdk():
    with pytest.raises(ImportError):
        create_provider("gemini")


def test_create_provider_rejects_unknown_name():
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        create_provider("not-a-real-provider")


def test_supported_providers_lists_all_provider_names():
    assert set(SUPPORTED_PROVIDERS) == {
        "openai",
        "openrouter",
        "ollama",
        "custom",
        "claude",
        "anthropic",
        "gemini",
    }


def test_custom_provider_uses_custom_settings(monkeypatch):
    monkeypatch.setattr(Settings, "CUSTOM_API_KEY", "dummy-key")
    monkeypatch.setattr(Settings, "CUSTOM_BASE_URL", "http://localhost:9999/v1")
    monkeypatch.setattr(Settings, "CUSTOM_MODEL", "custom-model")

    provider = create_provider("custom")

    assert provider.model == Settings.CUSTOM_MODEL
    assert str(provider.client.base_url).rstrip("/") == (
        Settings.CUSTOM_BASE_URL.rstrip("/")
    )


def test_openrouter_provider_uses_openrouter_settings(monkeypatch):
    monkeypatch.setattr(Settings, "OPENROUTER_API_KEY", "dummy-key")

    provider = create_provider("openrouter")

    assert provider.model == Settings.OPENROUTER_MODEL


def test_ollama_provider_uses_ollama_settings():
    provider = create_provider("ollama")

    assert provider.model == Settings.OLLAMA_MODEL


# ---------------------------------------------------------------------
# LLMClient integration with the provider abstraction
# ---------------------------------------------------------------------


def test_llm_client_defaults_to_settings_provider():
    client = LLMClient()

    assert isinstance(client.provider, OpenAICompatibleProvider)
    assert client.provider.model == Settings.OLLAMA_MODEL


def test_llm_client_accepts_explicit_provider_name(monkeypatch):
    monkeypatch.setattr(Settings, "OPENAI_API_KEY", "dummy-key")

    client = LLMClient(provider_name="openai")

    assert client.provider.model == Settings.OPENAI_MODEL


def test_llm_client_accepts_injected_provider_instance():
    class StubProvider(LLMProvider):
        def complete(self, messages, temperature, max_tokens):
            return "stubbed response"

    client = LLMClient(provider=StubProvider())

    assert client.generate("hi") == "stubbed response"


def test_llm_client_rejects_unknown_provider_name():
    with pytest.raises(ValueError):
        LLMClient(provider_name="not-a-real-provider")
