"""
Pearl Inference Provider — the client side of Pearl's inference contract.

CONTRACT
--------
POST {PEARL_INFERENCE_BASE_URL}/chat/completions
Authorization: Bearer <PEARL_INFERENCE_API_KEY>

The wire format is OpenAI Chat Completions (industry standard).  Both
OpenRouter (development) and the future api.pearl.ai gateway (production)
speak it, so no custom serialisation is needed.

Development:   PEARL_INFERENCE_BASE_URL = https://openrouter.ai/api/v1
Production:    PEARL_INFERENCE_BASE_URL = https://api.pearl.ai/v1  [REQUIRES DEPLOYMENT]

NOT CONFIGURED
--------------
Construction succeeds even with an empty key so the server can start and
show "Setup required" in the UI.  The first actual model call raises
PearlInferenceNotConfiguredError with a clear setup message — not an
OpenAI SDK 401 or a confusing credential error.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from src.llm.providers.base import LLMProvider
from src.llm.providers.openai_compatible import OpenAICompatibleProvider


class PearlInferenceNotConfiguredError(RuntimeError):
    """
    Raised when the inference layer is invoked without a configured key.

    This fires locally before any network call, so the user sees a clear
    Pearl-branded setup message rather than a raw HTTP 401 from the remote.
    """


class PearlInferenceProvider(LLMProvider):
    """
    Pearl's first-party inference provider.

    Delegates all HTTP work to OpenAICompatibleProvider (same wire protocol),
    but intercepts the unconfigured-key case with a Pearl-specific error
    and keeps provider branding out of user-visible error messages.

    Model routing is handled by ModelRouter one layer up:
        chat     → PEARL_INFERENCE_CHAT_MODEL   (fast, conversational)
        planning → PEARL_INFERENCE_PLAN_MODEL   (accurate, structured)
    """

    # Mirror the delegate's transient-error tuple so LLMClient's retry
    # logic retries the same transient conditions.
    TRANSIENT_ERRORS = OpenAICompatibleProvider.TRANSIENT_ERRORS
    AUTH_ERRORS = OpenAICompatibleProvider.AUTH_ERRORS

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 60.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._timeout = timeout
        self.model = model
        self._delegate: OpenAICompatibleProvider | None = None

    def _get_delegate(self) -> OpenAICompatibleProvider:
        if not self._api_key:
            raise PearlInferenceNotConfiguredError(
                "Pearl inference service is not configured.\n"
                "Add PEARL_INFERENCE_API_KEY to your .env file and restart.\n"
                "Get a free key at: https://openrouter.ai/keys\n\n"
                "Note: when a hosted Pearl service is available, "
                "no key will be required."
            )
        if self._delegate is None:
            self._delegate = OpenAICompatibleProvider(
                api_key=self._api_key,
                base_url=self._base_url,
                model=self.model,
                timeout=self._timeout,
            )
        return self._delegate

    def complete(
        self,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        return self._get_delegate().complete(messages, temperature, max_tokens)

    def complete_stream(
        self,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> Iterator[str]:
        yield from self._get_delegate().complete_stream(
            messages, temperature, max_tokens
        )
