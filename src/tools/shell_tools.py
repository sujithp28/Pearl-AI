from pathlib import Path
import shutil
import subprocess


def execute_shell(command: str) -> str:
    """
    Execute a shell command and return its output.
    """
    result = subprocess.run(
        command,
        shell=True,
        text=True,
        capture_output=True,
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())

    return result.stdout.strip()


def run_python(script: str) -> str:
    """
    Execute Python code and return its output.
    """
    result = subprocess.run(
        ["python3", "-c", script],
        text=True,
        capture_output=True,
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())

    return result.stdout.strip()


def pwd() -> str:
    """
    Return the current working directory.
    """
    return str(Path.cwd())


def ls(path: str = ".", show_hidden: bool = False) -> list[str]:
    """
    List files and directories.

    Args:
        path: Directory path.
        show_hidden: Include hidden files if True.

    Returns:
        Sorted list of file and directory names.
    """
    items = []

    for item in Path(path).iterdir():
        if not show_hidden and item.name.startswith("."):
            continue

        items.append(item.name)

    return sorted(items)


def which(program: str) -> str | None:
    """
    Return the full path of an executable.
    """
    return shutil.which(program)


def is_command_available(program: str) -> bool:
    """
    Check whether a command exists.
    """
    return shutil.which(program) is not None