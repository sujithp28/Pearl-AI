from pathlib import Path
from shutil import which as shutil_which
from subprocess import CompletedProcess, run

from src.tools.metadata import tool


@tool("Execute a shell command.")
def execute_shell(command: str) -> CompletedProcess[str]:
    """
    Execute a shell command.

    Args:
        command: Shell command to execute.

    Returns:
        subprocess.CompletedProcess
    """
    return run(
        command,
        shell=True,
        capture_output=True,
        text=True,
    )


@tool("Execute Python code.")
def run_python(code: str) -> CompletedProcess[str]:
    """
    Execute Python code.

    Args:
        code: Python code.

    Returns:
        subprocess.CompletedProcess
    """
    return run(
        ["python3", "-c", code],
        capture_output=True,
        text=True,
    )


@tool("Return the current working directory.")
def pwd() -> str:
    """
    Return the current working directory.
    """
    return str(Path.cwd())


@tool("List directory contents.")
def ls(
    path: str = ".",
    show_hidden: bool = False,
) -> list[str]:
    """
    List files in a directory.

    Args:
        path: Directory path.
        show_hidden: Include hidden files.

    Returns:
        List of file and directory names.
    """
    entries = []

    for item in Path(path).iterdir():
        if not show_hidden and item.name.startswith("."):
            continue
        entries.append(item.name)

    return sorted(entries)


@tool("Locate an executable in PATH.")
def which(command: str) -> str | None:
    """
    Locate an executable.

    Args:
        command: Command name.

    Returns:
        Absolute executable path or None.
    """
    return shutil_which(command)


@tool("Check whether a command is available.")
def is_command_available(command: str) -> bool:
    """
    Check if a command exists.

    Args:
        command: Command name.

    Returns:
        True if available.
    """
    return shutil_which(command) is not None