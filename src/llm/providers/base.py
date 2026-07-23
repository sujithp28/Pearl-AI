"""
Provider abstraction for Pearl's LLM client.

Every provider backend implements the same minimal interface, so
`LLMClient` (and everything built on it: the tool selector, the
planner, `PearlAgent.chat`) works identically regardless of which
LLM backend is configured.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
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
