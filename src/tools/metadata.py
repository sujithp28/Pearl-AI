from typing import Any, Callable


def tool(description: str):
    """
    Decorator that attaches metadata to a tool function.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        func._tool_name = func.__name__
        func._tool_description = description
        return func

    return decorator