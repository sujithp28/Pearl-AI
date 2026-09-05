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
from collections import OrderedDict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from src.llm.errors import ContextLengthError
from src.llm.providers.base import LLMProvider

logger = logging.getLogger(__name__)

# ── Shared Llama cache ────────────────────────────────────────────────────────
#
# Keyed by (model_path, n_ctx) rather than being a single global object.
#
# The original design was one global Llama so that two ModelRouter clients
# (chat + planning) could not double-load the same weights into RAM. That
# intent is preserved — identical (path, n_ctx) still returns one shared
# instance — but keying makes it possible to hold a *different* model at
# the same time, which inline autocomplete requires: it needs a small,
# fast model while planning uses the large one.
#
# The cost is real: two models means two models' worth of RAM (a 1.5B and
# a 0.5B is roughly 1.6 GB). LOCAL_MAX_LOADED_MODELS bounds it, evicting
# least-recently-used, so a misconfiguration cannot exhaust memory.

_cache_lock = threading.Lock()
_llms: "OrderedDict[tuple[str, int], Any]" = OrderedDict()

# Per-model inference locks. llama_cpp.Llama is not thread-safe for
# concurrent calls *on one object*, but two distinct objects are
# independent — so locking per model, rather than globally, lets a fast
# autocomplete run while a slow planning call is still in flight. A single
# global lock would make autocomplete queue behind planning and feel dead.
_inference_locks: dict[tuple[str, int], threading.Lock] = {}


def _model_key(model_path: str, n_ctx: int) -> tuple[str, int]:
    return (str(Path(model_path).resolve()), n_ctx)


def _get_inference_lock(model_path: str, n_ctx: int) -> threading.Lock:
    """Return the lock guarding inference on one specific model."""
    key = _model_key(model_path, n_ctx)
    with _cache_lock:
        if key not in _inference_locks:
            _inference_locks[key] = threading.Lock()
        return _inference_locks[key]


def _get_shared_llm(model_path: str, n_ctx: int, n_threads: int) -> Any:
    """
    Return the shared ``Llama`` for (model_path, n_ctx), loading it once.

    Repeated calls with the same key return the same object, so weights
    are never loaded twice.
    """
    from src.config.settings import Settings

    key = _model_key(model_path, n_ctx)

    with _cache_lock:
        if key in _llms:
            _llms.move_to_end(key)  # mark most-recently-used
            return _llms[key]

        try:
            from llama_cpp import Llama
        except ImportError as exc:
            raise ImportError(
                "llama-cpp-python is required for local inference.\n"
                "Install it with:\n"
                "  pip install llama-cpp-python "
                "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu"
            ) from exc

        logger.info("Loading local model: %s (requested n_ctx=%d)", model_path, n_ctx)
        llm = Llama(
            model_path=model_path,
            n_ctx=n_ctx,
            n_threads=n_threads,
            verbose=False,
        )
        # Report the n_ctx the runtime actually allocated — authoritative value.
        actual_ctx = llm.n_ctx() if hasattr(llm, "n_ctx") else n_ctx
        logger.info("Local model ready (actual n_ctx=%d).", actual_ctx)

        _llms[key] = llm

        # Evict least-recently-used beyond the cap so a bad config cannot
        # keep loading models until the machine runs out of memory.
        max_loaded = max(1, Settings.LOCAL_MAX_LOADED_MODELS)
        while len(_llms) > max_loaded:
            evicted_key, evicted = _llms.popitem(last=False)
            logger.info("Evicting local model from cache: %s", evicted_key[0])
            try:
                evicted.close()
            except Exception:
                # Not all llama_cpp builds expose close(); dropping the
                # reference is enough for GC to reclaim the weights.
                pass
            _inference_locks.pop(evicted_key, None)

        return llm


def reset_model_cache() -> None:
    """
    Drop every cached model. Test hook — never called in production.
    """
    with _cache_lock:
        for _key, llm in _llms.items():
            try:
                llm.close()
            except Exception:
                pass
        _llms.clear()
        _inference_locks.clear()


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


# ── Context-length detection ──────────────────────────────────────────────────

_CTX_PHRASES = ("exceed context", "too long", "exceeds maximum")


def _reraise_if_context_length(exc: ValueError) -> None:
    """
    Re-raise *exc* as ``ContextLengthError`` when the message indicates the
    prompt exceeded ``n_ctx``.

    llama-cpp-python raises ``ValueError`` with messages like:
      "Requested tokens (N) exceed context window of M"
    We translate those into the typed ``ContextLengthError`` so the
    condenser can catch it without matching raw strings.

    Does nothing (returns normally) for other ValueErrors — the caller
    will re-raise the original.
    """
    msg = str(exc).lower()
    if any(phrase in msg for phrase in _CTX_PHRASES):
        raise ContextLengthError(str(exc)) from exc


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

    def _lock(self) -> threading.Lock:
        """
        Return the inference lock for *this* provider's model.

        Per-model rather than global so a fast autocomplete on the small
        model is not serialized behind a slow planning call on the large
        one — which is the whole reason two models can be resident.
        """
        return _get_inference_lock(self._model_path, self._n_ctx)

    def complete(
        self,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        llm = self._get_llm()
        try:
            with self._lock():
                result = llm.create_chat_completion(
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=False,
                )
        except ValueError as exc:
            _reraise_if_context_length(exc)
            raise
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
        try:
            with self._lock():
                for chunk in llm.create_chat_completion(
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=True,
                ):
                    delta = chunk["choices"][0]["delta"]
                    if "content" in delta and delta["content"]:
                        yield delta["content"]
        except ValueError as exc:
            _reraise_if_context_length(exc)
            raise

    def complete_raw(
        self,
        prompt: str,
        temperature: float,
        max_tokens: int,
        stop: list[str] | None = None,
    ) -> str:
        """
        Continue `prompt` via llama.cpp's raw completion API
        (`create_completion`), not `create_chat_completion`.

        This is the actual fix for autocomplete returning chat replies:
        `create_chat_completion` renders the prompt through Qwen's
        instruct template (`<|im_start|>user ... <|im_end|>`), so the
        model answers *about* the code — often refusing outright, since
        an out-of-context code fragment reads like a suspicious request
        to an instruct-tuned model. `create_completion` sends `prompt`
        as plain text with no template, so the model does what a base
        completion model does: predicts the most likely next tokens,
        which for a code prefix is the code that follows it.
        """
        llm = self._get_llm()
        try:
            with self._lock():
                result = llm.create_completion(
                    prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stop=stop or [],
                )
        except ValueError as exc:
            _reraise_if_context_length(exc)
            raise
        return result["choices"][0]["text"]
