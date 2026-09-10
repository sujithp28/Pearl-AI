"""Tests for src/agent/tool_filter.py."""
from __future__ import annotations

from src.agent.tool_filter import relevant_tools


def _make_tools(names: list[str]) -> list[dict]:
    return [{"name": n, "description": f"Tool {n}"} for n in names]


ALL_TOOLS = _make_tools([
    "read_file", "create_file", "write_file", "append_file",
    "replace_in_file", "edit_lines", "patch_file",
    "delete_file", "file_exists", "file_size",
    "list_directory", "ls", "make_directory",
    "search_text", "find_symbol", "find_references",
    "find_function", "find_class", "find_method",
    "rename_symbol", "replace_function", "replace_class", "replace_method",
    "insert_after_symbol", "insert_before_symbol", "batch_write_files",
    "execute_shell", "run_python", "pwd", "which", "is_command_available",
    "operating_system", "current_user",
    "git_status", "git_diff", "git_log", "git_commit", "git_stage",
    "git_create_branch", "git_restore", "git_blame", "summarize_changes",
    "index_repository", "summarize_project", "explain_file",
    "web_search", "web_fetch", "web_context",
])


class TestRelevantTools:
    def test_always_includes_core_tools(self) -> None:
        result = relevant_tools("", ALL_TOOLS)
        names = {t["name"] for t in result}
        for core in ("read_file", "list_directory", "search_text", "execute_shell"):
            assert core in names

    def test_web_tools_excluded_for_local_query(self) -> None:
        result = relevant_tools("find ChangeManager in the codebase", ALL_TOOLS)
        names = {t["name"] for t in result}
        assert "web_search" not in names
        assert "web_fetch" not in names

    def test_web_tools_included_when_explicitly_requested(self) -> None:
        result = relevant_tools("search online for Python documentation", ALL_TOOLS)
        names = {t["name"] for t in result}
        assert "web_search" in names

    def test_git_tools_included_for_git_prompt(self) -> None:
        result = relevant_tools("show me the git diff for this file", ALL_TOOLS)
        names = {t["name"] for t in result}
        assert "git_diff" in names
        assert "git_status" in names

    def test_git_tools_excluded_for_unrelated_prompt(self) -> None:
        result = relevant_tools("explain the authenticate function", ALL_TOOLS)
        names = {t["name"] for t in result}
        assert "git_blame" not in names

    def test_edit_tools_included_for_modify_prompt(self) -> None:
        result = relevant_tools("modify the login function to add logging", ALL_TOOLS)
        names = {t["name"] for t in result}
        assert "replace_in_file" in names

    def test_result_subset_of_all_tools(self) -> None:
        all_names = {t["name"] for t in ALL_TOOLS}
        result = relevant_tools("read the config file", ALL_TOOLS)
        for t in result:
            assert t["name"] in all_names

    def test_fewer_tools_than_full_list_for_simple_query(self) -> None:
        result = relevant_tools("read the main.py file", ALL_TOOLS)
        assert len(result) < len(ALL_TOOLS)

    def test_never_returns_empty_list(self) -> None:
        empty_tools: list[dict] = []
        result = relevant_tools("do something", empty_tools)
        assert isinstance(result, list)

    def test_preserves_original_tool_dict(self) -> None:
        tools = [{"name": "read_file", "description": "Reads a file", "extra": 123}]
        result = relevant_tools("read my file", tools)
        assert result[0] == tools[0]

    def test_case_insensitive_matching(self) -> None:
        result_lower = relevant_tools("git diff", ALL_TOOLS)
        result_upper = relevant_tools("GIT DIFF", ALL_TOOLS)
        assert {t["name"] for t in result_lower} == {t["name"] for t in result_upper}
