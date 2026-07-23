import pytest

from src.llm.tool_selector import LLMToolSelector
from src.tools.metadata import tool
from src.tools.registry import ToolRegistry


@tool(
    description="Read a file.",
    parameters={"path": "str"},
    returns="str",
)
def read_file(path: str) -> str:
    return path


def build_selector() -> LLMToolSelector:
    registry = ToolRegistry()
    registry.register(read_file)
    return LLMToolSelector(registry)


def test_select_accepts_known_arguments(monkeypatch):
    selector = build_selector()

    monkeypatch.setattr(
        selector.client,
        "generate_json",
        lambda prompt: {"tool": "read_file", "arguments": {"path": "a.txt"}},
    )

    tool_call = selector.select("read a.txt")

    assert tool_call.tool_name == "read_file"
    assert tool_call.kwargs == {"path": "a.txt"}


def test_select_rejects_unexpected_arguments(monkeypatch):
    selector = build_selector()

    monkeypatch.setattr(
        selector.client,
        "generate_json",
        lambda prompt: {
            "tool": "read_file",
            "arguments": {"path": "a.txt", "encoding": "utf-8"},
        },
    )

    with pytest.raises(ValueError):
        selector.select("read a.txt")


def test_select_rejects_unknown_tool(monkeypatch):
    selector = build_selector()

    monkeypatch.setattr(
        selector.client,
        "generate_json",
        lambda prompt: {"tool": "delete_everything", "arguments": {}},
    )

    with pytest.raises(ValueError):
        selector.select("delete everything")


def test_select_allows_none_tool(monkeypatch):
    selector = build_selector()

    monkeypatch.setattr(
        selector.client,
        "generate_json",
        lambda prompt: {"tool": "none", "arguments": {}},
    )

    tool_call = selector.select("say hello")

    assert tool_call.tool_name == "none"
