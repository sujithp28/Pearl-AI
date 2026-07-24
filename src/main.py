"""
Pearl AI Coding Agent.

Application entry point.
"""

from __future__ import annotations

import logging

from src.agent import PearlAgent
from src.tools.edit_tools import (
    create_file,
    edit_lines,
    patch_file,
    replace_in_file,
)
from src.tools.file_tools import (
    append_file,
    delete_file,
    file_exists,
    file_size,
    list_directory,
    make_directory,
    read_file,
    write_file,
)
from src.tools.registry import ToolRegistry
from src.tools.repo_tools import (
    explain_file,
    find_references,
    find_symbol,
    index_repository,
    search_text,
    summarize_project,
)
from src.tools.shell_tools import (
    current_user,
    execute_shell,
    is_command_available,
    ls,
    operating_system,
    pwd,
    run_python,
    which,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(message)s",
)


def build_registry() -> ToolRegistry:
    """
    Create the registry and register all available tools.
    """

    registry = ToolRegistry()

    # File tools
    registry.register(read_file)
    registry.register(write_file)
    registry.register(append_file)
    registry.register(file_exists)
    registry.register(list_directory)
    registry.register(make_directory)
    registry.register(delete_file)
    registry.register(file_size)

    # Edit tools
    registry.register(create_file)
    registry.register(replace_in_file)
    registry.register(edit_lines)
    registry.register(patch_file)

    # Repository intelligence tools
    registry.register(index_repository)
    registry.register(find_symbol)
    registry.register(find_references)
    registry.register(search_text)
    registry.register(summarize_project)
    registry.register(explain_file)

    # Shell tools
    registry.register(execute_shell)
    registry.register(run_python)
    registry.register(pwd)
    registry.register(ls)
    registry.register(which)
    registry.register(is_command_available)
    registry.register(operating_system)
    registry.register(current_user)

    return registry


def main() -> None:
    """
    Start Pearl.
    """

    print("=" * 60)
    print(" Pearl AI Coding Agent")
    print("=" * 60)

    registry = build_registry()

    agent = PearlAgent(registry)

    print(f"Loaded {len(registry)} tools.")
    print("Type 'exit' to quit.\n")

    while True:
        try:
            prompt = input("Pearl > ").strip()

            if not prompt:
                continue

            if prompt.lower() in {
                "exit",
                "quit",
            }:
                print("Goodbye.")
                break

            result = agent.run(prompt)

            print()
            print(result)
            print()

        except KeyboardInterrupt:
            print("\nGoodbye.")
            break

        except Exception as exc:
            print(f"\nError: {exc}\n")


if __name__ == "__main__":
    main()