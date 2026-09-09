"""
LLM provider backends.

Every name below is importable from this package exactly as before, but
resolved on first access (PEP 562) rather than at import time.

This package used to import all seven backends eagerly, which meant any
touch of `src.llm.providers` — including `from .base import LLMProvider`,
which the client does — loaded the OpenAI SDK, and Anthropic and
llama.cpp behind it. That cost ~1.9 s, paid on every start, by every
user, including the zero-config local path that never calls OpenAI at
all.

Loading a backend is the job of `create_provider()`, which imports the
one it is about to construct. Adding a backend here means adding a row
to `_EXPORTS` — the module is named, not imported.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import LLMProvider

#: Exported name -> the submodule that defines it.
_EXPORTS: dict[str, str] = {
    "OpenAICompatibleProvider": "openai_compatible",
    "PearlInferenceProvider": "pearl_inference",
    "PearlInferenceNotConfiguredError": "pearl_inference",
    "LocalInferenceProvider": "local_inference",
    "LocalModelNotReadyError": "local_inference",
    "ClaudeProvider": "claude",
    "GeminiProvider": "gemini",
    "ScriptedProvider": "scripted",
    "create_provider": "factory",
    "SUPPORTED_PROVIDERS": "factory",
}

if TYPE_CHECKING:
    # Import eagerly for type checkers and IDE completion only; this
    # branch never runs.
    from .claude import ClaudeProvider
    from .factory import SUPPORTED_PROVIDERS, create_provider
    from .gemini import GeminiProvider
    from .local_inference import LocalInferenceProvider, LocalModelNotReadyError
    from .openai_compatible import OpenAICompatibleProvider
    from .pearl_inference import (
        PearlInferenceNotConfiguredError,
        PearlInferenceProvider,
    )
    from .scripted import ScriptedProvider


def __getattr__(name: str) -> Any:
    """
    Resolve an exported name by importing the module that defines it.

    Python calls this only for names not already in the module globals,
    so each backend is imported at most once and is a normal attribute
    afterwards.
    """

    module_name = _EXPORTS.get(name)

    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from importlib import import_module

    value = getattr(import_module(f".{module_name}", __name__), name)
    globals()[name] = value

    return value


def __dir__() -> list[str]:
    return sorted([*globals(), *_EXPORTS])


__all__ = ["LLMProvider", *_EXPORTS]
