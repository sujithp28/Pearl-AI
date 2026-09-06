"""
Tests for Phase 4 — RepositoryIndex.

Coverage areas
--------------
* SymbolEntry contract — fields, frozen, repr
* IndexStats contract — fields, frozen, repr
* RepositoryIndex.build() — empty, single file, multi-file, files with errors
* lookup() — exact name, multiple matches, miss, case sensitivity
* lookup_qualified() — exact match, miss, collision logging
* symbols_in_file() — known file, unknown file, source order
* symbols_by_kind() — CLASS, METHOD, FUNCTION, VARIABLE, CONSTANT, unknown kind
* search() — glob patterns: *, ?, prefix, suffix, multi-level, case
* imports_for() — known file, unknown file
* files_importing() — module name, imported name, no match, word-boundary
* indexed_files() — sorted order
* stats() — counts, duration, languages, error_file_count
* __len__() — empty, populated
* __repr__() — coverage
* Layer rule — index.py only imports from src.repository.{models,parsers}
* Integration — build from real PythonParser output
* Integration — full pipeline: scan → parse → index → query
* SymbolDef extended fields — signature, return_type, raises
* Python parser populates extended fields
* Performance — build 5 000 symbols < 500 ms
"""

from __future__ import annotations

import ast
import time
import tempfile
from dataclasses import dataclass
import sys
from pathlib import Path

import pytest

from src.repository.models import FileInfo, Language
from src.repository.parsers import ParseResult, SymbolDef, SymbolKind
from src.repository.index import IndexStats, RepositoryIndex, SymbolEntry


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _fi(name: str, lang: Language = Language.PYTHON) -> FileInfo:
    return FileInfo(
        path=Path(f"/repo/{name}"),
        relative_path=name,
        extension=Path(name).suffix.lower(),
        language=lang,
        size=100,
        modified_at=0.0,
        content_hash="",
    )


def _sym(
    name: str,
    qn: str | None = None,
    kind: SymbolKind = SymbolKind.FUNCTION,
    line_start: int = 1,
    line_end: int = 5,
    parent: str | None = None,
    decorators: list[str] | None = None,
    is_async: bool = False,
    docstring: str | None = None,
    signature: str | None = None,
    return_type: str | None = None,
    raises: list[str] | None = None,
) -> SymbolDef:
    return SymbolDef(
        name=name,
        qualified_name=qn or name,
        kind=kind,
        line_start=line_start,
        line_end=line_end,
        parent=parent,
        decorators=decorators or [],
        is_async=is_async,
        docstring=docstring,
        signature=signature,
        return_type=return_type,
        raises=raises or [],
    )


def _pr(
    name: str,
    symbols: list[SymbolDef] | None = None,
    imports: list[str] | None = None,
    errors: list[str] | None = None,
    lang: Language = Language.PYTHON,
) -> ParseResult:
    return ParseResult(
        file_info=_fi(name, lang),
        symbols=symbols or [],
        imports=imports or [],
        errors=errors or [],
    )


def _build(*args: ParseResult) -> RepositoryIndex:
    return RepositoryIndex.build(iter(args))


# ---------------------------------------------------------------------------
# SymbolEntry contract
# ---------------------------------------------------------------------------


class TestSymbolEntry:
    def test_fields_accessible(self) -> None:
        sym = _sym("foo")
        fi = _fi("a.py")
        e = SymbolEntry(symbol=sym, file_info=fi, relative_path="a.py",
                        language=Language.PYTHON)
        assert e.symbol is sym
        assert e.file_info is fi
        assert e.relative_path == "a.py"
        assert e.language is Language.PYTHON

    def test_is_frozen(self) -> None:
        e = SymbolEntry(symbol=_sym("x"), file_info=_fi("a.py"),
                        relative_path="a.py", language=Language.PYTHON)
        with pytest.raises((AttributeError, TypeError)):
            e.relative_path = "other.py"  # type: ignore[misc]

    def test_repr_contains_kind_and_name(self) -> None:
        e = SymbolEntry(symbol=_sym("foo", kind=SymbolKind.CLASS),
                        file_info=_fi("a.py"), relative_path="a.py",
                        language=Language.PYTHON)
        assert "class" in repr(e)
        assert "foo" in repr(e)
        assert "a.py" in repr(e)


# ---------------------------------------------------------------------------
# IndexStats contract
# ---------------------------------------------------------------------------


class TestIndexStats:
    def test_fields_accessible(self) -> None:
        s = IndexStats(file_count=3, symbol_count=10, import_count=5,
                       build_duration_ms=1.2, languages=frozenset({Language.PYTHON}))
        assert s.file_count == 3
        assert s.symbol_count == 10
        assert s.import_count == 5
        assert s.build_duration_ms == 1.2
        assert Language.PYTHON in s.languages
        assert s.error_file_count == 0  # default

    def test_is_frozen(self) -> None:
        s = IndexStats(file_count=1, symbol_count=0, import_count=0,
                       build_duration_ms=0.0, languages=frozenset())
        with pytest.raises((AttributeError, TypeError)):
            s.file_count = 99  # type: ignore[misc]

    def test_repr(self) -> None:
        s = IndexStats(file_count=2, symbol_count=5, import_count=3,
                       build_duration_ms=0.5, languages=frozenset({Language.PYTHON}))
        r = repr(s)
        assert "files=2" in r
        assert "symbols=5" in r
        assert "python" in r

    def test_error_file_count_default(self) -> None:
        s = IndexStats(file_count=1, symbol_count=0, import_count=0,
                       build_duration_ms=0.0, languages=frozenset())
        assert s.error_file_count == 0

    def test_error_file_count_explicit(self) -> None:
        s = IndexStats(file_count=5, symbol_count=0, import_count=0,
                       build_duration_ms=0.0, languages=frozenset(),
                       error_file_count=2)
        assert s.error_file_count == 2


# ---------------------------------------------------------------------------
# build()
# ---------------------------------------------------------------------------


class TestBuild:
    def test_empty_iterable(self) -> None:
        index = RepositoryIndex.build(iter([]))
        assert len(index) == 0
        assert index.stats().file_count == 0

    def test_single_file_no_symbols(self) -> None:
        index = _build(_pr("a.py"))
        assert len(index) == 0
        assert index.stats().file_count == 1

    def test_single_file_with_symbols(self) -> None:
        index = _build(_pr("a.py", symbols=[_sym("foo")]))
        assert len(index) == 1

    def test_multiple_files(self) -> None:
        index = _build(
            _pr("a.py", symbols=[_sym("A"), _sym("B")]),
            _pr("b.py", symbols=[_sym("C")]),
        )
        assert len(index) == 3
        assert index.stats().file_count == 2

    def test_file_with_errors_still_indexed(self) -> None:
        pr = _pr("bad.py", symbols=[_sym("X")], errors=["SyntaxError at line 1: ..."])
        index = _build(pr)
        assert len(index) == 1
        assert index.stats().error_file_count == 1

    def test_error_file_count_zero_when_no_errors(self) -> None:
        index = _build(_pr("a.py", symbols=[_sym("X")]))
        assert index.stats().error_file_count == 0

    def test_imports_indexed(self) -> None:
        index = _build(_pr("a.py", imports=["import os", "import sys"]))
        assert index.stats().import_count == 2

    def test_languages_collected(self) -> None:
        index = _build(
            _pr("a.py", lang=Language.PYTHON),
            _pr("b.ts", lang=Language.TYPESCRIPT),
        )
        assert Language.PYTHON in index.stats().languages
        assert Language.TYPESCRIPT in index.stats().languages

    def test_build_duration_non_negative(self) -> None:
        index = _build(_pr("a.py"))
        assert index.stats().build_duration_ms >= 0

    def test_build_accepts_list(self) -> None:
        results = [_pr("a.py"), _pr("b.py")]
        index = RepositoryIndex.build(results)
        assert index.stats().file_count == 2

    def test_build_accepts_generator(self) -> None:
        def gen():
            yield _pr("x.py")
            yield _pr("y.py")
        index = RepositoryIndex.build(gen())
        assert index.stats().file_count == 2

    def test_symbol_count_in_stats(self) -> None:
        index = _build(
            _pr("a.py", symbols=[_sym("X"), _sym("Y")]),
            _pr("b.py", symbols=[_sym("Z")]),
        )
        assert index.stats().symbol_count == 3


# ---------------------------------------------------------------------------
# lookup()
# ---------------------------------------------------------------------------


class TestLookup:
    def test_single_match(self) -> None:
        index = _build(_pr("a.py", symbols=[_sym("foo")]))
        result = index.lookup("foo")
        assert len(result) == 1
        assert result[0].symbol.name == "foo"

    def test_multiple_matches_across_files(self) -> None:
        index = _build(
            _pr("a.py", symbols=[_sym("foo")]),
            _pr("b.py", symbols=[_sym("foo")]),
        )
        result = index.lookup("foo")
        assert len(result) == 2

    def test_miss_returns_empty_list(self) -> None:
        index = _build(_pr("a.py", symbols=[_sym("foo")]))
        assert index.lookup("bar") == []

    def test_case_sensitive(self) -> None:
        index = _build(_pr("a.py", symbols=[_sym("NewFoo")]))
        assert index.lookup("foo") == []
        assert index.lookup("NewFoo") != []

    def test_returns_copy(self) -> None:
        index = _build(_pr("a.py", symbols=[_sym("foo")]))
        r1 = index.lookup("foo")
        r1.clear()
        assert index.lookup("foo") != []

    def test_lookup_by_unqualified_name(self) -> None:
        sym = _sym("method", qn="NewFoo.method", kind=SymbolKind.METHOD)
        index = _build(_pr("a.py", symbols=[sym]))
        result = index.lookup("method")
        assert len(result) == 1
        assert result[0].symbol.qualified_name == "NewFoo.method"


# ---------------------------------------------------------------------------
# lookup_qualified()
# ---------------------------------------------------------------------------


class TestLookupQualified:
    def test_exact_match(self) -> None:
        sym = _sym("method", qn="NewFoo.method")
        index = _build(_pr("a.py", symbols=[sym]))
        entry = index.lookup_qualified("NewFoo.method")
        assert entry is not None
        assert entry.symbol.name == "method"

    def test_miss_returns_none(self) -> None:
        index = _build(_pr("a.py", symbols=[_sym("foo")]))
        assert index.lookup_qualified("NoSuch.method") is None

    def test_unqualified_name_as_key(self) -> None:
        index = _build(_pr("a.py", symbols=[_sym("foo")]))
        assert index.lookup_qualified("foo") is not None

    def test_collision_last_wins(self) -> None:
        sym_a = _sym("foo", qn="foo")
        sym_b = _sym("foo", qn="foo")
        index = _build(
            _pr("a.py", symbols=[sym_a]),
            _pr("b.py", symbols=[sym_b]),
        )
        # last writer wins — result is one entry, in b.py
        entry = index.lookup_qualified("foo")
        assert entry is not None
        assert entry.relative_path == "b.py"

    def test_returns_symbol_entry_not_symboldef(self) -> None:
        index = _build(_pr("a.py", symbols=[_sym("foo")]))
        entry = index.lookup_qualified("foo")
        assert isinstance(entry, SymbolEntry)


# ---------------------------------------------------------------------------
# symbols_in_file()
# ---------------------------------------------------------------------------


class TestSymbolsInFile:
    def test_known_file(self) -> None:
        syms = [_sym("a", line_start=1), _sym("b", line_start=5)]
        index = _build(_pr("a.py", symbols=syms))
        result = index.symbols_in_file("a.py")
        assert len(result) == 2

    def test_unknown_file_returns_empty(self) -> None:
        index = _build(_pr("a.py", symbols=[_sym("x")]))
        assert index.symbols_in_file("no_such.py") == []

    def test_source_order_preserved(self) -> None:
        syms = [
            _sym("a", qn="a", line_start=1),
            _sym("b", qn="b", line_start=10),
            _sym("c", qn="c", line_start=20),
        ]
        index = _build(_pr("a.py", symbols=syms))
        result = index.symbols_in_file("a.py")
        names = [e.symbol.name for e in result]
        assert names == ["a", "b", "c"]

    def test_returns_copy(self) -> None:
        index = _build(_pr("a.py", symbols=[_sym("x")]))
        r1 = index.symbols_in_file("a.py")
        r1.clear()
        assert index.symbols_in_file("a.py") != []

    def test_file_with_no_symbols(self) -> None:
        index = _build(_pr("empty.py", symbols=[]))
        assert index.symbols_in_file("empty.py") == []


# ---------------------------------------------------------------------------
# symbols_by_kind()
# ---------------------------------------------------------------------------


class TestSymbolsByKind:
    def test_single_kind(self) -> None:
        index = _build(_pr("a.py", symbols=[
            _sym("NewFoo", kind=SymbolKind.CLASS),
            _sym("bar", kind=SymbolKind.FUNCTION),
        ]))
        classes = index.symbols_by_kind(SymbolKind.CLASS)
        assert len(classes) == 1
        assert classes[0].symbol.kind is SymbolKind.CLASS

    def test_unknown_kind_returns_empty(self) -> None:
        index = _build(_pr("a.py", symbols=[_sym("x")]))
        assert index.symbols_by_kind(SymbolKind.MODULE) == []

    def test_kind_across_files(self) -> None:
        index = _build(
            _pr("a.py", symbols=[_sym("A", kind=SymbolKind.CLASS)]),
            _pr("b.py", symbols=[_sym("B", kind=SymbolKind.CLASS)]),
        )
        assert len(index.symbols_by_kind(SymbolKind.CLASS)) == 2

    def test_method_vs_function_distinct(self) -> None:
        index = _build(_pr("a.py", symbols=[
            _sym("m", kind=SymbolKind.METHOD),
            _sym("f", kind=SymbolKind.FUNCTION),
        ]))
        assert len(index.symbols_by_kind(SymbolKind.METHOD)) == 1
        assert len(index.symbols_by_kind(SymbolKind.FUNCTION)) == 1

    def test_constant_and_variable_distinct(self) -> None:
        index = _build(_pr("a.py", symbols=[
            _sym("MAX", kind=SymbolKind.CONSTANT),
            _sym("count", kind=SymbolKind.VARIABLE),
        ]))
        assert len(index.symbols_by_kind(SymbolKind.CONSTANT)) == 1
        assert len(index.symbols_by_kind(SymbolKind.VARIABLE)) == 1

    def test_returns_copy(self) -> None:
        index = _build(_pr("a.py", symbols=[_sym("x", kind=SymbolKind.FUNCTION)]))
        r = index.symbols_by_kind(SymbolKind.FUNCTION)
        r.clear()
        assert index.symbols_by_kind(SymbolKind.FUNCTION) != []


# ---------------------------------------------------------------------------
# search()
# ---------------------------------------------------------------------------


class TestSearch:
    def test_suffix_glob(self) -> None:
        index = _build(_pr("a.py", symbols=[
            _sym("FooManager", qn="FooManager", kind=SymbolKind.CLASS),
            _sym("BarManager", qn="BarManager", kind=SymbolKind.CLASS),
            _sym("helper", qn="helper"),
        ]))
        result = index.search("*Manager")
        names = {e.symbol.name for e in result}
        assert names == {"FooManager", "BarManager"}

    def test_prefix_glob(self) -> None:
        index = _build(_pr("a.py", symbols=[
            _sym("test_foo", qn="test_foo"),
            _sym("test_bar", qn="test_bar"),
            _sym("other", qn="other"),
        ]))
        result = index.search("test_*")
        assert len(result) == 2

    def test_nested_glob(self) -> None:
        syms = [
            _sym("method", qn="NewFoo.method", kind=SymbolKind.METHOD),
            _sym("other", qn="NewFoo.other", kind=SymbolKind.METHOD),
            _sym("top", qn="top"),
        ]
        index = _build(_pr("a.py", symbols=syms))
        result = index.search("NewFoo.*")
        assert len(result) == 2

    def test_question_mark_wildcard(self) -> None:
        index = _build(_pr("a.py", symbols=[
            _sym("foo", qn="foo"),
            _sym("f", qn="f"),
        ]))
        result = index.search("fo?")
        assert len(result) == 1
        assert result[0].symbol.name == "foo"

    def test_no_match_returns_empty(self) -> None:
        index = _build(_pr("a.py", symbols=[_sym("foo")]))
        assert index.search("*zzz*") == []

    def test_exact_glob(self) -> None:
        index = _build(_pr("a.py", symbols=[_sym("foo"), _sym("foobar")]))
        result = index.search("foo")
        assert len(result) == 1

    def test_case_sensitive(self) -> None:
        index = _build(_pr("a.py", symbols=[
            _sym("NewFoo", qn="NewFoo"),
            _sym("foo", qn="foo"),
        ]))
        result = index.search("NewFoo")
        assert len(result) == 1
        assert result[0].symbol.name == "NewFoo"

    def test_double_star_cross_file(self) -> None:
        index = _build(
            _pr("a.py", symbols=[_sym("m", qn="A.m", kind=SymbolKind.METHOD)]),
            _pr("b.py", symbols=[_sym("m", qn="B.m", kind=SymbolKind.METHOD)]),
        )
        result = index.search("*.m")
        assert len(result) == 2


# ---------------------------------------------------------------------------
# imports_for()
# ---------------------------------------------------------------------------


class TestImportsFor:
    def test_known_file(self) -> None:
        index = _build(_pr("a.py", imports=["import os", "import sys"]))
        result = index.imports_for("a.py")
        assert "import os" in result
        assert "import sys" in result

    def test_unknown_file_returns_empty(self) -> None:
        index = _build(_pr("a.py", imports=["import os"]))
        assert index.imports_for("no_such.py") == []

    def test_returns_copy(self) -> None:
        index = _build(_pr("a.py", imports=["import os"]))
        r = index.imports_for("a.py")
        r.clear()
        assert index.imports_for("a.py") != []

    def test_empty_imports(self) -> None:
        index = _build(_pr("a.py", imports=[]))
        assert index.imports_for("a.py") == []


# ---------------------------------------------------------------------------
# files_importing()
# ---------------------------------------------------------------------------


class TestFilesImporting:
    def test_module_name_match(self) -> None:
        index = _build(
            _pr("a.py", imports=["import os"]),
            _pr("b.py", imports=["import sys"]),
        )
        result = index.files_importing("os")
        assert len(result) == 1
        assert result[0].relative_path == "a.py"

    def test_from_import_module(self) -> None:
        index = _build(
            _pr("a.py", imports=["from pathlib import Path"]),
        )
        result = index.files_importing("pathlib")
        assert len(result) == 1

    def test_from_import_name(self) -> None:
        index = _build(
            _pr("a.py", imports=["from pathlib import Path"]),
        )
        result = index.files_importing("Path")
        assert len(result) == 1

    def test_no_match(self) -> None:
        index = _build(_pr("a.py", imports=["import os"]))
        assert index.files_importing("asyncio") == []

    def test_multiple_files_match(self) -> None:
        index = _build(
            _pr("a.py", imports=["import asyncio"]),
            _pr("b.py", imports=["from asyncio import sleep"]),
            _pr("c.py", imports=["import os"]),
        )
        result = index.files_importing("asyncio")
        paths = {fi.relative_path for fi in result}
        assert paths == {"a.py", "b.py"}

    def test_word_boundary_no_partial_match(self) -> None:
        index = _build(_pr("a.py", imports=["import os_extras"]))
        result = index.files_importing("os")
        assert result == []

    def test_each_file_returned_once(self) -> None:
        index = _build(_pr("a.py", imports=["import os", "from os import path"]))
        result = index.files_importing("os")
        assert len(result) == 1

    def test_sorted_by_relative_path(self) -> None:
        index = _build(
            _pr("z.py", imports=["import os"]),
            _pr("a.py", imports=["import os"]),
        )
        result = index.files_importing("os")
        assert result[0].relative_path == "a.py"
        assert result[1].relative_path == "z.py"


# ---------------------------------------------------------------------------
# indexed_files()
# ---------------------------------------------------------------------------


class TestIndexedFiles:
    def test_returns_all_files(self) -> None:
        index = _build(_pr("a.py"), _pr("b.py"), _pr("c.py"))
        assert len(index.indexed_files()) == 3

    def test_sorted_by_relative_path(self) -> None:
        index = _build(_pr("z.py"), _pr("a.py"), _pr("m.py"))
        paths = [fi.relative_path for fi in index.indexed_files()]
        assert paths == ["a.py", "m.py", "z.py"]

    def test_empty_index(self) -> None:
        index = RepositoryIndex.build(iter([]))
        assert index.indexed_files() == []


# ---------------------------------------------------------------------------
# stats()
# ---------------------------------------------------------------------------


class TestStats:
    def test_stats_returns_index_stats(self) -> None:
        index = _build(_pr("a.py"))
        assert isinstance(index.stats(), IndexStats)

    def test_file_count(self) -> None:
        index = _build(_pr("a.py"), _pr("b.py"))
        assert index.stats().file_count == 2

    def test_symbol_count(self) -> None:
        index = _build(_pr("a.py", symbols=[_sym("x"), _sym("y")]))
        assert index.stats().symbol_count == 2

    def test_import_count(self) -> None:
        index = _build(_pr("a.py", imports=["import os", "import sys", "import re"]))
        assert index.stats().import_count == 3

    def test_languages(self) -> None:
        index = _build(
            _pr("a.py", lang=Language.PYTHON),
            _pr("b.go", lang=Language.GO),
        )
        assert Language.PYTHON in index.stats().languages
        assert Language.GO in index.stats().languages

    def test_error_file_count(self) -> None:
        index = _build(
            _pr("a.py", errors=["SyntaxError at line 1: bad"]),
            _pr("b.py"),
        )
        assert index.stats().error_file_count == 1

    @pytest.mark.skipif(sys.platform == "win32", reason="timer resolution too coarse for fast in-memory builds on Windows")
    def test_build_duration_positive(self) -> None:
        index = _build(_pr("a.py", symbols=[_sym("x")] * 100))
        assert index.stats().build_duration_ms > 0


# ---------------------------------------------------------------------------
# __len__ and __repr__
# ---------------------------------------------------------------------------


class TestDunder:
    def test_len_empty(self) -> None:
        assert len(RepositoryIndex.build(iter([]))) == 0

    def test_len_populated(self) -> None:
        index = _build(_pr("a.py", symbols=[_sym("x"), _sym("y"), _sym("z")]))
        assert len(index) == 3

    def test_repr_unbuilt(self) -> None:
        index = RepositoryIndex()
        assert "unbuilt" in repr(index)

    def test_repr_built(self) -> None:
        index = _build(_pr("a.py", symbols=[_sym("x")]))
        r = repr(index)
        assert "RepositoryIndex" in r
        assert "files=1" in r


# ---------------------------------------------------------------------------
# SymbolDef extended fields (Phase 4)
# ---------------------------------------------------------------------------


class TestSymbolDefExtendedFields:
    def test_defaults_are_none_and_empty(self) -> None:
        sym = _sym("foo")
        assert sym.signature is None
        assert sym.return_type is None
        assert sym.raises == []

    def test_signature_field_set(self) -> None:
        sym = _sym("foo", signature="(self, x: int) -> str")
        assert sym.signature == "(self, x: int) -> str"

    def test_return_type_field_set(self) -> None:
        sym = _sym("foo", return_type="str | None")
        assert sym.return_type == "str | None"

    def test_raises_field_set(self) -> None:
        sym = _sym("foo", raises=["ValueError", "OSError"])
        assert sym.raises == ["ValueError", "OSError"]

    def test_extended_fields_do_not_break_existing_code(self) -> None:
        # SymbolDef without the new fields should still construct fine
        sym = SymbolDef(
            name="foo",
            qualified_name="foo",
            kind=SymbolKind.FUNCTION,
            line_start=1,
            line_end=3,
        )
        assert sym.signature is None
        assert sym.return_type is None
        assert sym.raises == []


# ---------------------------------------------------------------------------
# Python parser populates extended fields
# ---------------------------------------------------------------------------


class TestPythonParserExtendedFields:
    def _parse_src(self, src: str):
        from src.repository.parsers.python_parser import PythonParser
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.py"
            p.write_text(src, encoding="utf-8")
            fi = FileInfo(path=p, relative_path="m.py", extension=".py",
                          language=Language.PYTHON, size=p.stat().st_size,
                          modified_at=p.stat().st_mtime, content_hash="")
            return PythonParser().parse(fi)

    def test_function_signature_extracted(self) -> None:
        r = self._parse_src("def foo(x: int, y: str = 'z') -> bool:\n    pass\n")
        sym = next(s for s in r.symbols if s.name == "foo")
        assert sym.signature is not None
        assert "x: int" in sym.signature or "x" in sym.signature
        assert "bool" in sym.signature or sym.return_type == "bool"

    def test_function_return_type_extracted(self) -> None:
        r = self._parse_src("def foo() -> str | None:\n    pass\n")
        sym = next(s for s in r.symbols if s.name == "foo")
        assert sym.return_type is not None
        assert "str" in sym.return_type

    def test_function_no_return_annotation(self) -> None:
        r = self._parse_src("def foo():\n    pass\n")
        sym = next(s for s in r.symbols if s.name == "foo")
        assert sym.return_type is None

    def test_method_signature_extracted(self) -> None:
        r = self._parse_src("class NewFoo:\n    def bar(self, x: int) -> None:\n        pass\n")
        sym = next(s for s in r.symbols if s.name == "bar")
        assert sym.signature is not None

    def test_raises_extracted_direct(self) -> None:
        src = "def foo():\n    raise ValueError('bad')\n    raise OSError()\n"
        r = self._parse_src(src)
        sym = next(s for s in r.symbols if s.name == "foo")
        assert "ValueError" in sym.raises
        assert "OSError" in sym.raises

    def test_raises_excludes_bare_raise(self) -> None:
        src = "def foo():\n    try:\n        pass\n    except Exception:\n        raise\n"
        r = self._parse_src(src)
        sym = next(s for s in r.symbols if s.name == "foo")
        # bare `raise` has no exc node
        assert sym.raises == []

    def test_class_does_not_have_signature(self) -> None:
        r = self._parse_src("class NewFoo:\n    pass\n")
        sym = next(s for s in r.symbols if s.name == "NewFoo")
        assert sym.signature is None

    def test_variable_has_no_extended_fields(self) -> None:
        r = self._parse_src("MAX = 10\n")
        sym = r.symbols[0]
        assert sym.signature is None
        assert sym.return_type is None
        assert sym.raises == []

    def test_async_function_signature(self) -> None:
        r = self._parse_src("async def fetch(url: str) -> bytes:\n    pass\n")
        sym = next(s for s in r.symbols if s.name == "fetch")
        assert sym.signature is not None
        assert sym.is_async is True


# ---------------------------------------------------------------------------
# Layer rule
# ---------------------------------------------------------------------------


class TestLayerRule:
    def test_index_only_imports_from_repository(self) -> None:
        src = Path(__file__).parent.parent.parent / "src" / "repository" / "index.py"
        tree = ast.parse(src.read_text(encoding="utf-8"))
        bad: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("src.") and not node.module.startswith(
                    "src.repository."
                ):
                    bad.append(node.module)
        assert not bad, f"index.py has forbidden imports: {bad}"


# ---------------------------------------------------------------------------
# Integration — build from real PythonParser output
# ---------------------------------------------------------------------------


class TestIntegrationPythonParser:
    def test_build_from_python_parser_output(self) -> None:
        from src.repository.parsers import ParserRegistry

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "a.py").write_text(
                "class NewFoo:\n    def bar(self) -> None:\n        pass\n",
                encoding="utf-8",
            )
            (root / "b.py").write_text("import os\nX = 1\n", encoding="utf-8")

            registry = ParserRegistry.default()
            results = [
                registry.parse(FileInfo(
                    path=root / name,
                    relative_path=name,
                    extension=".py",
                    language=Language.PYTHON,
                    size=(root / name).stat().st_size,
                    modified_at=0.0,
                    content_hash="",
                ))
                for name in ["a.py", "b.py"]
            ]
            results = [r for r in results if r is not None]

        index = RepositoryIndex.build(results)

        assert index.lookup_qualified("NewFoo") is not None
        assert index.lookup_qualified("NewFoo.bar") is not None
        assert index.lookup_qualified("NewFoo.bar").symbol.kind is SymbolKind.METHOD
        assert index.lookup_qualified("X").symbol.kind is SymbolKind.CONSTANT
        assert "import os" in index.imports_for("b.py")
        assert len(index.files_importing("os")) == 1

    def test_full_pipeline_scan_parse_index(self) -> None:
        from src.repository.scanner import RepositoryScanner
        from src.repository.parsers import ParserRegistry

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "a.py").write_text(
                "class Agent:\n    async def run(self) -> None:\n        pass\n",
                encoding="utf-8",
            )
            (root / "b.py").write_text(
                "from src import Agent\nMAX = 10\n",
                encoding="utf-8",
            )

            scanner = RepositoryScanner(root)
            scan = scanner.scan()
            registry = ParserRegistry.default()
            results = registry.parse_many(scan.source_files(Language.PYTHON))
            index = RepositoryIndex.build(results)

        assert len(index) > 0
        methods = index.symbols_by_kind(SymbolKind.METHOD)
        assert any(e.symbol.name == "run" for e in methods)
        assert index.stats().file_count == 2
        assert Language.PYTHON in index.stats().languages

    def test_build_pearl_own_src(self) -> None:
        """Build index from Pearl's own source — no crashes, reasonable counts."""
        from src.repository.scanner import RepositoryScanner
        from src.repository.parsers import ParserRegistry

        root = Path(__file__).parent.parent.parent
        scanner = RepositoryScanner(root)
        scan = scanner.scan()
        registry = ParserRegistry.default()
        results = registry.parse_many(scan.source_files(Language.PYTHON))
        index = RepositoryIndex.build(results)

        assert index.stats().symbol_count > 50
        assert index.stats().import_count > 20
        assert index.stats().error_file_count == 0
        # Spot-check known symbols
        assert index.lookup_qualified("PearlAgent") is not None or \
               len(index.lookup("PearlAgent")) > 0


# ---------------------------------------------------------------------------
# Performance benchmark
# ---------------------------------------------------------------------------


class TestIndexBenchmarks:
    def _many_results(self, n_files: int, syms_per_file: int) -> list[ParseResult]:
        results = []
        for i in range(n_files):
            name = f"file_{i}.py"
            fi = _fi(name)
            symbols = [
                _sym(f"sym_{i}_{j}", qn=f"Module{i}.sym_{j}",
                     kind=[SymbolKind.FUNCTION, SymbolKind.METHOD, SymbolKind.CLASS][j % 3])
                for j in range(syms_per_file)
            ]
            imports = [f"import module_{k}" for k in range(5)]
            results.append(ParseResult(file_info=fi, symbols=symbols,
                                       imports=imports, errors=[]))
        return results

    def test_build_5000_symbols_under_500ms(self) -> None:
        results = self._many_results(n_files=100, syms_per_file=50)
        t0 = time.monotonic()
        index = RepositoryIndex.build(results)
        elapsed_ms = (time.monotonic() - t0) * 1000

        assert len(index) == 5000
        assert elapsed_ms < 500, f"Build: {elapsed_ms:.1f}ms (limit: 500ms)"

    def test_lookup_after_build_fast(self) -> None:
        results = self._many_results(n_files=100, syms_per_file=50)
        index = RepositoryIndex.build(results)

        t0 = time.monotonic()
        for _ in range(1000):
            index.lookup("sym_0_0")
        elapsed_ms = (time.monotonic() - t0) * 1000

        assert elapsed_ms < 100, f"1000 lookups: {elapsed_ms:.1f}ms (limit: 100ms)"

    def test_search_across_5000_symbols_under_200ms(self) -> None:
        results = self._many_results(n_files=100, syms_per_file=50)
        index = RepositoryIndex.build(results)

        t0 = time.monotonic()
        result = index.search("*sym_*")
        elapsed_ms = (time.monotonic() - t0) * 1000

        assert len(result) == 5000
        assert elapsed_ms < 200, f"search(): {elapsed_ms:.1f}ms (limit: 200ms)"
