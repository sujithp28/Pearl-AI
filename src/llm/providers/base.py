"""
Provider abstraction for Pearl's LLM client.

Every provider backend implements the same minimal interface, so
`LLMClient` (and everything built on it: the tool selector, the
planner, `PearlAgent.chat`) works identically regardless of which
LLM backend is configured.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any


class LLMProvider(ABC):
    """
    Common interface every LLM provider backend implements.
    """

    #: Exception types this provider's SDK raises for transient
    #: failures (network errors, rate limits, 5xx) that are worth
    #: retrying with backoff. Empty by default: no retries.
    TRANSIENT_ERRORS: tuple[type[BaseException], ...] = ()

    @abstractmethod
    def complete(
        self,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        """
        Send `messages` to the provider and return the text response.
        """

        raise NotImplementedError

    def complete_stream(
        self,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> Iterator[str]:
        """
        Stream `messages` to the provider, yielding text chunks as they
        arrive.

        Default implementation calls `complete()` and yields the whole
        response as one chunk — providers that support native streaming
        should override this to yield incrementally.
        """

        yield self.complete(messages, temperature, max_tokens)

    def complete_raw(
        self,
        prompt: str,
        temperature: float,
        max_tokens: int,
        stop: list[str] | None = None,
    ) -> str:
        """
        Continue `prompt` as plain text — no chat template, no system/
        user/assistant framing.

        This exists for inline code completion: an instruct model's chat
        template makes it answer *about* the given text ("Here's how you
        could finish that function...", or an outright refusal) rather
        than simply continuing it. Autocomplete needs the latter.

        Most hosted chat APIs have no equivalent endpoint, so the default
        implementation degrades to a single-turn chat completion with an
        explicit continuation instruction — best-effort, not a true raw
        completion. `LocalInferenceProvider` overrides this with
        llama.cpp's real completion API, which is what makes local
        autocomplete actually work rather than degrade.
        """
        instruction = (
            "Continue the following code with no explanation, no markdown "
            "fences, and no repetition of the given text — output only the "
            "continuation:\n\n" + prompt
        )
        return self.complete(
            [{"role": "user", "content": instruction}], temperature, max_tokens
        )
