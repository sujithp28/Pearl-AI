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
    # Fail closed. A tool that never declared a tier is treated as the
    # most restrictive one, so forgetting the decorator cannot silently
    # grant a tool auto-approval in headless mode.
    risk_level: RiskLevel = "dangerous"

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


# Ordering of the risk tiers, least to most restrictive. Used to answer
# "how risky was this batch, overall" — a set of operations is as risky
# as its riskiest member, never as its average.
_RISK_ORDER: dict[RiskLevel, int] = {"safe": 0, "staged": 1, "dangerous": 2}


def max_risk(*levels: RiskLevel) -> RiskLevel:
    """
    Return the most restrictive of `levels`.

    Returns "safe" when called with nothing, which is the correct answer
    for "no risky operation happened" rather than a fail-open default:
    the caller has a risk to report only if a tool produced one.

    An unrecognised level is treated as the most restrictive, so a tier
    added later without updating this ordering fails closed.
    """
    if not levels:
        return "safe"
    return max(levels, key=lambda level: _RISK_ORDER.get(level, len(_RISK_ORDER)))
