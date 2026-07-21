from dataclasses import dataclass
from typing import Any, Callable


@dataclass(slots=True)
class Tool:
    """
    Represents a registered tool.
    """

    name: str
    description: str
    function: Callable[..., Any]