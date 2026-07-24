"""
Tool model definitions.

This module contains the data structures used to represent tools
registered with Pearl.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


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

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        """
        Execute the underlying tool function.
        """
        return self.function(*args, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        """
        Convert this tool into metadata suitable for LLM tool selection.
        """
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "returns": self.returns,
        }

    def __str__(self) -> str:
        return self.name
