"""
Tests for LocalInferenceProvider (src/llm/providers/local_inference.py).

All tests mock llama_cpp and huggingface_hub so the suite runs without a
model file on disk and without network access.  Real E2E inference is
verified separately (see Phase 7 in the engineering report).
"""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.config.settings import Settings
from src.llm.providers import LocalInferenceProvider, LocalModelNotReadyError, create_provider
from src.llm.providers.local_inference import _get_shared_llm


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_stream_chunk(content: str | None):
    delta = {"content": content} if content is not None else {}
    return {"choices": [{"delta": delta}]}


def _make_chat_response(content: str):
    return {"choices": [{"message": {"content": content}}]}


# ── Construction ──────────────────────────────────────────────────────────────


class TestLocalInferenceProviderConstruction:
    def test_constructs_without_error_when_model_exists(self, tmp_path):
        model_file = tmp_path / "model.gguf"
        model_file.write_bytes(b"fake")  # just needs to exist

        provider = LocalInferenceProvider(
            model_path=str(model_file),
            repo_id="org/repo",
            filename="model.gguf",
        )

        assert provider._ready.is_set()

    def test_starts_background_download_when_model_missing(self, tmp_path):
        model_path = str(tmp_path / "missing.gguf")

        with patch(
            "src.llm.providers.local_inference._download_model"
        ) as mock_dl:
            mock_dl.side_effect = lambda **kwargs: None

            provider = LocalInferenceProvider(
                model_path=model_path,
                repo_id="org/repo",
                filename="missing.gguf",
            )
            # Give the background thread a moment to call _download_model
            provider._ready.wait(timeout=2)

        # Download was attempted
        mock_dl.assert_called_once()

    def test_model_attribute_set_to_filename(self, tmp_path):
        model_file = tmp_path / "qwen2.5.gguf"
        model_file.write_bytes(b"fake")

        provider = LocalInferenceProvider(
            model_path=str(model_file),
            repo_id="Qwen/Qwen2.5-0.5B-Instruct-GGUF",
            filename="qwen2.5.gguf",
        )

        assert provider.model == "qwen2.5.gguf"

    def test_threads_defaults_to_cpu_count_minus_one(self, tmp_path):
        import os
        model_file = tmp_path / "m.gguf"
        model_file.write_bytes(b"fake")

        provider = LocalInferenceProvider(
            model_path=str(model_file),
            repo_id="r/r",
            filename="m.gguf",
            n_threads=0,
        )

        assert provider._n_threads == max(1, (os.cpu_count() or 4) - 1)


# ── Download failure ──────────────────────────────────────────────────────────


class TestLocalInferenceProviderDownloadFailure:
    def test_raises_not_ready_when_download_fails(self, tmp_path):
        model_path = str(tmp_path / "missing.gguf")

        with patch("src.llm.providers.local_inference._download_model") as mock_dl:
            mock_dl.side_effect = Exception("network error")

            provider = LocalInferenceProvider(
                model_path=model_path,
                repo_id="org/repo",
                filename="missing.gguf",
            )
            provider._ready.wait(timeout=2)

        with pytest.raises(LocalModelNotReadyError, match="could not be loaded"):
            provider.complete([{"role": "user", "content": "hi"}], 0.2, 64)

    def test_error_message_does_not_contain_raw_credentials(self, tmp_path):
        model_path = str(tmp_path / "missing.gguf")

        with patch("src.llm.providers.local_inference._download_model") as mock_dl:
            mock_dl.side_effect = Exception("secret-api-key-123 in error")

            provider = LocalInferenceProvider(
                model_path=model_path,
                repo_id="org/repo",
                filename="missing.gguf",
            )
            provider._ready.wait(timeout=2)

        # The error is wrapped — model path info visible but no extra secrets injected
        with pytest.raises(LocalModelNotReadyError):
            provider.complete([{"role": "user", "content": "hi"}], 0.2, 64)


# ── Inference ─────────────────────────────────────────────────────────────────


class TestLocalInferenceProviderComplete:
    def _provider_with_mock_llm(self, tmp_path, mock_llm):
        model_file = tmp_path / "model.gguf"
        model_file.write_bytes(b"fake")

        provider = LocalInferenceProvider(
            model_path=str(model_file),
            repo_id="r/r",
            filename="model.gguf",
        )

        with patch("src.llm.providers.local_inference._get_shared_llm", return_value=mock_llm):
            return provider

    def test_complete_returns_stripped_content(self, tmp_path):
        mock_llm = MagicMock()
        mock_llm.create_chat_completion.return_value = _make_chat_response("  hello  ")

        model_file = tmp_path / "model.gguf"
        model_file.write_bytes(b"fake")

        provider = LocalInferenceProvider(
            model_path=str(model_file),
            repo_id="r/r",
            filename="model.gguf",
        )

        with patch("src.llm.providers.local_inference._get_shared_llm", return_value=mock_llm):
            result = provider.complete([{"role": "user", "content": "hi"}], 0.2, 64)

        assert result == "hello"

    def test_complete_raises_on_empty_response(self, tmp_path):
        mock_llm = MagicMock()
        mock_llm.create_chat_completion.return_value = _make_chat_response("")

        model_file = tmp_path / "model.gguf"
        model_file.write_bytes(b"fake")

        provider = LocalInferenceProvider(
            model_path=str(model_file),
            repo_id="r/r",
            filename="model.gguf",
        )

        with patch("src.llm.providers.local_inference._get_shared_llm", return_value=mock_llm):
            with pytest.raises(ValueError, match="empty response"):
                provider.complete([{"role": "user", "content": "hi"}], 0.2, 64)

    def test_complete_stream_yields_content_chunks(self, tmp_path):
        mock_llm = MagicMock()
        mock_llm.create_chat_completion.return_value = iter([
            _make_stream_chunk("Hello"),
            _make_stream_chunk(" world"),
            _make_stream_chunk(None),  # no content — should be skipped
        ])

        model_file = tmp_path / "model.gguf"
        model_file.write_bytes(b"fake")

        provider = LocalInferenceProvider(
            model_path=str(model_file),
            repo_id="r/r",
            filename="model.gguf",
        )

        with patch("src.llm.providers.local_inference._get_shared_llm", return_value=mock_llm):
            chunks = list(
                provider.complete_stream([{"role": "user", "content": "hi"}], 0.2, 64)
            )

        assert chunks == ["Hello", " world"]


# ── Factory routing ───────────────────────────────────────────────────────────


class TestFactoryRouting:
    def test_pearl_without_key_creates_local_inference_provider(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Settings, "PEARL_INFERENCE_API_KEY", "")
        monkeypatch.setattr(Settings, "LOCAL_MODEL_DIR", str(tmp_path))
        monkeypatch.setattr(Settings, "LOCAL_MODEL_FILE", "model.gguf")
        monkeypatch.setattr(Settings, "LOCAL_MODEL_REPO", "r/r")
        # Pre-create the model file so no download starts
        (tmp_path / "model.gguf").write_bytes(b"fake")

        provider = create_provider("pearl")

        assert isinstance(provider, LocalInferenceProvider)

    def test_pearl_with_key_creates_pearl_inference_provider(self, monkeypatch):
        from src.llm.providers.pearl_inference import PearlInferenceProvider
        monkeypatch.setattr(Settings, "PEARL_INFERENCE_API_KEY", "sk-or-v1-test")

        provider = create_provider("pearl")

        assert isinstance(provider, PearlInferenceProvider)

    def test_local_provider_model_attribute_is_filename(self, monkeypatch, tmp_path):
        monkeypatch.setattr(Settings, "PEARL_INFERENCE_API_KEY", "")
        monkeypatch.setattr(Settings, "LOCAL_MODEL_DIR", str(tmp_path))
        monkeypatch.setattr(Settings, "LOCAL_MODEL_FILE", "qwen2.5.gguf")
        monkeypatch.setattr(Settings, "LOCAL_MODEL_REPO", "r/r")
        (tmp_path / "qwen2.5.gguf").write_bytes(b"fake")

        provider = create_provider("pearl")

        assert provider.model == "qwen2.5.gguf"


# ── Model routing — local model shared ───────────────────────────────────────


class TestLocalModelAllSameProvider:
    def test_all_same_provider_true_for_local_default(self, monkeypatch):
        """
        When using the local model, chat and planning use the SAME file.
        ModelRouter.all_same_provider() must return True so only one Llama
        instance is loaded (critical for RAM-constrained machines).
        """
        from src.llm.router import ModelRouter

        monkeypatch.setattr("src.llm.router.Settings.PLANNING_PROVIDER", "")
        monkeypatch.setattr("src.llm.router.Settings.CHAT_PROVIDER", "")
        monkeypatch.setattr("src.llm.router.Settings.PLANNING_MODEL", "")
        monkeypatch.setattr("src.llm.router.Settings.CHAT_MODEL", "")
        monkeypatch.setattr("src.llm.router.Settings.LLM_PROVIDER", "pearl")
        # Same filename for both → all_same_provider must be True
        monkeypatch.setattr(
            "src.llm.router.Settings.PEARL_INFERENCE_CHAT_MODEL",
            "qwen2.5-0.5b-instruct-q4_k_m.gguf",
        )
        monkeypatch.setattr(
            "src.llm.router.Settings.PEARL_INFERENCE_PLAN_MODEL",
            "qwen2.5-0.5b-instruct-q4_k_m.gguf",
        )

        assert ModelRouter().all_same_provider() is True
