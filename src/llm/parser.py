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


class ParseError(ValueError):
    """
    Structured error raised when ToolParser cannot parse an LLM response.

    Subclasses ValueError so existing callers that catch ValueError
    continue to work. The structured fields allow callers that want to
    distinguish causes (e.g. retry on invalid JSON vs. replan on missing
    fields) to do so without string-matching the message.

    Attributes
    ----------
    reason : str
        Machine-readable error category:
          "invalid_json"    — response was not valid JSON
          "not_a_dict"      — JSON was valid but not an object
          "missing_steps"   — plan had no 'steps' key or an empty list
          "missing_fields"  — a step was missing a required field
          "invalid_type"    — a field had an unexpected type
          "not_a_step"      — a plan step was not a JSON object
    raw_response : str
        The response that failed, capped at 500 chars for logging.
    field : str | None
        The field name involved, when the reason is field-specific.
    """

    _CAP = 500

    def __init__(
        self,
        reason: str,
        raw_response: str,
        *,
        field: str | None = None,
    ) -> None:
        self.reason = reason
        self.raw_response = raw_response[: self._CAP]
        self.field = field

        detail = f" (field={field!r})" if field else ""
        super().__init__(f"ParseError[{reason}]{detail}: {raw_response[:100]!r}")


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
            raise ParseError("invalid_json", response) from exc

        if not isinstance(payload, dict):
            raise ParseError("not_a_dict", response)

        return self._parse_step(payload, raw=response)

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
            raise ParseError("invalid_json", response) from exc

        if not isinstance(payload, dict):
            raise ParseError("not_a_dict", response)

        steps = payload.get("steps")

        if not isinstance(steps, list) or not steps:
            raise ParseError("missing_steps", response, field="steps")

        return [self._parse_step(step, raw=response) for step in steps]

    def _parse_step(self, step: Any, raw: str = "") -> ToolCall:
        """
        Parse a single {"tool": ..., "arguments": ...} object.
        """

        if not isinstance(step, dict):
            raise ParseError("not_a_step", raw)

        missing = self.REQUIRED_FIELDS - step.keys()

        if missing:
            raise ParseError("missing_fields", raw, field=", ".join(sorted(missing)))

        tool_name = step["tool"]
        arguments = step.get("arguments", {})

        if not isinstance(tool_name, str):
            raise ParseError("invalid_type", raw, field="tool")

        if not isinstance(arguments, dict):
            raise ParseError("invalid_type", raw, field="arguments")

        step_id = step.get("id")
        if step_id is not None and not isinstance(step_id, str):
            raise ParseError("invalid_type", raw, field="id")

        depends_on_raw = step.get("depends_on", [])
        if not isinstance(depends_on_raw, list):
            raise ParseError("invalid_type", raw, field="depends_on")
        for entry in depends_on_raw:
            if not isinstance(entry, str):
                raise ParseError("invalid_type", raw, field="depends_on")

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
