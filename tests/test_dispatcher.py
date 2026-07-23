import pytest

from src.agent.dispatcher import (
    ToolDispatcher,
    ToolExecutionError,
    ToolNotFoundError,
)
from src.tools.metadata import tool
from src.tools.registry import ToolRegistry


@tool(
    description="Add two numbers.",
    parameters={"a": "int", "b": "int"},
    returns="int",
)
def add(a: int, b: int) -> int:
    return a + b


@tool(description="Always fails.")
def boom() -> None:
    raise ValueError("kaboom")


def build_dispatcher() -> ToolDispatcher:
    registry = ToolRegistry()
    registry.register(add)
    registry.register(boom)
    return ToolDispatcher(registry)


def test_execute_returns_result():
    dispatcher = build_dispatcher()

    assert dispatcher.execute("add", a=1, b=2) == 3


def test_execute_unknown_tool_raises_tool_not_found_error():
    dispatcher = build_dispatcher()

    with pytest.raises(ToolNotFoundError):
        dispatcher.execute("missing")


def test_tool_not_found_error_is_value_error():
    dispatcher = build_dispatcher()

    with pytest.raises(ValueError):
        dispatcher.execute("missing")


def test_execute_wraps_tool_failure():
    dispatcher = build_dispatcher()

    with pytest.raises(ToolExecutionError) as exc_info:
        dispatcher.execute("boom")

    error = exc_info.value
    assert error.tool_name == "boom"
    assert isinstance(error.original, ValueError)
    assert isinstance(error.__cause__, ValueError)


def test_describe_tool_unknown_raises_tool_not_found_error():
    dispatcher = build_dispatcher()

    with pytest.raises(ToolNotFoundError):
        dispatcher.describe_tool("missing")
