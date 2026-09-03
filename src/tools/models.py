"""
Tool model definitions.

This module contains the data structures used to represent tools
registered with Pearl.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

RiskLevel = Literal["safe", "staged", "dangerous"]


@dataclass(slots=True)
class Tool:
    """
    Represents a callable tool that Pearl can execute.
    """

    name: str
    description: str
    function: Callable[..., Any]

    parameters: dict[str, str] = field(default_factory=dict)
    returns: str = "Any"
    risk_level: RiskLevel = "staged"

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        """
        Execute the underlying tool function.
        """
        return self.function(*args, **kwargs)

    def is_safe(self) -> bool:
        """Return True when the tool is read-only and needs no approval."""
        return self.risk_level == "safe"

    def is_dangerous(self) -> bool:
        """Return True when the tool is irreversible and always needs confirmation."""
        return self.risk_level == "dangerous"

    def to_dict(self) -> dict[str, Any]:
        """
        Convert this tool into metadata suitable for LLM tool selection.
        """
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "returns": self.returns,
            "risk_level": self.risk_level,
        }

    def __str__(self) -> str:
        return self.name
