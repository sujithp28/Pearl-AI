"""
Shell tools for Pearl.

These tools provide safe wrappers around shell execution and
common operating-system commands.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from src.tools.metadata import tool

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30


@tool(
    description="Execute a shell command.",
    parameters={
        "command": "str",
    },
    returns="subprocess.CompletedProcess",
)
def execute_shell(
    command: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> subprocess.CompletedProcess:
    """
    Execute a shell command.

    Raises
    ------
    subprocess.CalledProcessError
        If the command exits with a non-zero status.
    """

    logger.info("Executing command: %s", command)

    result = subprocess.run(
        command,
        shell=True,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=True,
    )

    return result


@tool(
    description="Execute a Python script.",
    parameters={
        "script": "str",
    },
    returns="subprocess.CompletedProcess",
)
def run_python(
    script: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> subprocess.CompletedProcess:
    """
    Execute a Python script.
    """

    return execute_shell(
        f'python3 "{script}"',
        timeout=timeout,
    )


@tool(
    description="Return the current working directory.",
    returns="str",
)
def pwd() -> str:
    """
    Return current working directory.
    """

    return str(Path.cwd())


@tool(
    description="List files in the current or specified directory.",
    parameters={
        "path": "str",
        "show_hidden": "bool",
    },
    returns="list[str]",
)
def ls(
    path: str = ".",
    show_hidden: bool = False,
) -> list[str]:
    """
    List directory contents.
    """

    directory = Path(path)

    if not directory.exists():
        raise FileNotFoundError(path)

    if not directory.is_dir():
        raise NotADirectoryError(path)

    items = []

    for item in sorted(directory.iterdir()):
        if not show_hidden and item.name.startswith("."):
            continue

        items.append(item.name)

    return items


@tool(
    description="Locate a command on the current system.",
    parameters={
        "command": "str",
    },
    returns="str | None",
)
def which(command: str) -> str | None:
    """
    Locate a system executable.
    """

    return shutil.which(command)


@tool(
    description="Return whether a command exists on the system.",
    parameters={
        "command": "str",
    },
    returns="bool",
)
def is_command_available(command: str) -> bool:
    """
    Check if a command is available.
    """

    return which(command) is not None


@tool(
    description="Return the operating system name.",
    returns="str",
)
def operating_system() -> str:
    """
    Return operating system.
    """

    return execute_shell("uname -a").stdout.strip()


@tool(
    description="Return the current username.",
    returns="str",
)
def current_user() -> str:
    """
    Return current user.
    """

    return execute_shell("whoami").stdout.strip()