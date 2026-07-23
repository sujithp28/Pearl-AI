from pathlib import Path

from src.tools.metadata import tool


@tool("Read the contents of a UTF-8 text file.")
def read_file(path: str) -> str:
    """
    Read a UTF-8 text file.
    """
    return Path(path).read_text(encoding="utf-8")