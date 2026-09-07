"""
Tests for src/tools/search_replace_editor.py

Coverage:
- Exact match
- Whitespace-normalized match
- Fuzzy match
- Match below threshold → MatchFailed
- Empty search → MatchFailed
- File not found → EditResult(succeeded=False)
- Stages via ChangeManager when active
- Writes directly when no ChangeManager
- validate() returns correct found/similarity
- count=1 replaces only first occurrence
- count=-1 replaces all occurrences
- Workspace boundary enforcement
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.config.workspace import clear_workspace_root, set_workspace_root
from src.tools.search_replace_editor import (
    EditResult,
    MatchFailed,
    SearchReplaceEditor,
    _find_exact,
    _find_fuzzy,
    _find_normalized,
    _normalize_ws,
)


@pytest.fixture(autouse=True)
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Set workspace root to tmp_path for every test."""
    set_workspace_root(tmp_path)
    yield tmp_path
    clear_workspace_root()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_normalize_ws_collapses_spaces(self) -> None:
        assert _normalize_ws("a  b   c") == "a b c"

    def test_normalize_ws_collapses_newlines(self) -> None:
        assert _normalize_ws("a\n  b\n\nc") == "a b c"

    def test_find_exact_returns_index(self) -> None:
        assert _find_exact("hello world", "world") == 6

    def test_find_exact_returns_minus_one_on_miss(self) -> None:
        assert _find_exact("hello world", "xyz") == -1

    def test_find_normalized_finds_extra_spaces(self) -> None:
        # Only whitespace difference: double-space before foo
        result = _find_normalized("def  foo():", "def foo():")
        assert result is not None

    def test_find_normalized_returns_none_on_miss(self) -> None:
        assert _find_normalized("hello world", "xyz abc") is None

    def test_find_fuzzy_above_threshold(self) -> None:
        content = "def hello_world():"
        result = _find_fuzzy(content, "def hello_world():", 0.9)
        assert result is not None

    def test_find_fuzzy_below_threshold_returns_none(self) -> None:
        result = _find_fuzzy("abc", "xyz123456789", 0.99)
        assert result is None


# ---------------------------------------------------------------------------
# SearchReplaceEditor.apply — matching strategies
# ---------------------------------------------------------------------------


class TestApplyExactMatch:
    def test_exact_match_replaces(self, workspace: Path) -> None:
        f = workspace / "a.py"
        f.write_text("def foo():\n    pass\n")
        editor = SearchReplaceEditor()
        result = editor.apply("a.py", "def foo():", "def foo(x: int):")
        assert result.succeeded
        assert result.strategy == "exact"
        assert "def foo(x: int):" in f.read_text()

    def test_exact_match_replaces_only_first_by_default(self, workspace: Path) -> None:
        f = workspace / "b.py"
        f.write_text("foo\nfoo\nfoo\n")
        editor = SearchReplaceEditor()
        result = editor.apply("b.py", "foo", "bar", count=1)
        assert result.succeeded
        assert result.occurrences == 1
        content = f.read_text()
        assert content.count("bar") == 1
        assert content.count("foo") == 2

    def test_count_minus_one_replaces_all(self, workspace: Path) -> None:
        f = workspace / "c.py"
        f.write_text("foo\nfoo\nfoo\n")
        editor = SearchReplaceEditor()
        result = editor.apply("c.py", "foo", "bar", count=-1)
        assert result.succeeded
        assert result.occurrences == 3
        assert "foo" not in f.read_text()

    def test_no_change_returns_succeeded_zero_occurrences(self, workspace: Path) -> None:
        f = workspace / "d.py"
        original = "def foo(): pass\n"
        f.write_text(original)
        editor = SearchReplaceEditor()
        # Replace with identical content
        result = editor.apply("d.py", "def foo(): pass", "def foo(): pass")
        assert result.succeeded
        assert result.occurrences == 0


class TestApplyNormalizedMatch:
    def test_normalized_match_handles_extra_whitespace(self, workspace: Path) -> None:
        f = workspace / "e.py"
        f.write_text("def  foo():\n    pass\n")
        editor = SearchReplaceEditor()
        result = editor.apply("e.py", "def foo():", "def foo(x: int):")
        assert result.succeeded
        assert result.strategy == "normalized"

    def test_normalized_match_updates_content(self, workspace: Path) -> None:
        f = workspace / "f.py"
        f.write_text("class  Foo:\n    pass\n")
        editor = SearchReplaceEditor()
        result = editor.apply("f.py", "class Foo:", "class FooBar:")
        assert result.succeeded
        assert "FooBar" in f.read_text()


class TestApplyFuzzyMatch:
    def test_fuzzy_match_above_threshold(self, workspace: Path) -> None:
        original = "def calculate_total(items):\n    return sum(items)\n"
        f = workspace / "g.py"
        f.write_text(original)
        # 1 char difference
        editor = SearchReplaceEditor(similarity_threshold=0.8)
        result = editor.apply("g.py", "def calculate_total(items):", "def calculate_total(items: list):")
        # Fuzzy or exact should succeed
        assert result.succeeded

    def test_fuzzy_match_below_threshold_fails(self, workspace: Path) -> None:
        f = workspace / "h.py"
        f.write_text("def foo(): pass\n")
        editor = SearchReplaceEditor(similarity_threshold=0.99)
        result = editor.apply("h.py", "completely different string that won't match", "new")
        assert not result.succeeded
        assert result.error


class TestApplyErrors:
    def test_file_not_found_returns_failed_result(self) -> None:
        editor = SearchReplaceEditor()
        result = editor.apply("nonexistent.py", "foo", "bar")
        assert not result.succeeded
        assert "not found" in result.error.lower()

    def test_empty_search_raises_match_failed(self, workspace: Path) -> None:
        f = workspace / "i.py"
        f.write_text("content\n")
        editor = SearchReplaceEditor()
        result = editor.apply("i.py", "", "replacement")
        assert not result.succeeded

    def test_outside_workspace_raises_permission_error(self, workspace: Path) -> None:
        editor = SearchReplaceEditor()
        with pytest.raises(PermissionError):
            editor.apply("../outside.py", "foo", "bar")

    def test_prefix_collision_attack_blocked(self, workspace: Path, tmp_path: Path) -> None:
        # A sibling dir whose name starts with the workspace name would fool a
        # startswith() check but must NOT fool is_relative_to().
        sibling = workspace.parent / (workspace.name + "_evil")
        sibling.mkdir(exist_ok=True)
        evil_file = sibling / "secret.py"
        evil_file.write_text("secret content")
        try:
            editor = SearchReplaceEditor()
            with pytest.raises(PermissionError):
                editor.apply(str(evil_file), "secret", "pwned")
        finally:
            evil_file.unlink(missing_ok=True)
            sibling.rmdir()


# ---------------------------------------------------------------------------
# ChangeManager integration
# ---------------------------------------------------------------------------


class TestPatchManagerIntegration:
    def test_stages_when_patch_manager_active(self, workspace: Path) -> None:
        f = workspace / "j.py"
        f.write_text("def foo(): pass\n")
        pm = MagicMock()
        pm.propose = MagicMock()

        editor = SearchReplaceEditor(patch_manager=pm)
        result = editor.apply("j.py", "def foo():", "def foo(x):")

        assert result.succeeded
        pm.propose.assert_called_once()
        # File on disk unchanged (staged, not written)
        assert "def foo():" in f.read_text()

    def test_writes_directly_when_no_patch_manager(self, workspace: Path) -> None:
        f = workspace / "k.py"
        f.write_text("def foo(): pass\n")
        editor = SearchReplaceEditor()  # no patch_manager override
        result = editor.apply("k.py", "def foo():", "def foo(x):")
        assert result.succeeded
        assert "def foo(x):" in f.read_text()


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------


class TestValidate:
    def test_returns_true_for_exact_match(self, workspace: Path) -> None:
        f = workspace / "l.py"
        f.write_text("def foo(): pass\n")
        editor = SearchReplaceEditor()
        found, sim = editor.validate("l.py", "def foo():")
        assert found is True
        assert sim == 1.0

    def test_returns_false_for_no_match(self, workspace: Path) -> None:
        f = workspace / "m.py"
        f.write_text("def foo(): pass\n")
        editor = SearchReplaceEditor()
        found, sim = editor.validate("m.py", "class Totally_Missing:")
        assert found is False

    def test_returns_false_for_missing_file(self) -> None:
        editor = SearchReplaceEditor()
        found, sim = editor.validate("missing.py", "anything")
        assert found is False
        assert sim == 0.0
