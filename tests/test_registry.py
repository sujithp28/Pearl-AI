from src.tools import ToolRegistry
from src.tools.file_tools import read_file
from src.tools.shell_tools import pwd


def test_register_tool():
    registry = ToolRegistry()

    registry.register(read_file)

    assert registry.has_tool("read_file")


def test_register_multiple_tools():
    registry = ToolRegistry()

    registry.register(read_file)
    registry.register(pwd)

    assert len(registry) == 2


def test_duplicate_registration():
    registry = ToolRegistry()

    registry.register(read_file)

    try:
        registry.register(read_file)
        assert False
    except ValueError:
        assert True


def test_get_tool():
    registry = ToolRegistry()

    registry.register(read_file)

    tool = registry.get_tool("read_file")

    assert tool.name == "read_file"


def test_list_tools():
    registry = ToolRegistry()

    registry.register(read_file)
    registry.register(pwd)

    tools = registry.list_tools()

    assert "read_file" in tools
    assert "pwd" in tools


def test_get_tools():
    registry = ToolRegistry()

    registry.register(read_file)

    metadata = registry.get_tools()

    assert len(metadata) == 1
    assert metadata[0]["name"] == "read_file"


def test_unregister():
    registry = ToolRegistry()

    registry.register(read_file)

    registry.unregister("read_file")

    assert not registry.has_tool("read_file")


def test_clear():
    registry = ToolRegistry()

    registry.register(read_file)
    registry.register(pwd)

    registry.clear()

    assert len(registry) == 0