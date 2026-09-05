"""
LLM Client for Pearl.

Provider-agnostic: the active backend (OpenAI, Claude, Gemini,
OpenRouter, or any other OpenAI-compatible endpoint) is selected via
`src.llm.providers`.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable

from src.config.settings import Settings
from src.llm.providers.base import LLMProvider
from src.llm.providers.factory import create_provider

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0

#: Granularity of the interruptible retry-backoff wait — see
#: `_interruptible_sleep`.
_CANCEL_POLL_INTERVAL = 0.1


class LLMCancelled(Exception):
    """
    Raised when `cancel_check` reports cancellation before a request
    is sent, or during the retry backoff wait between attempts.

    Deliberately does NOT interrupt a request already in flight to the
    provider — a synchronous HTTP call has no interruption point once
    sent short of closing the socket out from under it, which is a
    materially bigger change than this cleanup pass covers. Cancelling
    before a request starts, and during backoff between retries, is
    the honest scope of what cooperative cancellation can offer here.
    """


def _interruptible_sleep(
    seconds: float, cancel_check: Callable[[], bool] | None
) -> None:
    """
    Sleep for `seconds`, but return early — by raising `LLMCancelled`
    — the moment `cancel_check` reports cancellation.

    Polls in small increments rather than one blocking `time.sleep`,
    so a cancellation during retry backoff (the one waiting period
    `LLMClient` actually controls) takes effect within
    `_CANCEL_POLL_INTERVAL`, not after the full delay.
    """

    if cancel_check is None:
        time.sleep(seconds)
        return

    remaining = seconds

    while remaining > 0:
        if cancel_check():
            raise LLMCancelled("Cancelled during retry backoff.")

        step = min(_CANCEL_POLL_INTERVAL, remaining)
        time.sleep(step)
        remaining -= step


class LLMClient:
    """
    Provider-agnostic LLM client.

    Delegates the actual completion call to an `LLMProvider` backend, so the
    tool selector, planner, and `PearlAgent.chat` do not need to know which
    provider is active.
    """

    def __init__(
        self,
        provider: LLMProvider | None = None,
        provider_name: str | None = None,
    ) -> None:
        name = provider_name or Settings.LLM_PROVIDER

        logger.info("Connecting to LLM provider: %s", name)

        self.provider = provider or create_provider(name)

        logger.info(
            "Connected to provider: %s",
            type(self.provider).__name__,
        )

    def generate(
        self,
        prompt: str,
        temperature: float | None = None,
        max_new_tokens: int | None = None,
        history: list[dict[str, str]] | None = None,
        system: str | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> str:
        """
        Send prompt to the active provider and return its response.

        `history` is prior conversation turns (oldest first, in the
        standard `[{"role": ..., "content": ...}]` shape — see
        `Memory.recent_messages`) to send ahead of `prompt`, so the
        model can actually see what was already said.

        `system` is a system-role message sent ahead of everything
        else — used by the chat path to tell the model what Pearl is
        and what it can't do (see `src/prompts/system.py`).

        Both omitted (the default) sends `prompt` alone, exactly as
        before — which is what every non-conversational caller wants:
        `generate_json`, and through it all planning/replanning, must
        stay stateless and unprimed, or a plan would start depending
        on unrelated chat history or on prose written for a human.

        `cancel_check`, if given, is polled before the first attempt
        and during the wait between retries, raising `LLMCancelled`
        the moment it returns True — see `LLMCancelled` for the honest
        scope of what this can and can't interrupt.

        Retries transient API/network failures with exponential backoff.
        """

        if cancel_check is not None and cancel_check():
            raise LLMCancelled("Cancelled before the request was sent.")

        if temperature is None:
            temperature = Settings.TEMPERATURE

        if max_new_tokens is None:
            max_new_tokens = Settings.MAX_NEW_TOKENS

        messages = [
            *([{"role": "system", "content": system}] if system else []),
            *(history or []),
            {
                "role": "user",
                "content": prompt,
            },
        ]

        text = None
        attempt = 0

        while text is None:
            logger.info("Sending request to provider...")

            try:
                text = self.provider.complete(
                    messages,
                    temperature,
                    max_new_tokens,
                )

                logger.info("Received response from provider.")

            except self.provider.TRANSIENT_ERRORS as exc:
                attempt += 1

                if attempt > MAX_RETRIES:
                    logger.exception(
                        "Provider request failed after %d attempts.",
                        attempt,
                    )
                    raise

                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))

                logger.warning(
                    "Transient provider error (attempt %d/%d): %s. "
                    "Retrying in %.1fs...",
                    attempt,
                    MAX_RETRIES,
                    exc,
                    delay,
                )

                _interruptible_sleep(delay, cancel_check)

            except Exception:
                logger.exception("Provider request failed.")
                raise

        return text

    def generate_stream(
        self,
        prompt: str,
        history: list[dict[str, str]] | None = None,
        system: str | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> Iterator[str]:
        """
        Stream the provider response for `prompt`, yielding text chunks
        as they arrive.

        Same message assembly as `generate()` — system prompt, history,
        then `prompt` — but delegates to `provider.complete_stream()`
        so the first token is visible immediately rather than after the
        entire response is buffered.

        Only suitable for the chat path where partial text is
        displayable. Planning and tool-call paths must use
        `generate_json()` (which needs the complete response to parse
        JSON) and are not affected by this method.
        """

        if cancel_check is not None and cancel_check():
            raise LLMCancelled("Cancelled before the request was sent.")

        temperature = Settings.TEMPERATURE
        max_new_tokens = Settings.MAX_NEW_TOKENS

        messages: list[dict[str, str]] = [
            *([{"role": "system", "content": system}] if system else []),
            *(history or []),
            {"role": "user", "content": prompt},
        ]

        logger.info("Streaming request to provider...")

        yield from self.provider.complete_stream(messages, temperature, max_new_tokens)

        logger.info("Stream finished.")

    def complete_raw(
        self,
        prompt: str,
        temperature: float | None = None,
        max_new_tokens: int | None = None,
        stop: list[str] | None = None,
    ) -> str:
        """
        Continue `prompt` as plain text, with no chat template and no
        message assembly (no system prompt, no history) — unlike
        `generate()`, `prompt` is sent exactly as given.

        Built for inline code completion: `generate()`'s chat framing
        makes an instruct model respond *about* a code fragment (often
        refusing it outright) rather than continuing it. This calls
        `provider.complete_raw()`, which for `LocalInferenceProvider`
        uses llama.cpp's real completion API; other providers fall back
        to a best-effort single-turn instruction (see
        `LLMProvider.complete_raw`'s default).

        No retry/backoff: autocomplete callers need to fail fast and
        move on, not spend the completion's latency budget retrying.
        """
        if temperature is None:
            temperature = Settings.TEMPERATURE

        if max_new_tokens is None:
            max_new_tokens = Settings.MAX_NEW_TOKENS

        return self.provider.complete_raw(prompt, temperature, max_new_tokens, stop)

    def _extract_json(self, text: str) -> str:
        """
        Extract JSON from model output.

        Supports:
        - Plain JSON
        - Markdown JSON
        - Explanatory text containing JSON
        """

        text = re.sub(r"```json", "", text, flags=re.IGNORECASE)
        text = text.replace("```", "").strip()

        try:
            json.loads(text)
            return text
        except Exception:
            pass

        candidate = self._find_balanced_json_object(text)

        if candidate is not None:
            return candidate

        raise ValueError(f"No JSON object found.\n\nModel returned:\n{text}")

    @staticmethod
    def _find_balanced_json_object(text: str) -> str | None:
        """
        Find the first balanced `{...}` object in `text`.

        Scans brace depth (ignoring braces inside string literals)
        instead of using a greedy regex, so trailing prose containing
        braces cannot be captured into the match.
        """

        start = text.find("{")

        if start == -1:
            return None

        depth = 0
        in_string = False
        escape = False

        for index in range(start, len(text)):
            char = text[index]

            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1

                if depth == 0:
                    return text[start : index + 1]

        return None

    def generate_json(
        self,
        prompt: str,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """
        Generate JSON from the LLM.
        """

        response = self.generate(prompt, cancel_check=cancel_check)

        logger.debug("Raw model response: %s", response)

        cleaned = self._extract_json(response)

        logger.debug("Cleaned JSON: %s", cleaned)

        try:
            payload = json.loads(cleaned)

            logger.debug("Parsed payload: %s", payload)

            return payload

        except json.JSONDecodeError as exc:
            logger.debug("Invalid JSON from model: %s", cleaned)

            raise ValueError("Model returned invalid JSON.") from exc

    @staticmethod
    def save_response(
        response: str,
        path: str,
    ) -> None:
        """
        Save model response to a file.
        """

        Path(path).write_text(
            response,
            encoding="utf-8",
        )

    @staticmethod
    def load_prompt(
        path: str,
    ) -> str:
        """
        Load prompt template from disk.
        """

        return Path(path).read_text(
            encoding="utf-8",
        )
