import json
from types import SimpleNamespace

import pytest

from src.config.settings import Settings
from src.llm.client import LLMClient
from src.llm.providers import (
    ClaudeProvider,
    GeminiProvider,
    LLMProvider,
    OpenAICompatibleProvider,
    PearlInferenceNotConfiguredError,
    PearlInferenceProvider,
    ScriptedProvider,
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
    ["openai", "openrouter", "custom"],
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
        "pearl",
        "openai",
        "openrouter",
        "custom",
        "claude",
        "anthropic",
        "gemini",
        # Deterministic/offline; see src/llm/providers/scripted.py.
        "scripted",
    }


def test_pearl_without_key_creates_local_inference_provider(monkeypatch, tmp_path):
    # No API key → zero-config local inference. The factory returns
    # LocalInferenceProvider (not PearlInferenceProvider) so Pearl works
    # out of the box without any user-supplied credentials.
    from src.llm.providers.local_inference import LocalInferenceProvider
    monkeypatch.setattr(Settings, "PEARL_INFERENCE_API_KEY", "")
    monkeypatch.setattr(Settings, "LOCAL_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(Settings, "LOCAL_MODEL_FILE", "model.gguf")
    monkeypatch.setattr(Settings, "LOCAL_MODEL_REPO", "r/r")
    (tmp_path / "model.gguf").write_bytes(b"fake-gguf")  # model already cached

    provider = create_provider("pearl")

    assert isinstance(provider, LocalInferenceProvider)
    assert provider._ready.is_set()  # no download needed


def test_pearl_local_inference_raises_import_error_without_llama_cpp(monkeypatch, tmp_path):
    # If llama-cpp-python is not installed, the first model call must raise
    # a clear ImportError with install instructions, not a confusing AttributeError.
    from src.llm.providers.local_inference import LocalInferenceProvider
    monkeypatch.setattr(Settings, "PEARL_INFERENCE_API_KEY", "")
    monkeypatch.setattr(Settings, "LOCAL_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(Settings, "LOCAL_MODEL_FILE", "model.gguf")
    monkeypatch.setattr(Settings, "LOCAL_MODEL_REPO", "r/r")
    (tmp_path / "model.gguf").write_bytes(b"fake-gguf")

    provider = create_provider("pearl")

    # Simulate llama_cpp not being importable
    with pytest.raises(ImportError, match="llama-cpp-python"):
        with pytest.MonkeyPatch().context() as mp:
            mp.setitem(__import__("sys").modules, "llama_cpp", None)
            # Clear the model cache so loading is attempted again (and so
            # this test cannot evict a model a later test depends on).
            import src.llm.providers.local_inference as _m
            saved = dict(_m._llms)
            _m._llms.clear()
            try:
                provider.complete([{"role": "user", "content": "hi"}], 0.2, 100)
            finally:
                _m._llms.update(saved)


def test_pearl_provider_builds_pearl_inference_when_configured(monkeypatch):
    monkeypatch.setattr(Settings, "PEARL_INFERENCE_API_KEY", "test-key")
    monkeypatch.setattr(Settings, "PEARL_INFERENCE_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setattr(Settings, "PEARL_INFERENCE_CHAT_MODEL", "anthropic/claude-haiku-4-5-20251001")

    provider = create_provider("pearl")

    assert isinstance(provider, PearlInferenceProvider)
    assert provider._api_key == "test-key"
    assert provider.model == "anthropic/claude-haiku-4-5-20251001"




def test_scripted_provider_is_never_reached_without_asking_for_it(monkeypatch):
    """
    The scripted provider returns canned text. It must only ever be
    selected by explicit configuration — if a typo or a failure in a
    real provider could fall through to it, Pearl would silently
    serve fake answers as if they were a real model's.
    """

    # A real provider name never yields the scripted one...
    monkeypatch.setattr(Settings, "OPENAI_API_KEY", "dummy-key")
    assert not isinstance(create_provider("openai"), ScriptedProvider)

    # ...and an unrecognized name fails loudly rather than degrading.
    with pytest.raises(ValueError):
        create_provider("typo-provider")


def test_scripted_provider_is_selectable_by_name():
    assert isinstance(create_provider("scripted"), ScriptedProvider)


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


# ---------------------------------------------------------------------
# LLMClient integration with the provider abstraction
# ---------------------------------------------------------------------


def test_llm_client_defaults_to_settings_provider(monkeypatch):
    monkeypatch.setattr(Settings, "OPENAI_API_KEY", "dummy-key")
    monkeypatch.setattr(Settings, "LLM_PROVIDER", "openai")

    client = LLMClient()

    assert isinstance(client.provider, OpenAICompatibleProvider)
    assert client.provider.model == Settings.OPENAI_MODEL


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


# ---------------------------------------------------------------------
# ScriptedProvider (deterministic/offline)
# ---------------------------------------------------------------------


def test_scripted_provider_returns_responses_in_order():
    provider = ScriptedProvider(responses=["one", "two"])

    assert provider.complete([], 0.2, 100) == "one"
    assert provider.complete([], 0.2, 100) == "two"


def test_scripted_provider_repeats_its_last_response_when_exhausted():
    # Repeating rather than raising: a test asserting on the first two
    # calls shouldn't break because some later code path made a third.
    provider = ScriptedProvider(responses=["only"])

    assert provider.complete([], 0.2, 100) == "only"
    assert provider.complete([], 0.2, 100) == "only"


def test_scripted_provider_records_the_messages_it_was_sent():
    provider = ScriptedProvider(responses=["x"])
    messages = [{"role": "user", "content": "hi"}]

    provider.complete(messages, 0.2, 100)

    assert provider.calls == [messages]


def test_scripted_provider_reads_its_script_from_the_environment(monkeypatch):
    monkeypatch.setenv("PEARL_SCRIPTED_RESPONSES", '["from-env"]')

    assert ScriptedProvider().complete([], 0.2, 100) == "from-env"


def test_scripted_provider_falls_back_to_a_parsable_plan_when_unconfigured(
    monkeypatch,
):
    # An unconfigured scripted provider must still return something
    # the planner can parse, rather than failing in a way that looks
    # like a Pearl bug.
    monkeypatch.delenv("PEARL_SCRIPTED_RESPONSES", raising=False)

    response = ScriptedProvider().complete([], 0.2, 100)

    assert json.loads(response)["steps"][0]["tool"] == "none"


@pytest.mark.parametrize("bad", ["not json", '{"not": "a list"}', "[1, 2, 3]", "[]"])
def test_scripted_provider_survives_a_malformed_script(monkeypatch, bad):
    monkeypatch.setenv("PEARL_SCRIPTED_RESPONSES", bad)

    response = ScriptedProvider().complete([], 0.2, 100)

    assert json.loads(response)["steps"][0]["tool"] == "none"


# ---------------------------------------------------------------------
# Import cost
#
# Pearl's default path is the local model with no API key. It should not
# pay to import vendor SDKs it never calls. Measured in a subprocess
# because sys.modules is process-global — by the time this test runs,
# another test has almost certainly imported openai already.
# ---------------------------------------------------------------------


def _modules_after(code: str) -> set[str]:
    """Run `code` in a clean interpreter, return its loaded module names."""
    import json as _json
    import subprocess
    import sys as _sys
    from pathlib import Path as _Path

    script = (
        "import sys, json\n"
        f"{code}\n"
        "print(json.dumps(sorted(sys.modules)))"
    )
    out = subprocess.run(
        [_sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(_Path(__file__).resolve().parent.parent),
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    return set(_json.loads(out.stdout.strip().splitlines()[-1]))


def test_importing_the_provider_base_does_not_load_vendor_sdks():
    loaded = _modules_after("from src.llm.providers.base import LLMProvider")

    assert "openai" not in loaded
    assert "anthropic" not in loaded
    assert "llama_cpp" not in loaded


def test_local_provider_does_not_load_the_openai_sdk():
    """The zero-config default must not pay for an SDK it never calls."""
    loaded = _modules_after(
        "from src.config.settings import Settings\n"
        "Settings.PEARL_INFERENCE_API_KEY = ''\n"
        "from src.llm.providers.factory import create_provider\n"
        "create_provider('pearl')"
    )

    assert "openai" not in loaded


def test_openai_provider_still_loads_its_sdk():
    """The lazy import must not break the provider that needs it."""
    loaded = _modules_after(
        "from src.llm.providers.factory import create_provider\n"
        "create_provider('openai')"
    )

    assert "openai" in loaded


def test_every_exported_name_resolves():
    """
    The lazy __getattr__ maps each exported name to the module that
    defines it. A wrong row there fails only when someone imports that
    one name, which no linter catches — the eager version could not
    drift this way because the import itself would have failed.
    """
    import src.llm.providers as providers

    for name in providers.__all__:
        assert getattr(providers, name) is not None, name


def test_all_matches_the_export_map():
    import src.llm.providers as providers

    assert set(providers.__all__) == {"LLMProvider", *providers._EXPORTS}


def test_unknown_attribute_still_raises_attribute_error():
    import src.llm.providers as providers

    with pytest.raises(AttributeError):
        providers.NoSuchProvider
