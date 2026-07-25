"""
A deterministic, offline LLM provider.

Exists so Pearl's *real* stack can be exercised end to end — a real
`python -m src.mcp` subprocess, real stdio framing, real JSON-RPC,
real planning, real tool dispatch, real `PatchManager` — with the one
genuinely non-deterministic component (the model) replaced by a fixed
script.

This is the piece that makes honest end-to-end testing possible at
all: a test in another process cannot monkeypatch the subprocess's
LLM client, so the substitution has to be selectable by configuration
from outside. Everything *except* the model stays real, which is the
opposite trade from a unit test that fakes the transport and keeps
the logic.

Also useful outside tests: set `PEARL_LLM_PROVIDER=scripted` to run
Pearl with no model server at all, e.g. when debugging the MCP
protocol or the VS Code extension.

Selected only by explicit configuration — never a fallback, so a
misconfigured real provider can never silently degrade into fake
answers.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from src.llm.providers.base import LLMProvider

logger = logging.getLogger(__name__)

#: JSON array of strings: the responses to return, in order.
RESPONSES_ENV_VAR = "PEARL_SCRIPTED_RESPONSES"

DEFAULT_RESPONSE = '{"steps": [{"tool": "none", "arguments": {}}]}'


def _load_scripted_responses() -> list[str]:
    """
    Read the response script from the environment.

    Falls back to a single valid "no tool needed" plan rather than
    raising, so an unconfigured scripted provider still produces
    something the planner can parse instead of failing in a way that
    looks like a Pearl bug.
    """

    raw = os.getenv(RESPONSES_ENV_VAR, "").strip()

    if not raw:
        return [DEFAULT_RESPONSE]

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(
            "%s is not valid JSON; using the default scripted response.",
            RESPONSES_ENV_VAR,
        )
        return [DEFAULT_RESPONSE]

    if not isinstance(parsed, list) or not all(isinstance(r, str) for r in parsed):
        logger.warning(
            "%s must be a JSON array of strings; using the default scripted response.",
            RESPONSES_ENV_VAR,
        )
        return [DEFAULT_RESPONSE]

    return parsed or [DEFAULT_RESPONSE]


class ScriptedProvider(LLMProvider):
    """
    Returns pre-set responses in order, then repeats the last one.

    Repeating rather than raising once the script runs out: a test
    asserting on the first two calls shouldn't break because some
    unrelated later code path made a third.
    """

    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = list(responses) if responses else _load_scripted_responses()
        self.calls: list[list[dict[str, Any]]] = []

    def complete(
        self,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        self.calls.append(messages)

        index = min(len(self.calls) - 1, len(self.responses) - 1)

        return self.responses[index]
