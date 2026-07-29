"""
Tests for Phase 1 — Repository Scanner.

Coverage areas
--------------
* Language detection (models.detect_language, EXTENSION_TO_LANGUAGE)
* GitignoreRules pattern compilation (_compile_gitignore_pattern)
* GitignoreRules.is_ignored semantics
* RepositoryScanner — basic scanning
* RepositoryScanner — always-ignored directory pruning
* RepositoryScanner — .gitignore integration (simple, anchored, negation,
  nested, double-star, dir-only)
* RepositoryScanner — file metadata (size, mtime, hash, language, extension)
* RepositoryScanner — ScanResult helpers (by_language, by_extension,
  source_files)
* RepositoryScanner — edge cases (no extension, empty gitignore, large file
  skipping, bad permissions, not-a-directory root)
* ScanResult.__len__
"""

from __future__ import annotations

import hashlib
import os
import stat
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from src.repository.models import (
    EXTENSION_TO_LANGUAGE,
    FileInfo,
    Language,
    ScanResult,
    detect_language,
)
from src.repository.scanner import (
    GitignoreRules,
    RepositoryScanner,
    _collect_file_info,
    _compile_gitignore_pattern,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_tree(root: Path, spec: dict) -> None:
    """Create a directory tree from a nested dict.

    Keys ending in ``/`` create directories (value must be a sub-dict).
    All other keys create files with the value as UTF-8 text content.

    Example::

        make_tree(tmp_path, {
            "src/": {"foo.py": "x = 1"},
            ".gitignore": "*.pyc\\n",
        })
    """
    for name, content in spec.items():
        path = root / name
        if isinstance(content, dict):
            path.mkdir(parents=True, exist_ok=True)
            make_tree(path, content)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            text = content if isinstance(content, str) else ""
            path.write_text(text, encoding="utf-8")


def rel_paths(result: ScanResult) -> set[str]:
    """Return the set of relative paths from a ScanResult."""
    return {fi.relative_path for fi in result.files}


# ---------------------------------------------------------------------------
# Language detection (models module)
# ---------------------------------------------------------------------------


class TestDetectLanguage:
    def test_python(self, tmp_path: Path) -> None:
        assert detect_language(tmp_path / "foo.py") is Language.PYTHON

    def test_pyi_stub(self, tmp_path: Path) -> None:
        assert detect_language(tmp_path / "foo.pyi") is Language.PYTHON

    def test_pyx_cython(self, tmp_path: Path) -> None:
        assert detect_language(tmp_path / "ext.pyx") is Language.PYTHON

    def test_javascript(self, tmp_path: Path) -> None:
        assert detect_language(tmp_path / "index.js") is Language.JAVASCRIPT

    def test_jsx(self, tmp_path: Path) -> None:
        assert detect_language(tmp_path / "App.jsx") is Language.JAVASCRIPT

    def test_typescript(self, tmp_path: Path) -> None:
        assert detect_language(tmp_path / "service.ts") is Language.TYPESCRIPT

    def test_tsx(self, tmp_path: Path) -> None:
        assert detect_language(tmp_path / "App.tsx") is Language.TYPESCRIPT

    def test_java(self, tmp_path: Path) -> None:
        assert detect_language(tmp_path / "Main.java") is Language.JAVA

    def test_go(self, tmp_path: Path) -> None:
        assert detect_language(tmp_path / "main.go") is Language.GO

    def test_rust(self, tmp_path: Path) -> None:
        assert detect_language(tmp_path / "lib.rs") is Language.RUST

    def test_markdown(self, tmp_path: Path) -> None:
        assert detect_language(tmp_path / "README.md") is Language.MARKDOWN

    def test_yaml(self, tmp_path: Path) -> None:
        assert detect_language(tmp_path / "config.yaml") is Language.YAML
        assert detect_language(tmp_path / "config.yml") is Language.YAML

    def test_toml(self, tmp_path: Path) -> None:
        assert detect_language(tmp_path / "pyproject.toml") is Language.TOML

    def test_json(self, tmp_path: Path) -> None:
        assert detect_language(tmp_path / "package.json") is Language.JSON

    def test_shell(self, tmp_path: Path) -> None:
        assert detect_language(tmp_path / "build.sh") is Language.SHELL

    def test_unknown_extension(self, tmp_path: Path) -> None:
        assert detect_language(tmp_path / "data.xyzzy") is Language.UNKNOWN

    def test_no_extension(self, tmp_path: Path) -> None:
        assert detect_language(tmp_path / "Makefile") is Language.UNKNOWN

    def test_case_insensitive(self, tmp_path: Path) -> None:
        # Python files named with uppercase extension must still be detected.
        assert detect_language(tmp_path / "FOO.PY") is Language.PYTHON

    def test_extension_to_language_is_lowercase(self) -> None:
        # All keys in the mapping must be lower-cased so detect_language's
        # .lower() normalisation always finds a match.
        for ext in EXTENSION_TO_LANGUAGE:
            assert ext == ext.lower(), f"Key {ext!r} is not lower-case"


# ---------------------------------------------------------------------------
# Gitignore pattern compilation
# ---------------------------------------------------------------------------


class TestCompileGitignorePattern:
    def test_blank_line_returns_none(self) -> None:
        assert _compile_gitignore_pattern("") is None
        assert _compile_gitignore_pattern("   ") is None
        assert _compile_gitignore_pattern("\t") is None

    def test_comment_returns_none(self) -> None:
        assert _compile_gitignore_pattern("# comment") is None
        assert _compile_gitignore_pattern("  # indented comment") is None

    def test_simple_extension_pattern_matches_any_depth(self) -> None:
        compiled = _compile_gitignore_pattern("*.py")
        assert compiled is not None
        regex, negation, dir_only = compiled
        assert not negation
        assert not dir_only
        assert regex.search("foo.py")
        assert regex.search("src/foo.py")
        assert regex.search("a/b/c/foo.py")

    def test_simple_extension_pattern_does_not_match_partial(self) -> None:
        regex, _, _ = _compile_gitignore_pattern("*.py")  # type: ignore[misc]
        # Must not match 'foo.pyc' or 'foo.py.bak'
        assert not regex.search("foo.pyc")
        assert not regex.search("foo.py.bak")

    def test_dir_only_pattern_sets_flag(self) -> None:
        compiled = _compile_gitignore_pattern("dist/")
        assert compiled is not None
        _, negation, dir_only = compiled
        assert dir_only
        assert not negation

    def test_negation_pattern_sets_flag(self) -> None:
        compiled = _compile_gitignore_pattern("!important.log")
        assert compiled is not None
        _, negation, dir_only = compiled
        assert negation
        assert not dir_only

    def test_anchored_slash_prefix_matches_root_only(self) -> None:
        regex, _, _ = _compile_gitignore_pattern("/secret.txt")  # type: ignore[misc]
        assert regex.search("secret.txt")
        assert not regex.search("src/secret.txt")
        assert not regex.search("a/b/secret.txt")

    def test_pattern_with_slash_is_anchored(self) -> None:
        regex, _, _ = _compile_gitignore_pattern("src/gen")  # type: ignore[misc]
        assert regex.search("src/gen")
        assert not regex.search("a/src/gen")

    def test_double_star_prefix_matches_any_depth(self) -> None:
        regex, _, _ = _compile_gitignore_pattern("**/logs")  # type: ignore[misc]
        assert regex.search("logs")
        assert regex.search("a/logs")
        assert regex.search("a/b/c/logs")

    def test_double_star_inline(self) -> None:
        regex, _, _ = _compile_gitignore_pattern("src/**/test_*.py")  # type: ignore[misc]
        assert regex.search("src/test_foo.py")
        assert regex.search("src/a/b/test_bar.py")

    def test_question_mark_matches_one_char(self) -> None:
        regex, _, _ = _compile_gitignore_pattern("foo?.txt")  # type: ignore[misc]
        assert regex.search("fooa.txt")
        assert regex.search("foob.txt")
        assert not regex.search("foo.txt")      # 0 chars
        assert not regex.search("fooab.txt")    # 2 chars

    def test_malformed_pattern_returns_none(self) -> None:
        # A pattern that produces an invalid regex should be skipped
        # gracefully without raising.
        result = _compile_gitignore_pattern("!")  # negation of nothing
        assert result is None


# ---------------------------------------------------------------------------
# GitignoreRules semantics
# ---------------------------------------------------------------------------


class TestGitignoreRules:
    def test_empty_rules_have_zero_len(self, tmp_path: Path) -> None:
        rules = GitignoreRules(tmp_path, [])
        assert len(rules) == 0

    def test_pattern_count(self, tmp_path: Path) -> None:
        rules = GitignoreRules(tmp_path, ["*.py", "# comment", "dist/"])
        assert len(rules) == 2  # comment is not a pattern

    def test_is_ignored_returns_none_outside_base(self, tmp_path: Path) -> None:
        other = tmp_path.parent / "other_project"
        rules = GitignoreRules(tmp_path, ["*.py"])
        assert rules.is_ignored(other / "foo.py", is_dir=False) is None

    def test_is_ignored_true_for_match(self, tmp_path: Path) -> None:
        rules = GitignoreRules(tmp_path, ["*.pyc"])
        assert rules.is_ignored(tmp_path / "foo.pyc", is_dir=False) is True

    def test_is_ignored_none_for_no_match(self, tmp_path: Path) -> None:
        rules = GitignoreRules(tmp_path, ["*.pyc"])
        assert rules.is_ignored(tmp_path / "foo.py", is_dir=False) is None

    def test_dir_only_ignores_directory_not_file(self, tmp_path: Path) -> None:
        rules = GitignoreRules(tmp_path, ["dist/"])
        # File named 'dist' → not ignored (dir-only pattern)
        assert rules.is_ignored(tmp_path / "dist", is_dir=False) is None
        # Directory named 'dist' → ignored
        assert rules.is_ignored(tmp_path / "dist", is_dir=True) is True

    def test_negation_overrides_earlier_match(self, tmp_path: Path) -> None:
        rules = GitignoreRules(tmp_path, ["*.log", "!important.log"])
        assert rules.is_ignored(tmp_path / "debug.log", is_dir=False) is True
        assert rules.is_ignored(tmp_path / "important.log", is_dir=False) is False

    def test_last_rule_wins(self, tmp_path: Path) -> None:
        # First match: ignored; second match: un-ignored; third: ignored again
        rules = GitignoreRules(
            tmp_path, ["*.log", "!important.log", "important.log"]
        )
        # The last matching rule for important.log is the third one (ignored)
        assert rules.is_ignored(tmp_path / "important.log", is_dir=False) is True

    def test_anchored_pattern_in_subdir_scope(self, tmp_path: Path) -> None:
        # GitignoreRules is rooted at tmp_path/src.
        src = tmp_path / "src"
        rules = GitignoreRules(src, ["/secret.txt"])
        # Matches only 'src/secret.txt', not 'src/deep/secret.txt'
        assert rules.is_ignored(src / "secret.txt", is_dir=False) is True
        assert rules.is_ignored(src / "deep" / "secret.txt", is_dir=False) is None


# ---------------------------------------------------------------------------
# _collect_file_info (unit-level)
# ---------------------------------------------------------------------------


class TestCollectFileInfo:
    def test_returns_file_info(self, tmp_path: Path) -> None:
        f = tmp_path / "hello.py"
        f.write_text("print('hi')", encoding="utf-8")
        info = _collect_file_info(f, tmp_path)
        assert info is not None
        assert info.relative_path == "hello.py"
        assert info.language is Language.PYTHON

    def test_hash_is_correct(self, tmp_path: Path) -> None:
        content = b"deterministic content"
        f = tmp_path / "f.bin"
        f.write_bytes(content)
        expected = hashlib.md5(content, usedforsecurity=False).hexdigest()
        info = _collect_file_info(f, tmp_path)
        assert info is not None
        assert info.content_hash == expected

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        ghost = tmp_path / "ghost.py"
        assert _collect_file_info(ghost, tmp_path) is None

    def test_empty_file_has_empty_hash(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.py"
        f.write_bytes(b"")
        info = _collect_file_info(f, tmp_path)
        assert info is not None
        assert info.content_hash == ""  # empty file → skip hashing

    def test_relative_path_uses_forward_slashes(self, tmp_path: Path) -> None:
        (tmp_path / "sub").mkdir()
        f = tmp_path / "sub" / "file.ts"
        f.write_text("x", encoding="utf-8")
        info = _collect_file_info(f, tmp_path)
        assert info is not None
        assert info.relative_path == "sub/file.ts"
        assert "\\" not in info.relative_path


# ---------------------------------------------------------------------------
# RepositoryScanner — basic scanning
# ---------------------------------------------------------------------------


class TestRepositoryScannerBasic:
    def test_empty_directory_returns_empty_result(self, tmp_path: Path) -> None:
        result = RepositoryScanner(tmp_path).scan()
        assert result.files == []
        assert result.root == tmp_path.resolve()

    def test_single_file_found(self, tmp_path: Path) -> None:
        (tmp_path / "hello.py").write_text("x", encoding="utf-8")
        result = RepositoryScanner(tmp_path).scan()
        assert len(result.files) == 1
        assert result.files[0].relative_path == "hello.py"

    def test_multiple_files_at_root(self, tmp_path: Path) -> None:
        for name in ("a.py", "b.ts", "c.md"):
            (tmp_path / name).write_text("x", encoding="utf-8")
        result = RepositoryScanner(tmp_path).scan()
        assert rel_paths(result) == {"a.py", "b.ts", "c.md"}

    def test_subdirectory_files_found(self, tmp_path: Path) -> None:
        make_tree(
            tmp_path,
            {"src/": {"foo.py": "x", "bar.py": "y"}, "README.md": ""},
        )
        assert rel_paths(RepositoryScanner(tmp_path).scan()) == {
            "src/foo.py",
            "src/bar.py",
            "README.md",
        }

    def test_sort_order_is_deterministic(self, tmp_path: Path) -> None:
        for i in range(10):
            (tmp_path / f"file{i:02d}.py").write_text(str(i), encoding="utf-8")
        result = RepositoryScanner(tmp_path).scan()
        paths = [fi.relative_path for fi in result.files]
        assert paths == sorted(paths)

    def test_result_root_is_resolved(self, tmp_path: Path) -> None:
        result = RepositoryScanner(tmp_path).scan()
        assert result.root == tmp_path.resolve()

    def test_scan_duration_is_positive(self, tmp_path: Path) -> None:
        (tmp_path / "f.py").write_text("x", encoding="utf-8")
        result = RepositoryScanner(tmp_path).scan()
        assert result.scan_duration_ms > 0

    def test_scan_result_len(self, tmp_path: Path) -> None:
        for i in range(5):
            (tmp_path / f"f{i}.py").write_text("x", encoding="utf-8")
        result = RepositoryScanner(tmp_path).scan()
        assert len(result) == 5

    def test_not_a_directory_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("x", encoding="utf-8")
        with pytest.raises(NotADirectoryError):
            RepositoryScanner(f)


# ---------------------------------------------------------------------------
# RepositoryScanner — always-ignored directories
# ---------------------------------------------------------------------------


class TestAlwaysIgnoredDirs:
    def _make_dir_with_file(
        self, root: Path, dir_name: str, filename: str = "f.py"
    ) -> None:
        d = root / dir_name
        d.mkdir()
        (d / filename).write_text("x", encoding="utf-8")

    def _assert_dir_skipped(
        self, root: Path, dir_name: str, sentinel: str = "keep.py"
    ) -> None:
        (root / sentinel).write_text("x", encoding="utf-8")
        result = RepositoryScanner(root).scan()
        paths = rel_paths(result)
        assert sentinel in paths
        assert not any(dir_name in p for p in paths)

    def test_git_skipped(self, tmp_path: Path) -> None:
        self._make_dir_with_file(tmp_path, ".git", "HEAD")
        self._assert_dir_skipped(tmp_path, ".git")

    def test_pycache_skipped(self, tmp_path: Path) -> None:
        self._make_dir_with_file(tmp_path, "__pycache__", "mod.cpython-312.pyc")
        self._assert_dir_skipped(tmp_path, "__pycache__")

    def test_node_modules_skipped(self, tmp_path: Path) -> None:
        (tmp_path / "node_modules" / "react").mkdir(parents=True)
        (tmp_path / "node_modules" / "react" / "index.js").write_text(
            "x", encoding="utf-8"
        )
        self._assert_dir_skipped(tmp_path, "node_modules")

    def test_venv_skipped(self, tmp_path: Path) -> None:
        self._make_dir_with_file(tmp_path, "venv", "pyvenv.cfg")
        self._assert_dir_skipped(tmp_path, "venv")

    def test_dot_venv_skipped(self, tmp_path: Path) -> None:
        self._make_dir_with_file(tmp_path, ".venv", "pyvenv.cfg")
        self._assert_dir_skipped(tmp_path, ".venv")

    def test_dist_skipped(self, tmp_path: Path) -> None:
        self._make_dir_with_file(tmp_path, "dist", "bundle.js")
        self._assert_dir_skipped(tmp_path, "dist")

    def test_build_skipped(self, tmp_path: Path) -> None:
        self._make_dir_with_file(tmp_path, "build", "main.o")
        self._assert_dir_skipped(tmp_path, "build")

    def test_extra_ignore_dirs(self, tmp_path: Path) -> None:
        self._make_dir_with_file(tmp_path, "secrets", "key.pem")
        (tmp_path / "app.py").write_text("x", encoding="utf-8")
        result = RepositoryScanner(tmp_path, extra_ignore_dirs={"secrets"}).scan()
        paths = rel_paths(result)
        assert "app.py" in paths
        assert not any("secrets" in p for p in paths)

    def test_pytest_cache_skipped(self, tmp_path: Path) -> None:
        self._make_dir_with_file(tmp_path, ".pytest_cache", "v")
        self._assert_dir_skipped(tmp_path, ".pytest_cache")

    def test_mypy_cache_skipped(self, tmp_path: Path) -> None:
        self._make_dir_with_file(tmp_path, ".mypy_cache", "3.12")
        self._assert_dir_skipped(tmp_path, ".mypy_cache")


# ---------------------------------------------------------------------------
# RepositoryScanner — .gitignore integration
# ---------------------------------------------------------------------------


class TestGitignoreIntegration:
    def test_simple_extension_pattern(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {".gitignore": "*.pyc\n", "foo.py": "x", "foo.pyc": "x"})
        paths = rel_paths(RepositoryScanner(tmp_path).scan())
        assert "foo.py" in paths
        assert "foo.pyc" not in paths

    def test_directory_pattern_prunes_subtree(self, tmp_path: Path) -> None:
        make_tree(
            tmp_path,
            {
                ".gitignore": "generated/\n",
                "src/": {"app.py": "x"},
                "generated/": {"output.js": "x"},
            },
        )
        paths = rel_paths(RepositoryScanner(tmp_path).scan())
        assert "src/app.py" in paths
        assert "generated/output.js" not in paths

    def test_anchored_pattern_matches_root_only(self, tmp_path: Path) -> None:
        make_tree(
            tmp_path,
            {
                ".gitignore": "/secret.txt\n",
                "secret.txt": "x",
                "src/": {"secret.txt": "x"},  # NOT at root → must survive
            },
        )
        paths = rel_paths(RepositoryScanner(tmp_path).scan())
        assert "secret.txt" not in paths
        assert "src/secret.txt" in paths

    def test_negation_un_ignores_file(self, tmp_path: Path) -> None:
        make_tree(
            tmp_path,
            {
                ".gitignore": "*.log\n!important.log\n",
                "debug.log": "x",
                "important.log": "x",
                "app.py": "x",
            },
        )
        paths = rel_paths(RepositoryScanner(tmp_path).scan())
        assert "app.py" in paths
        assert "important.log" in paths
        assert "debug.log" not in paths

    def test_nested_gitignore_applies_in_subtree(self, tmp_path: Path) -> None:
        make_tree(
            tmp_path,
            {
                ".gitignore": "*.log\n",
                "src/": {
                    ".gitignore": "*.tmp\n",
                    "app.py": "x",
                    "debug.log": "x",   # ignored by root .gitignore
                    "cache.tmp": "x",   # ignored by src/.gitignore
                    "main.ts": "x",
                },
            },
        )
        paths = rel_paths(RepositoryScanner(tmp_path).scan())
        assert "src/app.py" in paths
        assert "src/main.ts" in paths
        assert "src/debug.log" not in paths
        assert "src/cache.tmp" not in paths

    def test_nested_gitignore_does_not_affect_parent(self, tmp_path: Path) -> None:
        make_tree(
            tmp_path,
            {
                "src/": {".gitignore": "*.tmp\n", "cache.tmp": "x"},
                "root.tmp": "x",  # src/.gitignore must NOT affect this
            },
        )
        paths = rel_paths(RepositoryScanner(tmp_path).scan())
        assert "root.tmp" in paths
        assert "src/cache.tmp" not in paths

    def test_double_star_directory_pattern(self, tmp_path: Path) -> None:
        make_tree(
            tmp_path,
            {
                ".gitignore": "**/logs/\n",
                "logs/": {"app.log": "x"},
                "src/": {"logs/": {"debug.log": "x"}, "app.py": "x"},
            },
        )
        paths = rel_paths(RepositoryScanner(tmp_path).scan())
        assert "src/app.py" in paths
        assert "logs/app.log" not in paths
        assert "src/logs/debug.log" not in paths

    def test_empty_gitignore_does_not_affect_scan(self, tmp_path: Path) -> None:
        (tmp_path / ".gitignore").write_text("", encoding="utf-8")
        (tmp_path / "foo.py").write_text("x", encoding="utf-8")
        result = RepositoryScanner(tmp_path).scan()
        # .gitignore itself + foo.py
        assert "foo.py" in rel_paths(result)

    def test_comment_lines_in_gitignore_are_no_ops(self, tmp_path: Path) -> None:
        make_tree(
            tmp_path,
            {
                ".gitignore": "# This is a comment\n*.pyc\n# Another\n",
                "foo.pyc": "x",
                "foo.py": "x",
            },
        )
        paths = rel_paths(RepositoryScanner(tmp_path).scan())
        assert "foo.py" in paths
        assert "foo.pyc" not in paths

    def test_gitignore_pattern_count_is_reported(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {".gitignore": "*.pyc\n*.log\ndist/\n"})
        result = RepositoryScanner(tmp_path).scan()
        assert result.gitignore_patterns == 3

    def test_gitignore_in_always_ignored_dir_is_irrelevant(
        self, tmp_path: Path
    ) -> None:
        # .gitignore inside node_modules must not be read.
        make_tree(
            tmp_path,
            {
                "node_modules/": {".gitignore": "!app.py\n", "app.py": "x"},
                "main.py": "x",
            },
        )
        result = RepositoryScanner(tmp_path).scan()
        paths = rel_paths(result)
        assert "main.py" in paths
        assert not any("node_modules" in p for p in paths)

    def test_non_utf8_gitignore_does_not_crash(self, tmp_path: Path) -> None:
        # A .gitignore with Latin-1 or binary content must not crash the scan.
        (tmp_path / ".gitignore").write_bytes("# Fichiers générés\n*.pyc\n".encode("latin-1"))
        (tmp_path / "main.py").write_text("x = 1", encoding="utf-8")
        result = RepositoryScanner(tmp_path).scan()
        assert "main.py" in rel_paths(result)

    def test_binary_gitignore_does_not_crash(self, tmp_path: Path) -> None:
        # A .gitignore with a UTF-16 BOM (binary) must not crash the scan.
        (tmp_path / ".gitignore").write_bytes(b"\xff\xfe# UTF-16\n*.pyc\n")
        (tmp_path / "app.py").write_text("x = 1", encoding="utf-8")
        result = RepositoryScanner(tmp_path).scan()
        assert "app.py" in rel_paths(result)


# ---------------------------------------------------------------------------
# RepositoryScanner — file metadata
# ---------------------------------------------------------------------------


class TestFileMetadata:
    def test_language_detected_for_known_extension(self, tmp_path: Path) -> None:
        make_tree(
            tmp_path,
            {"app.py": "x", "index.ts": "x", "README.md": "x", "data.xyz": "x"},
        )
        result = RepositoryScanner(tmp_path).scan()
        by_path = {fi.relative_path: fi for fi in result.files}
        assert by_path["app.py"].language is Language.PYTHON
        assert by_path["index.ts"].language is Language.TYPESCRIPT
        assert by_path["README.md"].language is Language.MARKDOWN
        assert by_path["data.xyz"].language is Language.UNKNOWN

    def test_extension_is_lowercased(self, tmp_path: Path) -> None:
        (tmp_path / "FOO.PY").write_text("x", encoding="utf-8")
        result = RepositoryScanner(tmp_path).scan()
        assert result.files[0].extension == ".py"

    def test_file_with_no_extension(self, tmp_path: Path) -> None:
        (tmp_path / "Makefile").write_text("x", encoding="utf-8")
        result = RepositoryScanner(tmp_path).scan()
        assert result.files[0].extension == ""
        assert result.files[0].language is Language.UNKNOWN

    def test_file_size_matches_disk(self, tmp_path: Path) -> None:
        content = "hello world"
        f = tmp_path / "f.txt"
        f.write_text(content, encoding="utf-8")
        result = RepositoryScanner(tmp_path).scan()
        assert result.files[0].size == f.stat().st_size

    def test_modified_at_is_recent(self, tmp_path: Path) -> None:
        before = time.time() - 2
        (tmp_path / "f.py").write_text("x", encoding="utf-8")
        result = RepositoryScanner(tmp_path).scan()
        assert result.files[0].modified_at > before

    def test_same_content_same_hash(self, tmp_path: Path) -> None:
        content = "identical content"
        (tmp_path / "a.py").write_text(content, encoding="utf-8")
        (tmp_path / "b.py").write_text(content, encoding="utf-8")
        result = RepositoryScanner(tmp_path).scan()
        hashes = {fi.content_hash for fi in result.files}
        assert len(hashes) == 1

    def test_different_content_different_hash(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("content A", encoding="utf-8")
        (tmp_path / "b.py").write_text("content B", encoding="utf-8")
        result = RepositoryScanner(tmp_path).scan()
        assert len({fi.content_hash for fi in result.files}) == 2

    def test_content_hash_matches_md5(self, tmp_path: Path) -> None:
        content = b"test content for hashing"
        (tmp_path / "f.bin").write_bytes(content)
        expected = hashlib.md5(content, usedforsecurity=False).hexdigest()
        result = RepositoryScanner(tmp_path).scan()
        assert result.files[0].content_hash == expected

    def test_relative_path_uses_forward_slashes(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "foo.py").write_text("x", encoding="utf-8")
        result = RepositoryScanner(tmp_path).scan()
        assert result.files[0].relative_path == "src/foo.py"
        # Must hold even on Windows (where os.sep is backslash)
        assert "\\" not in result.files[0].relative_path

    def test_path_is_absolute(self, tmp_path: Path) -> None:
        (tmp_path / "f.py").write_text("x", encoding="utf-8")
        result = RepositoryScanner(tmp_path).scan()
        assert result.files[0].path.is_absolute()

    def test_max_file_size_excludes_content_hash(self, tmp_path: Path) -> None:
        f = tmp_path / "large.bin"
        f.write_bytes(b"x" * 10)
        # Set max size to 5 bytes so the 10-byte file exceeds it
        result = RepositoryScanner(tmp_path, max_file_size_bytes=5).scan()
        # File is excluded entirely when it exceeds the limit
        assert not any(fi.relative_path == "large.bin" for fi in result.files)

    def test_unreadable_file_logged_not_raised(self, tmp_path: Path) -> None:
        f = tmp_path / "locked.py"
        f.write_text("x", encoding="utf-8")
        # Simulate an unreadable file by patching open inside _collect_file_info
        original_open = open

        def mock_open(path, *args, **kwargs):  # type: ignore[override]
            if str(path) == str(f):
                raise OSError("permission denied (simulated)")
            return original_open(path, *args, **kwargs)

        with patch("builtins.open", side_effect=mock_open):
            result = RepositoryScanner(tmp_path).scan()

        # File should still appear in results; content_hash is just empty
        locked = next(
            (fi for fi in result.files if fi.relative_path == "locked.py"), None
        )
        assert locked is not None
        assert locked.content_hash == ""


# ---------------------------------------------------------------------------
# ScanResult helpers
# ---------------------------------------------------------------------------


class TestScanResultHelpers:
    def test_by_language_groups_correctly(self, tmp_path: Path) -> None:
        make_tree(
            tmp_path,
            {"a.py": "x", "b.py": "x", "c.ts": "x", "d.xyz": "x"},
        )
        result = RepositoryScanner(tmp_path).scan()
        by_lang = result.by_language()
        assert len(by_lang[Language.PYTHON]) == 2
        assert len(by_lang[Language.TYPESCRIPT]) == 1
        assert len(by_lang[Language.UNKNOWN]) == 1

    def test_by_extension_groups_correctly(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"a.py": "x", "b.py": "x", "c.ts": "x"})
        result = RepositoryScanner(tmp_path).scan()
        by_ext = result.by_extension()
        assert len(by_ext[".py"]) == 2
        assert len(by_ext[".ts"]) == 1

    def test_source_files_no_args_excludes_unknown(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"a.py": "x", "b.ts": "x", "c.xyz": "x"})
        result = RepositoryScanner(tmp_path).scan()
        sources = result.source_files()
        paths = {fi.relative_path for fi in sources}
        assert "a.py" in paths
        assert "b.ts" in paths
        assert "c.xyz" not in paths

    def test_source_files_single_language_filter(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"a.py": "x", "b.ts": "x", "c.rs": "x"})
        result = RepositoryScanner(tmp_path).scan()
        py_only = result.source_files(Language.PYTHON)
        assert all(fi.language is Language.PYTHON for fi in py_only)
        assert len(py_only) == 1

    def test_source_files_multiple_language_filter(self, tmp_path: Path) -> None:
        make_tree(tmp_path, {"a.py": "x", "b.ts": "x", "c.rs": "x"})
        result = RepositoryScanner(tmp_path).scan()
        py_ts = result.source_files(Language.PYTHON, Language.TYPESCRIPT)
        langs = {fi.language for fi in py_ts}
        assert langs == {Language.PYTHON, Language.TYPESCRIPT}
        assert len(py_ts) == 2

    def test_by_language_empty_for_unused_language(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("x", encoding="utf-8")
        result = RepositoryScanner(tmp_path).scan()
        by_lang = result.by_language()
        assert Language.JAVA not in by_lang

    def test_scan_result_errors_list_accessible(self, tmp_path: Path) -> None:
        result = RepositoryScanner(tmp_path).scan()
        assert isinstance(result.errors, list)


# ---------------------------------------------------------------------------
# Benchmark (lightweight — runs in the regular test suite)
# ---------------------------------------------------------------------------


class TestScannerBenchmark:
    def test_medium_repository_scan_time(self, tmp_path: Path) -> None:
        """Scanning 500 files with mixed languages must complete in < 10 s."""
        languages = [".py", ".ts", ".rs", ".go", ".java", ".md", ".json", ".yaml"]
        for i in range(500):
            lang = languages[i % len(languages)]
            path = tmp_path / f"subdir_{i % 20}" / f"file_{i}{lang}"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"content_{i}", encoding="utf-8")

        start = time.monotonic()
        result = RepositoryScanner(tmp_path).scan()
        elapsed = time.monotonic() - start

        assert len(result.files) == 500
        assert elapsed < 10.0, f"Scan took {elapsed:.2f}s (limit: 10s)"
