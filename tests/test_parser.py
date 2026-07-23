import pytest

from src.llm.parser import ToolParser


def test_parse_valid_tool_call():
    parser = ToolParser()

    tool_call = parser.parse(
        '{"tool": "read_file", "arguments": {"path": "a.txt"}}'
    )

    assert tool_call.tool_name == "read_file"
    assert tool_call.kwargs == {"path": "a.txt"}


def test_parse_rejects_non_dict_json():
    parser = ToolParser()

    with pytest.raises(ValueError):
        parser.parse("[1, 2, 3]")
