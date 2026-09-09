"""
Provider backend for Anthropic's Claude models.

The `anthropic` package is an optional dependency: it is only
imported when this provider is actually instantiated, so Pearl does
not require it unless Claude is the configured provider.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from src.llm.providers.base import LLMProvider


class ClaudeProvider(LLMProvider):
    """
    Provider backend for Anthropic's Messages API.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float = 60.0,
    ) -> None:
        try:
            import anthropic as _anthropic
        except ImportError as exc:
            raise ImportError(
                "The 'anthropic' package is required to use the Claude "
                "provider. Install it with `pip install anthropic`."
            ) from exc

        self._anthropic = _anthropic
        self._api_key = api_key
        self._timeout = timeout
        self.model = model
        self._client: Any = None  # lazy — built on first use

        self.TRANSIENT_ERRORS = (
            _anthropic.APIConnectionError,
            _anthropic.APITimeoutError,
            _anthropic.RateLimitError,
            _anthropic.InternalServerError,
        )

        self.AUTH_ERRORS = (
            _anthropic.AuthenticationError,
            _anthropic.PermissionDeniedError,
        )

    def _get_client(self) -> Any:
        if self._client is None:
            if not self._api_key:
                raise ValueError(
                    "Anthropic API key is not configured. "
                    "Set ANTHROPIC_API_KEY in your .env file."
                )
            self._client = self._anthropic.Anthropic(
                api_key=self._api_key, timeout=self._timeout
            )
        return self._client

    @staticmethod
    def _split_system(
        messages: list[dict[str, Any]],
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """
        Anthropic's Messages API requires system content as a separate
        `system` parameter — passing it as a role in the messages list
        causes a validation error. Extract it here before dispatch.
        """
        system: str | None = None
        filtered: list[dict[str, Any]] = []
        for msg in messages:
            if msg.get("role") == "system":
                system = msg["content"]
            else:
                filtered.append(msg)
        return system, filtered

    def complete(
        self,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        system, user_msgs = self._split_system(messages)
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=user_msgs,
        )
        if system:
            kwargs["system"] = system

        response = self._get_client().messages.create(**kwargs)

        if not response.content:
            raise ValueError("No content returned from model.")

        text = getattr(response.content[0], "text", None)

        if not text:
            raise ValueError("Model returned an empty response.")

        return text.strip()

    def complete_stream(
        self,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> Iterator[str]:
        system, user_msgs = self._split_system(messages)
        kwargs: dict[str, Any] = dict(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=user_msgs,
        )
        if system:
            kwargs["system"] = system

        with self._get_client().messages.stream(**kwargs) as stream:
            for text_chunk in stream.text_stream:
                yield text_chunk
