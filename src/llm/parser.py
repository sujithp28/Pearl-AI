"""
LLM Response Parser.

Converts JSON returned by the LLM into a ToolCall object.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ToolCall:
    """
    Represents a tool selected by the LLM.
    """

    tool_name: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


class ToolParser:
    """
    Parse JSON produced by the language model.
    """

    REQUIRED_FIELDS = {
        "tool",
        "arguments",
    }

    def parse(self, response: str) -> ToolCall:
        """
        Parse an LLM JSON response.

        Example
        -------
        {
            "tool": "read_file",
            "arguments": {
                "path": "README.md"
            }
        }
        """

        try:
            payload = json.loads(response)

        except json.JSONDecodeError as exc:
            raise ValueError("LLM returned invalid JSON.") from exc

        if not isinstance(payload, dict):
            raise ValueError("LLM response must be a JSON object.")

        return self._parse_step(payload)

    def parse_plan(self, response: str) -> list[ToolCall]:
        """
        Parse an LLM JSON response describing an ordered multi-step plan.

        Example
        -------
        {
            "steps": [
                {"tool": "read_file", "arguments": {"path": "a.txt"}},
                {"tool": "write_file", "arguments": {"path": "b.txt", "content": "..."}}
            ]
        }
        """

        try:
            payload = json.loads(response)

        except json.JSONDecodeError as exc:
            raise ValueError("LLM returned invalid JSON.") from exc

        if not isinstance(payload, dict):
            raise ValueError("Plan response must be a JSON object.")

        steps = payload.get("steps")

        if not isinstance(steps, list) or not steps:
            raise ValueError("Plan must contain a non-empty 'steps' list.")

        return [self._parse_step(step) for step in steps]

    def _parse_step(self, step: Any) -> ToolCall:
        """
        Parse a single {"tool": ..., "arguments": ...} object.
        """

        if not isinstance(step, dict):
            raise ValueError("Each step must be a JSON object.")

        missing = self.REQUIRED_FIELDS - step.keys()

        if missing:
            raise ValueError(f"Missing JSON fields: {missing}")

        tool_name = step["tool"]
        arguments = step["arguments"]

        if not isinstance(tool_name, str):
            raise TypeError("Tool name must be a string.")

        if not isinstance(arguments, dict):
            raise TypeError("Arguments must be a dictionary.")

        return ToolCall(
            tool_name=tool_name,
            args=(),
            kwargs=arguments,
        )

    def validate(
        self,
        response: str,
    ) -> bool:
        """
        Validate whether a response is valid JSON
        for tool execution.
        """

        try:
            self.parse(response)
            return True

        except Exception:
            return False
