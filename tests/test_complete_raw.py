"""
Tests for the raw-completion path (`complete_raw`), added to fix
autocomplete returning chat refusals instead of code continuations.

Root cause: `generate()`/`create_chat_completion()` renders every prompt
through the model's instruct chat template. For an out-of-context code
fragment, an instruct-tuned model treats that as a suspicious request and
often refuses outright rather than continuing the text — which is what a
base/completion model does instead. `complete_raw()` bypasses the chat
template entirely.

Confirmed against the real local model before writing these tests:
    generate():     "I'm sorry, but I can't assist with that."  (2.53s)
    complete_raw():  " a + b"                                    (0.16s)
for the prefix "def add(a, b):\n    return".
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.llm.client import LLMClient
from src.llm.providers.base import LLMProvider
from src.llm.providers.local_inference import LocalInferenceProvider
from src.llm.providers.scripted import ScriptedProvider

# ---------------------------------------------------------------------------
# Default fallback (LLMProvider.complete_raw)
# ---------------------------------------------------------------------------


class _FakeProvider(LLMProvider):
    """Minimal provider exercising only the base class's default."""

    def __init__(self) -> None:
        self.received_messages: list[list[dict]] = []

    def complete(self, messages, temperature, max_tokens):
        self.received_messages.append(messages)
        return "raw fallback output"


class TestDefaultFallback:
    def test_providers_without_a_real_completion_api_still_produce_text(self):
        """
        Most hosted chat APIs have no template-free completion endpoint,
        so the default must still return usable text rather than raising
        NotImplementedError — a degraded autocomplete beats a crashing one.
        """
        provider = _FakeProvider()
        result = provider.complete_raw("def add(a, b):\n    return", 0.0, 20)
        assert result == "raw fallback output"

    def test_fallback_routes_through_a_single_user_turn(self):
        """
        No system prompt, no history — the fallback is a single
        instruction turn, not the assembled chat context `generate()`
        would build.
        """
        provider = _FakeProvider()
        provider.complete_raw("some code", 0.0, 20)
        messages = provider.received_messages[0]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"

    def test_fallback_instructs_continuation_not_explanation(self):
        """
        The one thing the degraded path can do to reduce refusals: make
        the intent explicit rather than handing the model a bare code
        fragment with no framing at all.
        """
        provider = _FakeProvider()
        provider.complete_raw("def add(a, b):\n    return", 0.0, 20)
        content = provider.received_messages[0][0]["content"]
        assert "continue" in content.lower()
        assert "def add(a, b):\n    return" in content

    def test_scripted_provider_uses_the_default_not_a_bespoke_override(self):
        """
        Guards against a future scripted-provider change accidentally
        diverging from the interface every other provider shares.
        """
        assert ScriptedProvider.complete_raw is LLMProvider.complete_raw


# ---------------------------------------------------------------------------
# LocalInferenceProvider — the actual fix
# ---------------------------------------------------------------------------


class TestLocalInferenceCompleteRaw:
    def _provider(self) -> LocalInferenceProvider:
        import threading

        provider = LocalInferenceProvider.__new__(LocalInferenceProvider)
        provider._ready = threading.Event()
        provider._ready.set()
        provider._error = None
        provider._model_path = "fake.gguf"
        provider._repo_id = ""
        provider._filename = ""
        provider._n_ctx = 512
        provider._n_threads = 1
        return provider

    def test_calls_create_completion_not_create_chat_completion(self, monkeypatch):
        """
        The exact bug: create_chat_completion applies the instruct chat
        template. create_completion sends the prompt as plain text.
        Using the wrong one is precisely how a refusal happens instead
        of a continuation.
        """
        import src.llm.providers.local_inference as mod

        fake_llm = MagicMock()
        fake_llm.create_completion.return_value = {"choices": [{"text": " a + b"}]}
        monkeypatch.setattr(mod, "_get_shared_llm", lambda *a, **k: fake_llm)

        provider = self._provider()
        result = provider.complete_raw("def add(a, b):\n    return", 0.0, 20)

        assert result == " a + b"
        fake_llm.create_completion.assert_called_once()
        fake_llm.create_chat_completion.assert_not_called()

    def test_prompt_is_sent_as_plain_text_unmodified(self, monkeypatch):
        """
        No message-list wrapping, no role framing — the prompt argument
        to create_completion must be exactly the given string.
        """
        import src.llm.providers.local_inference as mod

        fake_llm = MagicMock()
        fake_llm.create_completion.return_value = {"choices": [{"text": "x"}]}
        monkeypatch.setattr(mod, "_get_shared_llm", lambda *a, **k: fake_llm)

        provider = self._provider()
        prefix = "for i in range("
        provider.complete_raw(prefix, 0.0, 10)

        call_args = fake_llm.create_completion.call_args
        assert call_args.args[0] == prefix

    def test_stop_sequences_are_forwarded(self, monkeypatch):
        import src.llm.providers.local_inference as mod

        fake_llm = MagicMock()
        fake_llm.create_completion.return_value = {"choices": [{"text": "x"}]}
        monkeypatch.setattr(mod, "_get_shared_llm", lambda *a, **k: fake_llm)

        provider = self._provider()
        provider.complete_raw("code", 0.0, 10, stop=["\n\n", "def "])

        assert fake_llm.create_completion.call_args.kwargs["stop"] == ["\n\n", "def "]

    def test_no_stop_defaults_to_empty_list_not_none(self, monkeypatch):
        """llama_cpp expects a list; passing None through would be a TypeError risk."""
        import src.llm.providers.local_inference as mod

        fake_llm = MagicMock()
        fake_llm.create_completion.return_value = {"choices": [{"text": "x"}]}
        monkeypatch.setattr(mod, "_get_shared_llm", lambda *a, **k: fake_llm)

        provider = self._provider()
        provider.complete_raw("code", 0.0, 10)

        assert fake_llm.create_completion.call_args.kwargs["stop"] == []

    def test_holds_the_per_model_inference_lock(self, monkeypatch):
        """
        Same serialization guarantee as complete()/complete_stream() —
        llama_cpp is not thread-safe for concurrent calls on one model.
        """
        import src.llm.providers.local_inference as mod

        model_lock = mod._get_inference_lock("fake.gguf", 512)
        held_during_call = []

        def _check_lock(*args, **kwargs):
            held_during_call.append(not model_lock.acquire(blocking=False))
            if held_during_call[-1] is False:
                model_lock.release()
            return {"choices": [{"text": "x"}]}

        fake_llm = MagicMock()
        fake_llm.create_completion.side_effect = _check_lock
        monkeypatch.setattr(mod, "_get_shared_llm", lambda *a, **k: fake_llm)

        provider = self._provider()
        provider.complete_raw("code", 0.0, 10)

        assert held_during_call == [True]

    def test_context_length_error_is_translated(self, monkeypatch):
        import src.llm.providers.local_inference as mod
        from src.llm.errors import ContextLengthError

        fake_llm = MagicMock()
        fake_llm.create_completion.side_effect = ValueError(
            "Requested tokens (5000) exceed context window of 2048"
        )
        monkeypatch.setattr(mod, "_get_shared_llm", lambda *a, **k: fake_llm)

        provider = self._provider()
        with pytest.raises(ContextLengthError):
            provider.complete_raw("code", 0.0, 10)

    def test_unrelated_value_error_propagates_unchanged(self, monkeypatch):
        import src.llm.providers.local_inference as mod

        fake_llm = MagicMock()
        fake_llm.create_completion.side_effect = ValueError("something else broke")
        monkeypatch.setattr(mod, "_get_shared_llm", lambda *a, **k: fake_llm)

        provider = self._provider()
        with pytest.raises(ValueError, match="something else broke"):
            provider.complete_raw("code", 0.0, 10)


# ---------------------------------------------------------------------------
# LLMClient.complete_raw — the public entry point
# ---------------------------------------------------------------------------


class TestLLMClientCompleteRaw:
    def test_delegates_to_provider_complete_raw(self):
        provider = MagicMock()
        provider.complete_raw.return_value = "the completion"
        client = LLMClient(provider=provider)

        result = client.complete_raw("def add(a, b):\n    return", 0.0, 20)

        assert result == "the completion"
        provider.complete_raw.assert_called_once_with(
            "def add(a, b):\n    return", 0.0, 20, None
        )

    def test_prompt_is_not_wrapped_in_messages(self):
        """
        The whole point: unlike generate(), no system prompt and no
        history are assembled — complete_raw's provider call receives
        exactly the string passed in, not a messages list.
        """
        provider = MagicMock()
        provider.complete_raw.return_value = "x"
        client = LLMClient(provider=provider)

        client.complete_raw("raw text")

        call = provider.complete_raw.call_args
        assert isinstance(call.args[0], str)
        assert call.args[0] == "raw text"

    def test_defaults_fill_in_from_settings(self, monkeypatch):
        from src.config.settings import Settings

        monkeypatch.setattr(Settings, "TEMPERATURE", 0.3)
        monkeypatch.setattr(Settings, "MAX_NEW_TOKENS", 256)

        provider = MagicMock()
        provider.complete_raw.return_value = "x"
        client = LLMClient(provider=provider)

        client.complete_raw("code")

        call = provider.complete_raw.call_args
        assert call.args[1] == 0.3
        assert call.args[2] == 256

    def test_stop_sequences_pass_through(self):
        provider = MagicMock()
        provider.complete_raw.return_value = "x"
        client = LLMClient(provider=provider)

        client.complete_raw("code", stop=["\n\n"])

        assert provider.complete_raw.call_args.args[3] == ["\n\n"]
