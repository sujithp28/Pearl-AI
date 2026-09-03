"""
Task-aware tool filter for Pearl's planning engine.

Reduces the tool list sent to the LLM to only the tools that are likely
needed for the current request. Two benefits:

1. Token efficiency: 48 tools × ~70 tokens ≈ 3 400 tokens. Filtering to
   12 relevant tools uses ~850 tokens, freeing ~2 500 extra tokens for
   workspace context.

2. Tool-selection accuracy: small models (1.5 B parameters) are
   significantly more reliable when choosing from 8–15 tools rather than
   48. The primary observed failure — picking ``web_search`` instead of
   ``search_text`` for local codebase queries — is eliminated when
   ``web_search`` is not in scope for the task.

This is a keyword heuristic, not a security boundary. The workspace
path validator and PatchManager approval gate remain the real enforcement
points; this filter only shapes the planning prompt for quality.
"""
from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Always included — the minimum viable tool set for any autonomous task.
# ---------------------------------------------------------------------------

_CORE: frozenset[str] = frozenset({
    "read_file",
    "list_directory",
    "search_text",
    "find_symbol",
    "execute_shell",
})

# ---------------------------------------------------------------------------
# Keyword-gated groups: (keywords, tool_names)
#
# Each keyword tuple uses lowercase substrings.  A single match in the
# lowercased prompt unlocks the entire group.  Order doesn't matter.
# ---------------------------------------------------------------------------

_GROUPS: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    # File creation / writing
    (
        ("create", "new file", "touch", "make file", "generate file", "write file"),
        ("create_file", "write_file", "append_file", "make_directory"),
    ),
    (
        ("write", "save", "overwrite", "output"),
        ("write_file", "create_file", "append_file"),
    ),
    # Editing
    (
        ("edit", "modify", "change", "update", "fix", "replace", "patch"),
        ("replace_in_file", "edit_lines", "patch_file"),
    ),
    (
        ("append",),
        ("append_file",),
    ),
    # Deletion
    (
        ("delete", "remove", " rm "),
        ("delete_file",),
    ),
    # Directory / existence
    (
        ("ls", "list", "directory", "folder", "dir"),
        ("ls", "make_directory", "file_exists", "file_size"),
    ),
    (
        ("exist", "size", "check file"),
        ("file_exists", "file_size"),
    ),
    # Code search / navigation
    (
        ("function", "def ", "method", "class ", "symbol", "reference"),
        ("find_function", "find_class", "find_method", "find_references", "find_symbol"),
    ),
    (
        ("grep", "search code", "search for", "find in", "locate", "where is"),
        ("search_text", "find_symbol", "find_references"),
    ),
    # Repository understanding
    (
        ("explain", "understand", "purpose", "describe", "overview", "architecture",
         "what does", "how does", "summarize"),
        ("explain_file", "summarize_project", "index_repository"),
    ),
    # Refactoring
    (
        ("refactor", "rename", "extract", "move symbol"),
        (
            "rename_symbol", "replace_function", "replace_class", "replace_method",
            "insert_after_symbol", "insert_before_symbol",
            "find_function", "find_class", "find_method",
        ),
    ),
    (
        ("batch", "multiple files", "many files"),
        ("batch_write_files",),
    ),
    # Shell / execution
    (
        ("run", "execute", "test", "pytest", "pip", "install", "command", "terminal",
         "shell", "script"),
        ("execute_shell", "run_python"),
    ),
    (
        ("python script", "run python"),
        ("run_python",),
    ),
    (
        ("pwd", "current directory", "working dir"),
        ("pwd",),
    ),
    (
        ("which", "is installed", "available", "command exists"),
        ("which", "is_command_available", "operating_system", "current_user"),
    ),
    # Git
    (
        ("git", "commit", "diff", "stage", "branch", "checkout",
         "history", "log", "revert", "stash"),
        (
            "git_status", "git_diff", "git_log", "git_commit", "git_stage",
            "git_create_branch", "git_restore", "git_blame", "summarize_changes",
        ),
    ),
    (
        ("blame", "who wrote", "when was added"),
        ("git_blame",),
    ),
    # Web — only unlocked when the user explicitly asks for external info
    (
        ("web", "internet", "online", "documentation", "docs", "latest version",
         "current version", "external", "lookup", "google", "search online"),
        ("web_search", "web_fetch", "web_context"),
    ),
]

# Fallback when no keyword matches — richer than _CORE, covers the most
# common agentic tasks without triggering the full 48-tool list.
_FALLBACK: frozenset[str] = frozenset({
    "read_file", "create_file", "write_file", "replace_in_file",
    "list_directory", "ls", "search_text", "find_symbol",
    "execute_shell", "git_status", "git_diff",
})


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def relevant_tools(
    user_prompt: str,
    all_tools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Return the subset of ``all_tools`` relevant to ``user_prompt``.

    Always includes ``_CORE``.  Adds keyword-matched groups.  Falls back
    to ``_FALLBACK`` when no group matches.  Never returns an empty list
    (safety net: returns ``all_tools`` if every name is unrecognised).

    Parameters
    ----------
    user_prompt:
        The raw user request string.
    all_tools:
        Tool metadata dicts from ``ToolRegistry.get_tools()``.

    Returns
    -------
    list[dict]
        Subset of ``all_tools`` in their original order.
    """
    prompt_lower = _normalise(user_prompt)
    selected: set[str] = set(_CORE)

    for keywords, tool_names in _GROUPS:
        if any(kw in prompt_lower for kw in keywords):
            selected.update(tool_names)

    if len(selected) <= len(_CORE):
        # No keyword matched beyond the core — use the richer fallback set
        selected = set(_FALLBACK)

    result = [t for t in all_tools if t["name"] in selected]
    return result if result else all_tools
