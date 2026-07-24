"""
Pearl Tool System.

Exports every public component required to build and register tools.
"""

from .edit_tools import (
    create_file,
    edit_lines,
    patch_file,
    replace_in_file,
)
from .file_tools import (
    append_file,
    delete_file,
    file_exists,
    file_size,
    list_directory,
    make_directory,
    read_file,
    write_file,
)
from .git_tools import (
    git_commit,
    git_create_branch,
    git_diff,
    git_log,
    git_restore,
    git_status,
)
from .metadata import tool
from .models import Tool
from .registry import ToolRegistry
from .repo_tools import (
    explain_file,
    find_references,
    find_symbol,
    index_repository,
    search_text,
    summarize_project,
)
from .shell_tools import (
    current_user,
    execute_shell,
    is_command_available,
    ls,
    operating_system,
    pwd,
    run_python,
    which,
)
from .symbol_editor import (
    find_class,
    find_function,
    find_method,
    insert_after_symbol,
    insert_before_symbol,
    replace_class,
    replace_function,
)

__all__ = [
    # Models
    "Tool",
    # Registry
    "ToolRegistry",
    # Decorator
    "tool",
    # File tools
    "read_file",
    "write_file",
    "append_file",
    "delete_file",
    "file_exists",
    "file_size",
    "list_directory",
    "make_directory",
    # Edit tools
    "create_file",
    "replace_in_file",
    "edit_lines",
    "patch_file",
    # Repository intelligence tools
    "index_repository",
    "find_symbol",
    "find_references",
    "search_text",
    "summarize_project",
    "explain_file",
    # Shell tools
    "execute_shell",
    "run_python",
    "pwd",
    "ls",
    "which",
    "is_command_available",
    "operating_system",
    "current_user",
    # Git tools
    "git_diff",
    "git_status",
    "git_log",
    "git_create_branch",
    "git_commit",
    "git_restore",
    # Symbol-aware editing tools
    "find_function",
    "find_class",
    "find_method",
    "replace_function",
    "replace_class",
    "insert_after_symbol",
    "insert_before_symbol",
]
