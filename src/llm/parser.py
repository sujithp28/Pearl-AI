"""
LLM Response Parser.

Converts JSON returned by the LLM into a ToolCall object.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, repr=False)
class ToolCall:
    """
    Represents a tool selected by the LLM.

    `step_id` and `depends_on` are optional dependency-annotation
    fields. Plans without them parse identically to before — the
    defaults keep `ToolCall(tool_name, args, kwargs)` construction
    backward compatible.
    """

    tool_name: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    step_id: str | None = None
    depends_on: list[str] = field(default_factory=list)

    def __repr__(self) -> str:
        # Flatten kwargs into key=value pairs; truncate long string values
        # so a write_file call with 5 000 bytes of content doesn't swamp logs.
        _MAX = 60
        parts = []
        for k, v in self.kwargs.items():
            if isinstance(v, str) and len(v) > _MAX:
                parts.append(f"{k}={v[:_MAX]!r}…")
            else:
                parts.append(f"{k}={v!r}")
        kwargs_str = ", ".join(parts)
        step = f"[{self.step_id}]" if self.step_id else ""
        sep = ", " if kwargs_str else ""
        return f"ToolCall({self.tool_name}{step}{sep}{kwargs_str})"

    def to_dict(self) -> dict[str, object]:
        """
        Return a JSON-serialisable representation of this ToolCall.

        Useful for logging, checkpointing, and future conversation
        history. args is omitted — Pearl always constructs ToolCalls
        with args=() and passes everything through kwargs.
        """

        d: dict[str, object] = {
            "tool_name": self.tool_name,
            "kwargs": self.kwargs,
        }
        if self.step_id is not None:
            d["step_id"] = self.step_id
        if self.depends_on:
            d["depends_on"] = list(self.depends_on)
        return d


class ToolParser:
    """
    Parse JSON produced by the language model.
    """

    REQUIRED_FIELDS = {
        "tool",
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
        arguments = step.get("arguments", {})

        if not isinstance(tool_name, str):
            raise TypeError("Tool name must be a string.")

        if not isinstance(arguments, dict):
            raise TypeError("Arguments must be a dictionary.")

        step_id = step.get("id")
        if step_id is not None and not isinstance(step_id, str):
            raise TypeError("Step 'id' must be a string.")

        depends_on_raw = step.get("depends_on", [])
        if not isinstance(depends_on_raw, list):
            raise TypeError("'depends_on' must be a list.")
        for entry in depends_on_raw:
            if not isinstance(entry, str):
                raise TypeError("Each 'depends_on' entry must be a string.")

        return ToolCall(
            tool_name=tool_name,
            args=(),
            kwargs=arguments,
            step_id=step_id,
            depends_on=list(depends_on_raw),
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
