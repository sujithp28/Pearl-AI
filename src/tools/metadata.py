from typing import Callable


def tool(
    description: str,
    parameters: dict[str, str] | None = None,
    returns: str = "Any",
):
    """
    Decorator used to attach metadata to tools.
    """

    def decorator(func: Callable):
        func._tool_name = func.__name__
        func._tool_description = description
        func._tool_parameters = parameters or {}
        func._tool_returns = returns
        return func

    return decorator