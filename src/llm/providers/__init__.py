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
    # branch never runs at import time.
    #
    # noqa: F401 because `__all__` is built from `_EXPORTS` at runtime,
    # which a static linter cannot resolve, so it reads these as dead.
    # They are not: they are what gives an editor completion and a type
    # checker real signatures for names that `__getattr__` supplies.
    # `test_every_exported_name_resolves` is the check that actually
    # matters — it catches a `_EXPORTS` row pointing at the wrong module,
    # which no linter would see either way.
    from .claude import ClaudeProvider  # noqa: F401
    from .factory import SUPPORTED_PROVIDERS, create_provider  # noqa: F401
    from .gemini import GeminiProvider  # noqa: F401
    from .local_inference import (  # noqa: F401
        LocalInferenceProvider,
        LocalModelNotReadyError,
    )
    from .openai_compatible import OpenAICompatibleProvider  # noqa: F401
    from .pearl_inference import (  # noqa: F401
        PearlInferenceNotConfiguredError,
        PearlInferenceProvider,
    )
    from .scripted import ScriptedProvider  # noqa: F401


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
