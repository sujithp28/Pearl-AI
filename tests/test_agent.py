from src.agent import PearlAgent
from src.tools.file_tools import (
    append_file,
    file_exists,
    list_directory,
    make_directory,
    read_file,
    write_file,
)
from src.tools.registry import ToolRegistry
from src.tools.shell_tools import (
    execute_shell,
    is_command_available,
    ls,
    pwd,
    run_python,
    which,
)


def build_agent() -> PearlAgent:
    """
    Build a fully configured Pearl agent.
    """

    registry = ToolRegistry()

    # File tools
    registry.register(read_file)
    registry.register(write_file)
    registry.register(append_file)
    registry.register(list_directory)
    registry.register(file_exists)
    registry.register(make_directory)

    # Shell tools
    registry.register(execute_shell)
    registry.register(run_python)
    registry.register(pwd)
    registry.register(ls)
    registry.register(which)
    registry.register(is_command_available)

    return PearlAgent(registry)


def main():
    agent = build_agent()

    print("=" * 60)
    print("🦪 Pearl Test Runner")
    print("=" * 60)

    print(agent.run("Read README.md")[:500])


if __name__ == "__main__":
    main()
