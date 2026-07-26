import pytest

from src.llm.parser import ToolCall
from src.llm.validation import validate_tool_call
from src.tools.metadata import tool
from src.tools.registry import ToolRegistry


@tool(
    description="Read a file.",
    parameters={"path": "str"},
    returns="str",
)
def read_file(path: str) -> str:
    return path


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(read_file)
    return registry


def test_validate_tool_call_accepts_known_arguments():
    registry = build_registry()
    tool_call = ToolCall(tool_name="read_file", args=(), kwargs={"path": "a.txt"})

    validate_tool_call(tool_call, registry)


def test_validate_tool_call_allows_none():
    registry = build_registry()
    tool_call = ToolCall(tool_name="none", args=(), kwargs={})

    validate_tool_call(tool_call, registry)


def test_validate_tool_call_rejects_unknown_tool():
    registry = build_registry()
    tool_call = ToolCall(tool_name="delete_everything", args=(), kwargs={})

    with pytest.raises(ValueError):
        validate_tool_call(tool_call, registry)


def test_validate_tool_call_rejects_unexpected_arguments():
    registry = build_registry()
    tool_call = ToolCall(
        tool_name="read_file",
        args=(),
        kwargs={"path": "a.txt", "encoding": "utf-8"},
    )

    with pytest.raises(ValueError):
        validate_tool_call(tool_call, registry)


def test_validate_tool_call_rejects_missing_required_argument():
    registry = build_registry()
    # read_file requires "path" — omitting it must raise
    tool_call = ToolCall(tool_name="read_file", args=(), kwargs={})

    with pytest.raises(ValueError, match="missing required"):
        validate_tool_call(tool_call, registry)


def test_validate_tool_call_error_names_missing_argument():
    registry = build_registry()
    tool_call = ToolCall(tool_name="read_file", args=(), kwargs={})

    with pytest.raises(ValueError) as exc_info:
        validate_tool_call(tool_call, registry)

    assert "path" in str(exc_info.value)


@tool(
    description="Add two numbers with an optional multiplier.",
    parameters={"a": "int", "b": "int", "multiplier": "int"},
    returns="int",
)
def add_with_optional(a: int, b: int, multiplier: int = 1) -> int:
    return (a + b) * multiplier


def test_validate_tool_call_accepts_optional_argument_omitted():
    registry = ToolRegistry()
    registry.register(add_with_optional)
    # "multiplier" has a default — omitting it must not raise
    tool_call = ToolCall(
        tool_name="add_with_optional", args=(), kwargs={"a": 1, "b": 2}
    )
    validate_tool_call(tool_call, registry)  # must not raise
