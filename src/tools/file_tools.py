from pathlib import Path

from src.tools.metadata import tool


@tool("Read the contents of a UTF-8 text file.")
def read_file(path: str) -> str:
    """
    Read a UTF-8 text file.

    Args:
        path: Path to the file.

    Returns:
        File contents.
    """
    return Path(path).read_text(encoding="utf-8")


@tool("Write text to a UTF-8 file.")
def write_file(path: str, content: str) -> None:
    """
    Write text to a file.

    Args:
        path: Path to the file.
        content: Content to write.
    """
    Path(path).write_text(content, encoding="utf-8")


@tool("Append text to a UTF-8 file.")
def append_file(path: str, content: str) -> None:
    """
    Append text to a file.

    Args:
        path: Path to the file.
        content: Content to append.
    """
    with Path(path).open("a", encoding="utf-8") as file:
        file.write(content)


@tool("List directory contents.")
def list_directory(path: str = ".") -> list[str]:
    """
    List files and directories.

    Args:
        path: Directory path.

    Returns:
        Sorted list of entries.
    """
    return sorted(item.name for item in Path(path).iterdir())


@tool("Check whether a file or directory exists.")
def file_exists(path: str) -> bool:
    """
    Check if a path exists.

    Args:
        path: File or directory path.

    Returns:
        True if the path exists.
    """
    return Path(path).exists()


@tool("Create a directory if it does not exist.")
def make_directory(path: str) -> None:
    """
    Create a directory.

    Args:
        path: Directory path.
    """
    Path(path).mkdir(parents=True, exist_ok=True)