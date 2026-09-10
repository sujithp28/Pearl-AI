"""
Tests for Phase 2 — Language Parser Framework.

Coverage areas
--------------
* SymbolKind — all values, string-enum behaviour
* SymbolDef — construction, defaults, __repr__
* ParseResult — construction, language property, __repr__
* BaseParser — abstract methods enforced, can_parse default, __repr__
* ParserRegistry — register, replace, parser_for, supports,
  supported_languages, parse (success / no-parser / exception),
  parse_many, default(), __len__, __repr__
* Integration — concrete parser end-to-end through registry
* Layer rule — no imports from agent / tools / mcp
* Benchmarks — framework overhead is sub-millisecond per file
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest

from src.repository.models import FileInfo, Language, detect_language
from src.repository.parsers import (
    BaseParser,
    ParseResult,
    ParserRegistry,
    SymbolDef,
    SymbolKind,
)

# ---------------------------------------------------------------------------
# Test fixtures / concrete parser implementations
# ---------------------------------------------------------------------------


def _make_file_info(
    tmp_path: Path,
    filename: str = "module.py",
    content: str = "x = 1\n",
) -> FileInfo:
    """Write a file to tmp_path and return a FileInfo for it."""
    f = tmp_path / filename
    f.write_text(content, encoding="utf-8")
    return FileInfo(
        path=f,
        relative_path=filename,
        extension=Path(filename).suffix.lower(),
        language=detect_language(Path(filename)),
        size=f.stat().st_size,
        modified_at=f.stat().st_mtime,
        content_hash="",
    )


class FakePythonParser(BaseParser):
    """A deterministic stub that always returns two symbols."""

    @property
    def language(self) -> Language:
        return Language.PYTHON

    @property
    def supported_extensions(self) -> frozenset[str]:
        return frozenset({".py", ".pyi"})

    def parse(self, file_info: FileInfo) -> ParseResult:
        symbols = [
            SymbolDef(
                name="MyClass",
                qualified_name="module.MyClass",
                kind=SymbolKind.CLASS,
                line_start=1,
                line_end=10,
                docstring="A class.",
                decorators=[],
                is_async=False,
                parent=None,
            ),
            SymbolDef(
                name="my_func",
                qualified_name="module.my_func",
                kind=SymbolKind.FUNCTION,
                line_start=12,
                line_end=20,
                docstring=None,
                decorators=["staticmethod"],
                is_async=True,
                parent=None,
            ),
        ]
        return ParseResult(
            file_info=file_info,
            symbols=symbols,
            imports=["import os", "from pathlib import Path"],
            errors=[],
        )


class FakeGoParser(BaseParser):
    """Stub for Go."""

    @property
    def language(self) -> Language:
        return Language.GO

    @property
    def supported_extensions(self) -> frozenset[str]:
        return frozenset({".go"})

    def parse(self, file_info: FileInfo) -> ParseResult:
        return ParseResult(file_info=file_info)


class ExplodingParser(BaseParser):
    """Parser that always raises — used to test error containment."""

    @property
    def language(self) -> Language:
        return Language.RUST

    @property
    def supported_extensions(self) -> frozenset[str]:
        return frozenset({".rs"})

    def parse(self, file_info: FileInfo) -> ParseResult:
        raise RuntimeError("intentional parse explosion")


# ---------------------------------------------------------------------------
# SymbolKind
# ---------------------------------------------------------------------------


class TestSymbolKind:
    def test_all_values_are_strings(self) -> None:
        for kind in SymbolKind:
            assert isinstance(kind.value, str)

    def test_expected_kinds_exist(self) -> None:
        expected = {
            "class",
            "function",
            "method",
            "variable",
            "constant",
            "module",
            "decorator",
            "unknown",
        }
        assert {k.value for k in SymbolKind} == expected

    def test_is_str_enum(self) -> None:
        assert SymbolKind.CLASS == "class"
        assert SymbolKind.FUNCTION == "function"

    def test_identity_comparison(self) -> None:
        assert SymbolKind.CLASS is SymbolKind.CLASS
        assert SymbolKind.CLASS is not SymbolKind.FUNCTION

    def test_usable_in_set(self) -> None:
        kinds = {SymbolKind.CLASS, SymbolKind.FUNCTION}
        assert SymbolKind.CLASS in kinds
        assert SymbolKind.METHOD not in kinds


# ---------------------------------------------------------------------------
# SymbolDef
# ---------------------------------------------------------------------------


class TestSymbolDef:
    def test_required_fields(self) -> None:
        sym = SymbolDef(
            name="foo",
            qualified_name="mod.foo",
            kind=SymbolKind.FUNCTION,
            line_start=1,
            line_end=5,
        )
        assert sym.name == "foo"
        assert sym.qualified_name == "mod.foo"
        assert sym.kind is SymbolKind.FUNCTION
        assert sym.line_start == 1
        assert sym.line_end == 5

    def test_defaults(self) -> None:
        sym = SymbolDef(
            name="x",
            qualified_name="mod.x",
            kind=SymbolKind.VARIABLE,
            line_start=3,
            line_end=3,
        )
        assert sym.docstring is None
        assert sym.decorators == []
        assert sym.is_async is False
        assert sym.parent is None

    def test_optional_fields(self) -> None:
        sym = SymbolDef(
            name="bar",
            qualified_name="pkg.mod.bar",
            kind=SymbolKind.METHOD,
            line_start=10,
            line_end=20,
            docstring="Does bar.",
            decorators=["classmethod"],
            is_async=True,
            parent="pkg.mod.MyClass",
        )
        assert sym.docstring == "Does bar."
        assert sym.decorators == ["classmethod"]
        assert sym.is_async is True
        assert sym.parent == "pkg.mod.MyClass"

    def test_repr_sync(self) -> None:
        sym = SymbolDef(
            name="foo",
            qualified_name="mod.foo",
            kind=SymbolKind.CLASS,
            line_start=1,
            line_end=5,
        )
        r = repr(sym)
        assert "class" in r
        assert "mod.foo" in r
        assert "L1-5" in r
        assert "async" not in r

    def test_repr_async(self) -> None:
        sym = SymbolDef(
            name="bar",
            qualified_name="mod.bar",
            kind=SymbolKind.FUNCTION,
            line_start=7,
            line_end=12,
            is_async=True,
        )
        assert "async" in repr(sym)

    def test_decorators_default_is_independent(self) -> None:
        # Each SymbolDef should get its own list, not share a reference.
        sym_a = SymbolDef(
            name="a",
            qualified_name="a",
            kind=SymbolKind.FUNCTION,
            line_start=1,
            line_end=1,
        )
        sym_b = SymbolDef(
            name="b",
            qualified_name="b",
            kind=SymbolKind.FUNCTION,
            line_start=2,
            line_end=2,
        )
        sym_a.decorators.append("x")
        assert sym_b.decorators == []


# ---------------------------------------------------------------------------
# ParseResult
# ---------------------------------------------------------------------------


class TestParseResult:
    def test_minimal_construction(self, tmp_path: Path) -> None:
        fi = _make_file_info(tmp_path)
        pr = ParseResult(file_info=fi)
        assert pr.file_info is fi
        assert pr.symbols == []
        assert pr.imports == []
        assert pr.errors == []

    def test_language_property(self, tmp_path: Path) -> None:
        fi = _make_file_info(tmp_path, "app.ts")
        pr = ParseResult(file_info=fi)
        assert pr.language is Language.TYPESCRIPT

    def test_language_property_matches_file_info(self, tmp_path: Path) -> None:
        fi = _make_file_info(tmp_path, "main.go")
        pr = ParseResult(file_info=fi)
        assert pr.language is fi.language

    def test_full_construction(self, tmp_path: Path) -> None:
        fi = _make_file_info(tmp_path)
        sym = SymbolDef(
            name="f",
            qualified_name="mod.f",
            kind=SymbolKind.FUNCTION,
            line_start=1,
            line_end=2,
        )
        pr = ParseResult(
            file_info=fi,
            symbols=[sym],
            imports=["import os"],
            errors=["SyntaxWarning: …"],
        )
        assert len(pr.symbols) == 1
        assert pr.imports == ["import os"]
        assert pr.errors == ["SyntaxWarning: …"]

    def test_repr(self, tmp_path: Path) -> None:
        fi = _make_file_info(tmp_path)
        pr = ParseResult(file_info=fi)
        r = repr(pr)
        assert "module.py" in r
        assert "symbols=0" in r
        assert "imports=0" in r
        assert "errors=0" in r

    def test_symbols_default_is_independent(self, tmp_path: Path) -> None:
        fi_a = _make_file_info(tmp_path, "a.py")
        fi_b = _make_file_info(tmp_path, "b.py")
        pr_a = ParseResult(file_info=fi_a)
        pr_b = ParseResult(file_info=fi_b)
        pr_a.symbols.append(
            SymbolDef(
                name="x",
                qualified_name="x",
                kind=SymbolKind.VARIABLE,
                line_start=1,
                line_end=1,
            )
        )
        assert pr_b.symbols == []


# ---------------------------------------------------------------------------
# BaseParser
# ---------------------------------------------------------------------------


class TestBaseParser:
    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            BaseParser()  # type: ignore[abstract]

    def test_abstract_methods_must_be_implemented(self) -> None:
        class IncompleteParser(BaseParser):
            @property
            def language(self) -> Language:
                return Language.GO

            # Missing: supported_extensions, parse

        with pytest.raises(TypeError):
            IncompleteParser()  # type: ignore[abstract]

    def test_can_parse_default_uses_extension(self, tmp_path: Path) -> None:
        parser = FakePythonParser()
        py_fi = _make_file_info(tmp_path, "app.py")
        ts_fi = _make_file_info(tmp_path, "app.ts")
        pyi_fi = _make_file_info(tmp_path, "stub.pyi")

        assert parser.can_parse(py_fi) is True
        assert parser.can_parse(pyi_fi) is True
        assert parser.can_parse(ts_fi) is False

    def test_can_parse_override(self, tmp_path: Path) -> None:
        class CustomParser(BaseParser):
            @property
            def language(self) -> Language:
                return Language.PYTHON

            @property
            def supported_extensions(self) -> frozenset[str]:
                return frozenset({".py"})

            def can_parse(self, file_info: FileInfo) -> bool:
                # Only parse files whose name starts with "test_"
                return file_info.path.name.startswith("test_")

            def parse(self, file_info: FileInfo) -> ParseResult:
                return ParseResult(file_info=file_info)

        parser = CustomParser()
        fi_test = _make_file_info(tmp_path, "test_foo.py")
        fi_app = _make_file_info(tmp_path, "app.py")
        assert parser.can_parse(fi_test) is True
        assert parser.can_parse(fi_app) is False

    def test_repr(self) -> None:
        parser = FakePythonParser()
        r = repr(parser)
        assert "FakePythonParser" in r
        assert "python" in r

    def test_language_property(self) -> None:
        assert FakePythonParser().language is Language.PYTHON
        assert FakeGoParser().language is Language.GO

    def test_supported_extensions_is_frozenset(self) -> None:
        exts = FakePythonParser().supported_extensions
        assert isinstance(exts, frozenset)
        assert ".py" in exts


# ---------------------------------------------------------------------------
# ParserRegistry — registration
# ---------------------------------------------------------------------------


class TestParserRegistryRegistration:
    def test_empty_registry(self) -> None:
        registry = ParserRegistry()
        assert len(registry) == 0
        assert registry.supported_languages() == frozenset()

    def test_register_single_parser(self) -> None:
        registry = ParserRegistry()
        registry.register(FakePythonParser())
        assert len(registry) == 1
        assert Language.PYTHON in registry.supported_languages()

    def test_register_multiple_parsers(self) -> None:
        registry = ParserRegistry()
        registry.register(FakePythonParser())
        registry.register(FakeGoParser())
        assert len(registry) == 2
        langs = registry.supported_languages()
        assert Language.PYTHON in langs
        assert Language.GO in langs

    def test_register_replaces_existing(self) -> None:
        registry = ParserRegistry()
        registry.register(FakePythonParser())

        class AltPythonParser(FakePythonParser):
            pass

        registry.register(AltPythonParser())
        assert len(registry) == 1
        assert isinstance(registry.parser_for(Language.PYTHON), AltPythonParser)

    def test_register_returns_none(self) -> None:
        registry = ParserRegistry()
        result = registry.register(FakePythonParser())
        assert result is None


# ---------------------------------------------------------------------------
# ParserRegistry — capability detection
# ---------------------------------------------------------------------------


class TestParserRegistryCapability:
    def test_supports_registered_language(self) -> None:
        registry = ParserRegistry()
        registry.register(FakePythonParser())
        assert registry.supports(Language.PYTHON) is True

    def test_does_not_support_unregistered_language(self) -> None:
        registry = ParserRegistry()
        assert registry.supports(Language.PYTHON) is False

    def test_parser_for_returns_registered_parser(self) -> None:
        registry = ParserRegistry()
        parser = FakePythonParser()
        registry.register(parser)
        found = registry.parser_for(Language.PYTHON)
        assert found is parser

    def test_parser_for_returns_none_for_missing(self) -> None:
        registry = ParserRegistry()
        assert registry.parser_for(Language.GO) is None

    def test_supported_languages_is_frozenset(self) -> None:
        registry = ParserRegistry()
        registry.register(FakePythonParser())
        langs = registry.supported_languages()
        assert isinstance(langs, frozenset)

    def test_supported_languages_does_not_include_unregistered(self) -> None:
        registry = ParserRegistry()
        registry.register(FakePythonParser())
        langs = registry.supported_languages()
        assert Language.GO not in langs
        assert Language.RUST not in langs


# ---------------------------------------------------------------------------
# ParserRegistry — parsing
# ---------------------------------------------------------------------------


class TestParserRegistryParse:
    def test_parse_returns_none_when_no_parser(self, tmp_path: Path) -> None:
        registry = ParserRegistry()
        fi = _make_file_info(tmp_path)
        assert registry.parse(fi) is None

    def test_parse_calls_registered_parser(self, tmp_path: Path) -> None:
        registry = ParserRegistry()
        registry.register(FakePythonParser())
        fi = _make_file_info(tmp_path)
        result = registry.parse(fi)
        assert result is not None
        assert isinstance(result, ParseResult)
        assert result.file_info is fi

    def test_parse_returns_symbols(self, tmp_path: Path) -> None:
        registry = ParserRegistry()
        registry.register(FakePythonParser())
        fi = _make_file_info(tmp_path)
        result = registry.parse(fi)
        assert result is not None
        assert len(result.symbols) == 2

    def test_parse_contains_error_on_parser_exception(self, tmp_path: Path) -> None:
        registry = ParserRegistry()
        registry.register(ExplodingParser())
        fi = _make_file_info(tmp_path, "code.rs")
        result = registry.parse(fi)
        assert result is not None
        assert len(result.errors) == 1
        assert "RuntimeError" in result.errors[0]
        assert "intentional parse explosion" in result.errors[0]

    def test_parse_exception_does_not_propagate(self, tmp_path: Path) -> None:
        registry = ParserRegistry()
        registry.register(ExplodingParser())
        fi = _make_file_info(tmp_path, "code.rs")
        # Must not raise — exception must be absorbed
        result = registry.parse(fi)
        assert result is not None

    def test_parse_result_file_info_on_exception(self, tmp_path: Path) -> None:
        registry = ParserRegistry()
        registry.register(ExplodingParser())
        fi = _make_file_info(tmp_path, "code.rs")
        result = registry.parse(fi)
        assert result is not None
        assert result.file_info is fi


# ---------------------------------------------------------------------------
# ParserRegistry — parse_many
# ---------------------------------------------------------------------------


class TestParserRegistryParseMany:
    def test_parse_many_empty_input(self) -> None:
        registry = ParserRegistry()
        registry.register(FakePythonParser())
        results = registry.parse_many([])
        assert results == []

    def test_parse_many_all_supported(self, tmp_path: Path) -> None:
        registry = ParserRegistry()
        registry.register(FakePythonParser())
        fis = [
            _make_file_info(tmp_path, "a.py"),
            _make_file_info(tmp_path, "b.py"),
        ]
        results = registry.parse_many(fis)
        assert len(results) == 2

    def test_parse_many_skips_unsupported_languages(self, tmp_path: Path) -> None:
        registry = ParserRegistry()
        registry.register(FakePythonParser())
        fis = [
            _make_file_info(tmp_path, "a.py"),  # supported
            _make_file_info(tmp_path, "b.ts"),  # not registered
            _make_file_info(tmp_path, "c.go"),  # not registered
        ]
        results = registry.parse_many(fis)
        assert len(results) == 1
        assert results[0].file_info.relative_path == "a.py"

    def test_parse_many_mixed_languages(self, tmp_path: Path) -> None:
        registry = ParserRegistry()
        registry.register(FakePythonParser())
        registry.register(FakeGoParser())
        fis = [
            _make_file_info(tmp_path, "a.py"),
            _make_file_info(tmp_path, "b.go"),
            _make_file_info(tmp_path, "c.ts"),
        ]
        results = registry.parse_many(fis)
        assert len(results) == 2
        langs = {pr.language for pr in results}
        assert Language.PYTHON in langs
        assert Language.GO in langs

    def test_parse_many_preserves_order(self, tmp_path: Path) -> None:
        registry = ParserRegistry()
        registry.register(FakePythonParser())
        fis = [_make_file_info(tmp_path, f"f{i}.py") for i in range(5)]
        results = registry.parse_many(fis)
        rel_paths = [pr.file_info.relative_path for pr in results]
        assert rel_paths == [f"f{i}.py" for i in range(5)]

    def test_parse_many_exception_in_one_file_continues(self, tmp_path: Path) -> None:
        registry = ParserRegistry()
        registry.register(ExplodingParser())
        fis = [
            _make_file_info(tmp_path, "a.rs"),
            _make_file_info(tmp_path, "b.rs"),
        ]
        results = registry.parse_many(fis)
        assert len(results) == 2
        assert all(len(pr.errors) == 1 for pr in results)

    def test_parse_many_accepts_generator(self, tmp_path: Path) -> None:
        registry = ParserRegistry()
        registry.register(FakePythonParser())

        def gen() -> Iterator:
            yield _make_file_info(tmp_path, "x.py")

        results = registry.parse_many(gen())
        assert len(results) == 1


# ---------------------------------------------------------------------------
# ParserRegistry — factory and dunder methods
# ---------------------------------------------------------------------------


class TestParserRegistryFactory:
    def test_default_returns_registry_instance(self) -> None:
        registry = ParserRegistry.default()
        assert isinstance(registry, ParserRegistry)

    def test_default_includes_python_parser(self) -> None:
        # Phase 3: PythonParser is registered in default().
        registry = ParserRegistry.default()
        assert len(registry) >= 1
        assert registry.supports(Language.PYTHON)

    def test_default_returns_new_instance_each_call(self) -> None:
        r1 = ParserRegistry.default()
        r2 = ParserRegistry.default()
        assert r1 is not r2

    def test_default_is_independently_extendable(self) -> None:
        r1 = ParserRegistry.default()
        # Overwrite PythonParser with FakePythonParser in r1
        r1.register(FakePythonParser())
        r2 = ParserRegistry.default()
        # r2 must be an independent copy — it still has the real PythonParser,
        # not the FakePythonParser we injected into r1.
        assert r2.parser_for(Language.PYTHON) is not r1.parser_for(Language.PYTHON)

    def test_len_empty(self) -> None:
        assert len(ParserRegistry()) == 0

    def test_len_after_register(self) -> None:
        registry = ParserRegistry()
        registry.register(FakePythonParser())
        registry.register(FakeGoParser())
        assert len(registry) == 2

    def test_repr_empty(self) -> None:
        assert repr(ParserRegistry()) == "ParserRegistry([])"

    def test_repr_with_parsers(self) -> None:
        registry = ParserRegistry()
        registry.register(FakePythonParser())
        registry.register(FakeGoParser())
        r = repr(registry)
        assert "go" in r
        assert "python" in r


# ---------------------------------------------------------------------------
# Integration — end-to-end through registry
# ---------------------------------------------------------------------------


class TestParserFrameworkIntegration:
    def test_full_parse_workflow(self, tmp_path: Path) -> None:
        registry = ParserRegistry()
        registry.register(FakePythonParser())

        fi = _make_file_info(tmp_path, "service.py", content="class Foo:\n    pass\n")
        result = registry.parse(fi)

        assert result is not None
        assert result.language is Language.PYTHON
        assert len(result.symbols) == 2
        classes = [s for s in result.symbols if s.kind is SymbolKind.CLASS]
        assert len(classes) == 1
        assert classes[0].name == "MyClass"
        assert classes[0].qualified_name == "module.MyClass"
        assert classes[0].docstring == "A class."
        assert classes[0].line_start == 1
        assert classes[0].line_end == 10

    def test_async_function_in_result(self, tmp_path: Path) -> None:
        registry = ParserRegistry()
        registry.register(FakePythonParser())
        fi = _make_file_info(tmp_path)
        result = registry.parse(fi)
        assert result is not None
        async_fns = [s for s in result.symbols if s.is_async]
        assert len(async_fns) == 1
        assert async_fns[0].name == "my_func"
        assert async_fns[0].decorators == ["staticmethod"]

    def test_imports_in_result(self, tmp_path: Path) -> None:
        registry = ParserRegistry()
        registry.register(FakePythonParser())
        fi = _make_file_info(tmp_path)
        result = registry.parse(fi)
        assert result is not None
        assert "import os" in result.imports

    def test_parse_many_with_scan_result(self, tmp_path: Path) -> None:
        from src.repository import RepositoryScanner

        (tmp_path / "a.py").write_text("x = 1", encoding="utf-8")
        (tmp_path / "b.py").write_text("y = 2", encoding="utf-8")
        (tmp_path / "c.ts").write_text("z = 3", encoding="utf-8")

        scan = RepositoryScanner(tmp_path).scan()
        registry = ParserRegistry()
        registry.register(FakePythonParser())

        py_files = scan.source_files(Language.PYTHON)
        results = registry.parse_many(py_files)

        assert len(results) == 2
        assert all(pr.language is Language.PYTHON for pr in results)

    def test_no_parser_for_language_returns_none(self, tmp_path: Path) -> None:
        registry = ParserRegistry()
        fi = _make_file_info(tmp_path, "code.rs")
        assert registry.parse(fi) is None

    def test_error_result_has_empty_symbols(self, tmp_path: Path) -> None:
        registry = ParserRegistry()
        registry.register(ExplodingParser())
        fi = _make_file_info(tmp_path, "lib.rs")
        result = registry.parse(fi)
        assert result is not None
        assert result.symbols == []

    def test_replacing_parser_changes_behaviour(self, tmp_path: Path) -> None:
        class MinimalParser(BaseParser):
            @property
            def language(self) -> Language:
                return Language.PYTHON

            @property
            def supported_extensions(self) -> frozenset[str]:
                return frozenset({".py"})

            def parse(self, file_info: FileInfo) -> ParseResult:
                return ParseResult(file_info=file_info, symbols=[], imports=[])

        registry = ParserRegistry()
        registry.register(FakePythonParser())
        fi = _make_file_info(tmp_path)
        result1 = registry.parse(fi)
        assert result1 is not None
        assert len(result1.symbols) == 2

        registry.register(MinimalParser())
        result2 = registry.parse(fi)
        assert result2 is not None
        assert len(result2.symbols) == 0


# ---------------------------------------------------------------------------
# Layer rule — parsers must not import from agent / tools / mcp
# ---------------------------------------------------------------------------


class TestLayerRule:
    def test_parsers_package_does_not_import_agent(self) -> None:
        import ast

        src = (
            Path(__file__).parent.parent.parent
            / "src"
            / "repository"
            / "parsers"
            / "__init__.py"
        )
        tree = ast.parse(src.read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
        forbidden = {"src.agent", "src.tools", "src.mcp", "src.prompts"}
        for imp in imports:
            for bad in forbidden:
                assert not imp.startswith(bad), (
                    f"parsers/__init__.py imports from forbidden layer: {imp!r}"
                )

    def test_parsers_only_imports_from_models(self) -> None:
        import ast

        src = (
            Path(__file__).parent.parent.parent
            / "src"
            / "repository"
            / "parsers"
            / "__init__.py"
        )
        tree = ast.parse(src.read_text(encoding="utf-8"))
        src_imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("src."):
                    src_imports.add(node.module)
        # Intra-package deferred imports (e.g. inside default()) are allowed.
        allowed_prefixes = ("src.repository.models", "src.repository.parsers.")
        unexpected = {
            imp
            for imp in src_imports
            if not any(imp == a or imp.startswith(a) for a in allowed_prefixes)
        }
        assert not unexpected, (
            f"parsers/__init__.py has unexpected src.* imports: {unexpected}"
        )


# ---------------------------------------------------------------------------
# Benchmarks — framework overhead
# ---------------------------------------------------------------------------


class TestParserFrameworkBenchmarks:
    """Verify the framework itself adds negligible overhead.

    These benchmarks test only the registry dispatch and error-containment
    machinery, not any language parser.  Language-specific benchmarks live
    in test_python_parser.py.
    """

    def _make_many_files(
        self, tmp_path: Path, count: int, lang_ext: str = ".py"
    ) -> list[FileInfo]:
        files = []
        for i in range(count):
            f = tmp_path / f"file_{i}{lang_ext}"
            f.write_text(f"x = {i}", encoding="utf-8")
            files.append(
                FileInfo(
                    path=f,
                    relative_path=f"file_{i}{lang_ext}",
                    extension=lang_ext,
                    language=detect_language(Path(f"file_{i}{lang_ext}")),
                    size=f.stat().st_size,
                    modified_at=f.stat().st_mtime,
                    content_hash="",
                )
            )
        return files

    def test_registry_dispatch_100_files_under_100ms(self, tmp_path: Path) -> None:
        """Registry overhead for 100 files must be under 100 ms."""
        import time

        registry = ParserRegistry()
        registry.register(FakePythonParser())
        files = self._make_many_files(tmp_path, 100)

        start = time.monotonic()
        results = registry.parse_many(files)
        elapsed_ms = (time.monotonic() - start) * 1000

        assert len(results) == 100
        assert elapsed_ms < 100, (
            f"Framework dispatch took {elapsed_ms:.1f} ms (limit: 100 ms)"
        )

    def test_registry_skip_overhead_500_files_under_200ms(self, tmp_path: Path) -> None:
        """Skipping 500 unsupported files must cost under 200 ms."""
        import time

        registry = ParserRegistry()
        registry.register(FakePythonParser())
        # All TypeScript — no parser registered for them
        files = self._make_many_files(tmp_path, 500, ".ts")

        start = time.monotonic()
        results = registry.parse_many(files)
        elapsed_ms = (time.monotonic() - start) * 1000

        assert results == []
        assert elapsed_ms < 200, (
            f"Skip overhead for 500 files: {elapsed_ms:.1f} ms (limit: 200 ms)"
        )

    def test_error_containment_overhead_is_negligible(self, tmp_path: Path) -> None:
        """Containing parser exceptions must add less than 5 ms per file."""
        import time

        class QuickBombParser(BaseParser):
            @property
            def language(self) -> Language:
                return Language.GO

            @property
            def supported_extensions(self) -> frozenset[str]:
                return frozenset({".go"})

            def parse(self, file_info: FileInfo) -> ParseResult:
                raise RuntimeError("boom")

        registry = ParserRegistry()
        registry.register(QuickBombParser())
        files = self._make_many_files(tmp_path, 50, ".go")

        start = time.monotonic()
        results = registry.parse_many(files)
        elapsed_ms = (time.monotonic() - start) * 1000

        assert len(results) == 50
        assert all(len(r.errors) == 1 for r in results)
        per_file_ms = elapsed_ms / 50
        assert per_file_ms < 5, (
            f"Error containment overhead: {per_file_ms:.2f} ms/file (limit: 5 ms)"
        )
