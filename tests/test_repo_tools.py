import pytest

from src.tools.repo_tools import (
    build_startup_index,
    explain_file,
    find_references,
    find_symbol,
    index_repository,
    search_text,
    summarize_project,
)


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch):
    """
    Run every test with tmp_path as the workspace root, since these
    tools are workspace-contained like the other file/edit tools.
    """

    monkeypatch.chdir(tmp_path)


def _write_sample_project(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()

    content_a = "\n".join(
        [
            "def foo():",
            "    return 1",
            "",
            "",
            "class Bar:",
            "    def method(self):",
            "        return foo()",
        ]
    ) + "\n"
    (pkg / "a.py").write_text(content_a)

    content_b = "\n".join(
        [
            "from pkg.a import foo",
            "",
            "",
            "def baz():",
            "    return foo() + 1",
        ]
    ) + "\n"
    (pkg / "b.py").write_text(content_b)

    (tmp_path / "notes.txt").write_text("TODO: refactor foo() later\n")

    content_readme = "\n".join(
        [
            "# Project",
            "",
            "Uses foo extensively.",
        ]
    ) + "\n"
    (tmp_path / "README.md").write_text(content_readme)


# ---------------------------------------------------------------------
# index_repository
# ---------------------------------------------------------------------


def test_index_repository_counts_files_and_symbols(tmp_path):
    _write_sample_project(tmp_path)

    summary = index_repository()

    assert summary["files_indexed"] == 2
    assert summary["unique_symbols"] == 4
    assert summary["symbols_found"] == 4


def test_index_repository_missing_path_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        index_repository("does-not-exist")


def test_index_repository_rejects_file_path(tmp_path):
    file = tmp_path / "a.py"
    file.write_text("x = 1\n")

    with pytest.raises(NotADirectoryError):
        index_repository("a.py")


def test_index_repository_rejects_path_outside_workspace(tmp_path):
    with pytest.raises(PermissionError):
        index_repository("..")


# ---------------------------------------------------------------------
# find_symbol
# ---------------------------------------------------------------------


def test_find_symbol_locates_function(tmp_path):
    _write_sample_project(tmp_path)

    locations = find_symbol("foo")

    assert locations == [
        {"file": "pkg/a.py", "line": 1, "type": "function"}
    ]


def test_find_symbol_locates_class(tmp_path):
    _write_sample_project(tmp_path)

    locations = find_symbol("Bar")

    assert locations == [{"file": "pkg/a.py", "line": 5, "type": "class"}]


def test_find_symbol_locates_method(tmp_path):
    _write_sample_project(tmp_path)

    locations = find_symbol("method")

    assert locations == [
        {"file": "pkg/a.py", "line": 6, "type": "function"}
    ]


def test_find_symbol_returns_empty_for_unknown_name(tmp_path):
    _write_sample_project(tmp_path)

    assert find_symbol("does_not_exist") == []


def test_find_symbol_rejects_empty_name(tmp_path):
    _write_sample_project(tmp_path)

    with pytest.raises(ValueError):
        find_symbol("")


# ---------------------------------------------------------------------
# find_references
# ---------------------------------------------------------------------


def test_find_references_returns_source_usages(tmp_path):
    _write_sample_project(tmp_path)

    references = find_references("foo")

    files_and_lines = {(r["file"], r["line"]) for r in references}

    assert files_and_lines == {
        ("pkg/a.py", 1),
        ("pkg/a.py", 7),
        ("pkg/b.py", 1),
        ("pkg/b.py", 5),
    }


def test_find_references_matches_whole_word_only(tmp_path):
    (tmp_path / "sample.py").write_text("foobar = 1\nfoo = 2\n")

    references = find_references("foo")

    assert len(references) == 1
    assert references[0]["line"] == 2


def test_find_references_rejects_empty_symbol(tmp_path):
    _write_sample_project(tmp_path)

    with pytest.raises(ValueError):
        find_references("")


# ---------------------------------------------------------------------
# search_text
# ---------------------------------------------------------------------


def test_search_text_matches_across_all_files(tmp_path):
    _write_sample_project(tmp_path)

    matches = search_text("foo")

    files = {match["file"] for match in matches}

    assert files == {"pkg/a.py", "pkg/b.py", "notes.txt", "README.md"}
    assert len(matches) == 6


def test_search_text_returns_empty_when_not_found(tmp_path):
    _write_sample_project(tmp_path)

    assert search_text("nonexistent_query_xyz") == []


def test_search_text_rejects_empty_query(tmp_path):
    _write_sample_project(tmp_path)

    with pytest.raises(ValueError):
        search_text("")


# ---------------------------------------------------------------------
# summarize_project
# ---------------------------------------------------------------------


def test_summarize_project_reports_counts(tmp_path):
    _write_sample_project(tmp_path)

    summary = summarize_project()

    assert summary["total_files"] == 4
    assert summary["source_files"] == 2
    assert summary["files_by_extension"] == {
        ".py": 2,
        ".txt": 1,
        ".md": 1,
    }
    assert summary["unique_symbols"] == 4
    assert summary["functions"] == 3
    assert summary["classes"] == 1
    assert set(summary["top_level_entries"]) == {
        "pkg",
        "notes.txt",
        "README.md",
    }


def test_summarize_project_rejects_path_outside_workspace(tmp_path):
    with pytest.raises(PermissionError):
        summarize_project("..")


# ---------------------------------------------------------------------
# explain_file
# ---------------------------------------------------------------------


def test_explain_file_reports_imports_classes_and_functions(tmp_path):
    _write_sample_project(tmp_path)

    explanation = explain_file("pkg/a.py")

    assert explanation["file"] == "pkg/a.py"
    assert explanation["total_lines"] == 7
    assert explanation["imports"] == []
    assert explanation["classes"] == [{"name": "Bar", "line": 5}]
    assert explanation["functions"] == [
        {"name": "foo", "line": 1},
        {"name": "method", "line": 6},
    ]


def test_explain_file_reports_imports(tmp_path):
    _write_sample_project(tmp_path)

    explanation = explain_file("pkg/b.py")

    assert explanation["imports"] == ["pkg.a.foo"]
    assert explanation["functions"] == [{"name": "baz", "line": 4}]
    assert explanation["classes"] == []


def test_explain_file_handles_non_python_file(tmp_path):
    _write_sample_project(tmp_path)

    explanation = explain_file("notes.txt")

    assert explanation["imports"] == []
    assert explanation["classes"] == []
    assert explanation["functions"] == []
    assert explanation["total_lines"] == 1


def test_explain_file_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        explain_file("does-not-exist.py")


def test_explain_file_rejects_directory(tmp_path):
    _write_sample_project(tmp_path)

    with pytest.raises(IsADirectoryError):
        explain_file("pkg")


def test_explain_file_rejects_path_outside_workspace(tmp_path):
    with pytest.raises(PermissionError):
        explain_file("../outside.py")


def test_explain_file_rejects_unparsable_python(tmp_path):
    (tmp_path / "broken.py").write_text("def broken(:\n")

    with pytest.raises(ValueError):
        explain_file("broken.py")


# ---------------------------------------------------------------------
# Repository index caching / startup priming
# ---------------------------------------------------------------------


def test_find_symbol_reuses_cached_index_within_a_session(tmp_path):
    _write_sample_project(tmp_path)

    first = find_symbol("foo")
    assert first == [{"file": "pkg/a.py", "line": 1, "type": "function"}]

    # Appending a new definition doesn't change the result: the index
    # for this root was already built and cached by the first call.
    (tmp_path / "pkg" / "a.py").write_text(
        (tmp_path / "pkg" / "a.py").read_text()
        + "\n\ndef newly_added():\n    return 2\n"
    )

    second = find_symbol("newly_added")
    assert second == []


def test_build_startup_index_primes_the_cache(tmp_path):
    _write_sample_project(tmp_path)

    build_startup_index()

    # Delete the file on disk after priming: find_symbol still
    # resolves it from the cache built during startup, proving the
    # index was built eagerly rather than lazily on first lookup.
    (tmp_path / "pkg" / "a.py").unlink()

    locations = find_symbol("foo")
    assert locations == [{"file": "pkg/a.py", "line": 1, "type": "function"}]


def test_build_startup_index_does_not_raise_for_missing_path(tmp_path):
    build_startup_index("does-not-exist")
