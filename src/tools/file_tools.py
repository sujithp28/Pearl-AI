from pathlib import Path


def read_file(path: str) -> str:
    """
    Read and return the contents of a file.
    """
    return Path(path).read_text(encoding="utf-8")


def write_file(path: str, content: str) -> None:
    """
    Write content to a file.
    """
    Path(path).write_text(content, encoding="utf-8")


def append_file(path: str, content: str) -> None:
    """
    Append content to a file.
    """
    with Path(path).open("a", encoding="utf-8") as file:
        file.write(content)


def list_directory(path: str = ".") -> list[str]:
    """
    List files and directories.
    """
    return sorted(item.name for item in Path(path).iterdir())


def file_exists(path: str) -> bool:
    """
    Check whether a file or directory exists.
    """
    return Path(path).exists()


def make_directory(path: str) -> None:
    """
    Create a directory if it doesn't exist.
    """
    Path(path).mkdir(parents=True, exist_ok=True)