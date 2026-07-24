import pytest

from src.llm.parser import ToolParser


def test_parse_valid_tool_call():
    parser = ToolParser()

    tool_call = parser.parse('{"tool": "read_file", "arguments": {"path": "a.txt"}}')

    assert tool_call.tool_name == "read_file"
    assert tool_call.kwargs == {"path": "a.txt"}


def test_parse_rejects_non_dict_json():
    parser = ToolParser()

    with pytest.raises(ValueError):
        parser.parse("[1, 2, 3]")


def test_parse_plan_returns_ordered_tool_calls():
    parser = ToolParser()

    steps = parser.parse_plan(
        '{"steps": ['
        '{"tool": "read_file", "arguments": {"path": "a.txt"}},'
        '{"tool": "write_file", "arguments": {"path": "b.txt", "content": "hi"}}'
        "]}"
    )

    assert [step.tool_name for step in steps] == ["read_file", "write_file"]
    assert steps[0].kwargs == {"path": "a.txt"}
    assert steps[1].kwargs == {"path": "b.txt", "content": "hi"}


def test_parse_plan_rejects_missing_steps_key():
    parser = ToolParser()

    with pytest.raises(ValueError):
        parser.parse_plan('{"tool": "read_file", "arguments": {}}')


def test_parse_plan_rejects_empty_steps():
    parser = ToolParser()

    with pytest.raises(ValueError):
        parser.parse_plan('{"steps": []}')


def test_parse_plan_rejects_non_list_steps():
    parser = ToolParser()

    with pytest.raises(ValueError):
        parser.parse_plan('{"steps": "read_file"}')


def test_parse_plan_rejects_malformed_step():
    parser = ToolParser()

    with pytest.raises(ValueError):
        parser.parse_plan('{"steps": [{"tool": "read_file"}]}')
