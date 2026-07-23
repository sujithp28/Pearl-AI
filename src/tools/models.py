from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(slots=True)
class Tool:
    """
    Represents a registered tool.
    """

    name: str
    description: str
    function: Callable[..., Any]

    parameters: dict[str, str] = field(default_factory=dict)
    returns: str = "Any"