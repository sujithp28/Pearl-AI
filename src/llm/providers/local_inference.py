"""
Local inference provider for Pearl — zero-configuration AI.

Runs a real open-weight model (GGUF format) locally via llama-cpp-python.
No API key required.  Model is downloaded from HuggingFace on first run.

DEFAULT MODEL
-------------
Qwen2.5-0.5B-Instruct Q4_K_M
  Repo:    Qwen/Qwen2.5-0.5B-Instruct-GGUF
  File:    qwen2.5-0.5b-instruct-q4_k_m.gguf
  Size:    ~491 MB
  License: Apache 2.0
  Speed:   18-30 tok/s on i5-10300H (CPU, AVX2)
  Context: 32 768 tokens
  Runtime: llama-cpp-python (CPU; CUDA path auto-enabled when toolkit present)

Override via .env:
  LOCAL_MODEL_REPO=Qwen/Qwen2.5-1.5B-Instruct-GGUF
  LOCAL_MODEL_FILE=qwen2.5-1.5b-instruct-q4_k_m.gguf

DEPENDENCIES
------------
  pip install llama-cpp-python \\
      --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu

huggingface_hub is already in Pearl's dependency tree and handles the download.

SINGLETON
---------
The Llama model is expensive to load and consumes most of the available RAM.
A module-level singleton ensures it is loaded once regardless of how many
LocalInferenceProvider instances are created (ModelRouter may create two).
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from src.llm.providers.base import LLMProvider

logger = logging.getLogger(__name__)

# ── Shared Llama singleton ────────────────────────────────────────────────────

_llm_lock = threading.Lock()
_llm: Any = None  # llama_cpp.Llama, set on first use


def _get_shared_llm(model_path: str, n_ctx: int, n_threads: int) -> Any:
    global _llm
    with _llm_lock:
        if _llm is None:
            try:
                from llama_cpp import Llama
            except ImportError as exc:
                raise ImportError(
                    "llama-cpp-python is required for local inference.\n"
                    "Install it with:\n"
                    "  pip install llama-cpp-python "
                    "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu"
                ) from exc

            logger.info("Loading local model: %s", model_path)
            _llm = Llama(
                model_path=model_path,
                n_ctx=n_ctx,
                n_threads=n_threads,
                verbose=False,
            )
            logger.info("Local model ready.")
        return _llm


# ── Model download ────────────────────────────────────────────────────────────

class ModelDownloadError(RuntimeError):
    """Raised when the model file cannot be downloaded."""


def _download_model(repo_id: str, filename: str, local_dir: str) -> str:
    """
    Download the GGUF model file from HuggingFace.

    Returns the local path.  huggingface_hub handles caching — if the file
    already exists it is returned instantly without re-downloading.
    """
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise ModelDownloadError(
            "huggingface_hub is required to download the model.\n"
            "Install it: pip install huggingface_hub"
        ) from exc

    try:
        return hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            local_dir=local_dir,
        )
    except Exception as exc:
        raise ModelDownloadError(
            f"Failed to download {filename} from {repo_id}: {exc}\n"
            "Check your internet connection and try again."
        ) from exc


# ── Provider ──────────────────────────────────────────────────────────────────

class LocalModelNotReadyError(RuntimeError):
    """Raised when the model is still downloading or failed to load."""


class LocalInferenceProvider(LLMProvider):
    """
    Zero-configuration Pearl inference — real model, no API key.

    Lifecycle
    ---------
    1. __init__  — starts background download if model not cached; returns immediately.
    2. complete / complete_stream — waits for download + load (one-time), then infers.

    The Llama model is shared across all instances (module-level singleton) so
    two ModelRouter clients (chat + planning) do not double-load into RAM.
    """

    TRANSIENT_ERRORS = ()  # llama_cpp raises plain RuntimeError on transient issues

    def __init__(
        self,
        model_path: str,
        repo_id: str,
        filename: str,
        n_ctx: int = 2048,
        n_threads: int | None = None,
    ) -> None:
        self._model_path = model_path
        self._repo_id = repo_id
        self._filename = filename
        self._n_ctx = n_ctx
        self._n_threads = n_threads or max(1, (os.cpu_count() or 4) - 1)
        self.model = filename  # surfaced by ModelRouter / provider_info

        # _ready is set when the model file is on disk (download complete or already cached)
        self._ready = threading.Event()
        self._error: Exception | None = None

        if Path(model_path).exists():
            logger.info("Local model found: %s", model_path)
            self._ready.set()
        else:
            logger.info(
                "Local model not found; starting background download of %s/%s",
                repo_id,
                filename,
            )
            threading.Thread(
                target=self._background_download,
                daemon=True,
                name="pearl-model-download",
            ).start()

    def _background_download(self) -> None:
        local_dir = str(Path(self._model_path).parent)
        try:
            _download_model(self._repo_id, self._filename, local_dir)
            logger.info("Model download complete: %s", self._model_path)
        except Exception as exc:
            self._error = exc
            logger.error("Model download failed: %s", exc)
        finally:
            self._ready.set()

    def _get_llm(self) -> Any:
        # Wait up to 10 minutes for download; typical ~350-1100 MB over broadband
        if not self._ready.wait(timeout=600):
            raise LocalModelNotReadyError(
                "Model download timed out after 10 minutes. "
                "Check your internet connection and try again."
            )
        if self._error is not None:
            raise LocalModelNotReadyError(
                f"Model could not be loaded: {self._error}"
            ) from self._error
        return _get_shared_llm(self._model_path, self._n_ctx, self._n_threads)

    def complete(
        self,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        llm = self._get_llm()
        result = llm.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=False,
        )
        content = result["choices"][0]["message"].get("content", "")
        if not content:
            raise ValueError("Local model returned an empty response.")
        return content.strip()

    def complete_stream(
        self,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> Iterator[str]:
        llm = self._get_llm()
        for chunk in llm.create_chat_completion(
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            stream=True,
        ):
            delta = chunk["choices"][0]["delta"]
            if "content" in delta and delta["content"]:
                yield delta["content"]
