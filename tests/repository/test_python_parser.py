"""
Tests for Phase 3 — Python AST Parser.

Coverage areas
--------------
* Language / extension contract
* Empty, comments-only, and stub files
* Class extraction — top-level, nested, decorated, docstring, line numbers
* Function extraction — top-level, sync, async, decorated, nested, docstring
* Method extraction — kind=METHOD inside ClassDef; kind=FUNCTION inside ClassDef.method
* Variable / constant extraction — module scope, class scope, not inside functions
* Constant classification (ALL_CAPS rule)
* Decorator extraction — Name, Attribute, Call, chained Call
* Import extraction — absolute, from-import, relative, multi-name,
  inside functions, inside if TYPE_CHECKING
* Qualified-name building — nested classes, nested functions, methods
* Error handling — SyntaxError, OSError, non-UTF-8 encoding, empty file
* Layer rule — no imports from agent / tools / mcp
* Performance benchmark — parse time for a large realistic file
* Integration — PythonParser registered in ParserRegistry.default()
* Integration — parse Pearl's own source files without error
"""

from __future__ import annotations

import ast
import os
import stat
import time
from pathlib import Path

import pytest

from src.repository.models import FileInfo, Language
from src.repository.parsers import ParseResult, ParserRegistry, SymbolDef, SymbolKind
from src.repository.parsers.python_parser import (
    PythonParser,
    _classify_name,
    _decorator_name,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fi(tmp: Path, name: str, content: str, encoding: str = "utf-8") -> FileInfo:
    """Write *content* to tmp/name and return a FileInfo for it."""
    path = tmp / name
    path.write_text(content, encoding=encoding)
    return FileInfo(
        path=path,
        relative_path=name,
        extension=path.suffix.lower(),
        language=Language.PYTHON,
        size=path.stat().st_size,
        modified_at=path.stat().st_mtime,
        content_hash="",
    )


def _fi_bytes(tmp: Path, name: str, data: bytes) -> FileInfo:
    """Write raw *data* to tmp/name and return a FileInfo for it."""
    path = tmp / name
    path.write_bytes(data)
    return FileInfo(
        path=path,
        relative_path=name,
        extension=path.suffix.lower(),
        language=Language.PYTHON,
        size=path.stat().st_size,
        modified_at=path.stat().st_mtime,
        content_hash="",
    )


def _parse(tmp: Path, name: str, content: str, encoding: str = "utf-8") -> ParseResult:
    return PythonParser().parse(_fi(tmp, name, content, encoding=encoding))


def _syms(result: ParseResult) -> dict[str, SymbolDef]:
    """Return {qualified_name: SymbolDef} for quick lookup."""
    return {s.qualified_name: s for s in result.symbols}


# ---------------------------------------------------------------------------
# Language / extension contract
# ---------------------------------------------------------------------------


class TestPythonParserContract:
    def test_language_is_python(self) -> None:
        assert PythonParser().language is Language.PYTHON

    def test_supported_extensions(self) -> None:
        exts = PythonParser().supported_extensions
        assert ".py" in exts
        assert ".pyi" in exts
        assert ".pyx" in exts
        assert isinstance(exts, frozenset)

    def test_can_parse_py(self, tmp_path: Path) -> None:
        assert PythonParser().can_parse(_fi(tmp_path, "f.py", ""))

    def test_can_parse_pyi(self, tmp_path: Path) -> None:
        assert PythonParser().can_parse(_fi(tmp_path, "stub.pyi", ""))

    def test_cannot_parse_ts(self, tmp_path: Path) -> None:
        fi = _fi(tmp_path, "app.ts", "")
        fi2 = FileInfo(path=fi.path, relative_path="app.ts", extension=".ts",
                       language=Language.TYPESCRIPT, size=fi.size,
                       modified_at=fi.modified_at, content_hash="")
        assert not PythonParser().can_parse(fi2)

    def test_parse_returns_parse_result(self, tmp_path: Path) -> None:
        result = _parse(tmp_path, "m.py", "")
        assert isinstance(result, ParseResult)

    def test_parse_result_file_info_is_preserved(self, tmp_path: Path) -> None:
        fi = _fi(tmp_path, "m.py", "x = 1")
        result = PythonParser().parse(fi)
        assert result.file_info is fi


# ---------------------------------------------------------------------------
# Edge cases — empty and trivial files
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_file(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "empty.py", "")
        assert r.symbols == []
        assert r.imports == []
        assert r.errors == []

    def test_comments_only(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "comments.py", "# Just a comment\n# Another\n")
        assert r.symbols == []
        assert r.errors == []

    def test_module_docstring_only(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "doc.py", '"""Module docstring."""\n')
        assert r.errors == []
        # Module-level string expression — no SymbolDef produced
        assert all(s.kind is not SymbolKind.FUNCTION for s in r.symbols)

    def test_pass_only(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "pass.py", "pass\n")
        assert r.symbols == []
        assert r.errors == []

    def test_ellipsis_body_class(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "stub.pyi", "class Foo:\n    ...\n")
        assert len(r.symbols) == 1
        assert r.symbols[0].kind is SymbolKind.CLASS

    def test_single_import(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "imp.py", "import os\n")
        assert r.imports == ["import os"]
        assert r.symbols == []


# ---------------------------------------------------------------------------
# Class extraction
# ---------------------------------------------------------------------------


class TestClassExtraction:
    def test_top_level_class(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "m.py", "class Foo:\n    pass\n")
        assert len(r.symbols) == 1
        s = r.symbols[0]
        assert s.name == "Foo"
        assert s.qualified_name == "Foo"
        assert s.kind is SymbolKind.CLASS
        assert s.parent is None

    def test_class_line_numbers(self, tmp_path: Path) -> None:
        src = "# line 1\nclass Foo:\n    pass\n"
        r = _parse(tmp_path, "m.py", src)
        s = r.symbols[0]
        assert s.line_start == 2
        assert s.line_end == 3

    def test_class_with_docstring(self, tmp_path: Path) -> None:
        src = 'class Foo:\n    """My docstring."""\n    pass\n'
        r = _parse(tmp_path, "m.py", src)
        assert r.symbols[0].docstring == "My docstring."

    def test_class_without_docstring(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "m.py", "class Foo:\n    x = 1\n")
        assert r.symbols[0].docstring is None

    def test_class_is_not_async(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "m.py", "class Foo:\n    pass\n")
        assert r.symbols[0].is_async is False

    def test_decorated_class(self, tmp_path: Path) -> None:
        src = "@dataclass\nclass Point:\n    x: int\n    y: int\n"
        r = _parse(tmp_path, "m.py", src)
        classes = [s for s in r.symbols if s.kind is SymbolKind.CLASS]
        assert classes[0].decorators == ["dataclass"]

    def test_multiple_decorators_on_class(self, tmp_path: Path) -> None:
        src = "@one\n@two\nclass Foo:\n    pass\n"
        r = _parse(tmp_path, "m.py", src)
        assert r.symbols[0].decorators == ["one", "two"]

    def test_multiple_classes(self, tmp_path: Path) -> None:
        src = "class A:\n    pass\n\nclass B:\n    pass\n"
        r = _parse(tmp_path, "m.py", src)
        names = [s.qualified_name for s in r.symbols]
        assert "A" in names
        assert "B" in names

    def test_nested_class(self, tmp_path: Path) -> None:
        src = "class Outer:\n    class Inner:\n        pass\n"
        r = _parse(tmp_path, "m.py", src)
        by_qn = _syms(r)
        assert "Outer" in by_qn
        assert "Outer.Inner" in by_qn
        assert by_qn["Outer.Inner"].parent == "Outer"

    def test_three_level_nesting(self, tmp_path: Path) -> None:
        src = "class A:\n    class B:\n        class C:\n            pass\n"
        r = _parse(tmp_path, "m.py", src)
        by_qn = _syms(r)
        assert "A.B.C" in by_qn
        assert by_qn["A.B.C"].parent == "A.B"

    def test_class_inheriting_base(self, tmp_path: Path) -> None:
        src = "class Parent:\n    pass\n\nclass Child(Parent):\n    pass\n"
        r = _parse(tmp_path, "m.py", src)
        names = {s.name for s in r.symbols if s.kind is SymbolKind.CLASS}
        assert {"Parent", "Child"} == names


# ---------------------------------------------------------------------------
# Function extraction
# ---------------------------------------------------------------------------


class TestFunctionExtraction:
    def test_top_level_function(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "m.py", "def foo():\n    pass\n")
        assert len(r.symbols) == 1
        s = r.symbols[0]
        assert s.name == "foo"
        assert s.qualified_name == "foo"
        assert s.kind is SymbolKind.FUNCTION
        assert s.is_async is False
        assert s.parent is None

    def test_async_function(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "m.py", "async def fetch():\n    pass\n")
        s = r.symbols[0]
        assert s.kind is SymbolKind.FUNCTION
        assert s.is_async is True

    def test_function_with_docstring(self, tmp_path: Path) -> None:
        src = 'def foo():\n    """Does foo."""\n    pass\n'
        r = _parse(tmp_path, "m.py", src)
        assert r.symbols[0].docstring == "Does foo."

    def test_function_line_numbers(self, tmp_path: Path) -> None:
        src = "x = 1\ndef foo():\n    return 1\n"
        r = _parse(tmp_path, "m.py", src)
        fn = next(s for s in r.symbols if s.kind is SymbolKind.FUNCTION)
        assert fn.line_start == 2
        assert fn.line_end == 3

    def test_decorated_function(self, tmp_path: Path) -> None:
        src = "@staticmethod\ndef foo():\n    pass\n"
        r = _parse(tmp_path, "m.py", src)
        assert r.symbols[0].decorators == ["staticmethod"]

    def test_decorator_with_call(self, tmp_path: Path) -> None:
        src = "@lru_cache(maxsize=128)\ndef foo():\n    pass\n"
        r = _parse(tmp_path, "m.py", src)
        assert r.symbols[0].decorators == ["lru_cache"]

    def test_decorator_with_attribute(self, tmp_path: Path) -> None:
        src = "@functools.wraps(f)\ndef wrapper():\n    pass\n"
        r = _parse(tmp_path, "m.py", src)
        assert r.symbols[0].decorators == ["wraps"]

    def test_tool_decorator_pattern(self, tmp_path: Path) -> None:
        src = "@tool(\n    name='read_file',\n)\ndef read_file(path: str) -> str:\n    pass\n"
        r = _parse(tmp_path, "m.py", src)
        assert r.symbols[0].decorators == ["tool"]

    def test_nested_function_inside_function(self, tmp_path: Path) -> None:
        src = "def outer():\n    def inner():\n        pass\n"
        r = _parse(tmp_path, "m.py", src)
        by_qn = _syms(r)
        assert "outer" in by_qn
        assert "outer.inner" in by_qn
        assert by_qn["outer.inner"].kind is SymbolKind.FUNCTION
        assert by_qn["outer.inner"].parent == "outer"

    def test_async_nested_function(self, tmp_path: Path) -> None:
        src = "async def outer():\n    async def inner():\n        pass\n"
        r = _parse(tmp_path, "m.py", src)
        by_qn = _syms(r)
        assert by_qn["outer"].is_async is True
        assert by_qn["outer.inner"].is_async is True


# ---------------------------------------------------------------------------
# Method extraction
# ---------------------------------------------------------------------------


class TestMethodExtraction:
    def test_method_kind_is_method(self, tmp_path: Path) -> None:
        src = "class Foo:\n    def bar(self):\n        pass\n"
        r = _parse(tmp_path, "m.py", src)
        by_qn = _syms(r)
        assert by_qn["Foo.bar"].kind is SymbolKind.METHOD

    def test_async_method(self, tmp_path: Path) -> None:
        src = "class Foo:\n    async def bar(self):\n        pass\n"
        r = _parse(tmp_path, "m.py", src)
        s = _syms(r)["Foo.bar"]
        assert s.kind is SymbolKind.METHOD
        assert s.is_async is True

    def test_method_parent_is_class(self, tmp_path: Path) -> None:
        src = "class Foo:\n    def bar(self):\n        pass\n"
        r = _parse(tmp_path, "m.py", src)
        assert _syms(r)["Foo.bar"].parent == "Foo"

    def test_classmethod_decorator(self, tmp_path: Path) -> None:
        src = "class Foo:\n    @classmethod\n    def create(cls):\n        pass\n"
        r = _parse(tmp_path, "m.py", src)
        s = _syms(r)["Foo.create"]
        assert s.kind is SymbolKind.METHOD
        assert "classmethod" in s.decorators

    def test_staticmethod_decorator(self, tmp_path: Path) -> None:
        src = "class Foo:\n    @staticmethod\n    def helper():\n        pass\n"
        r = _parse(tmp_path, "m.py", src)
        assert "staticmethod" in _syms(r)["Foo.helper"].decorators

    def test_property_decorator(self, tmp_path: Path) -> None:
        src = "class Foo:\n    @property\n    def value(self):\n        return 0\n"
        r = _parse(tmp_path, "m.py", src)
        assert "property" in _syms(r)["Foo.value"].decorators

    def test_nested_function_inside_method_is_function_not_method(
        self, tmp_path: Path
    ) -> None:
        src = "class Foo:\n    def bar(self):\n        def helper():\n            pass\n"
        r = _parse(tmp_path, "m.py", src)
        assert _syms(r)["Foo.bar.helper"].kind is SymbolKind.FUNCTION

    def test_nested_class_inside_method_methods_are_methods(
        self, tmp_path: Path
    ) -> None:
        src = (
            "class Outer:\n"
            "    def factory(self):\n"
            "        class Local:\n"
            "            def local_method(self):\n"
            "                pass\n"
        )
        r = _parse(tmp_path, "m.py", src)
        by_qn = _syms(r)
        assert by_qn["Outer.factory.Local.local_method"].kind is SymbolKind.METHOD

    def test_dunder_methods_extracted(self, tmp_path: Path) -> None:
        src = "class Foo:\n    def __init__(self):\n        pass\n    def __repr__(self):\n        pass\n"
        r = _parse(tmp_path, "m.py", src)
        by_qn = _syms(r)
        assert "Foo.__init__" in by_qn
        assert "Foo.__repr__" in by_qn

    def test_multiple_methods_in_source_order(self, tmp_path: Path) -> None:
        src = "class Foo:\n    def a(self):\n        pass\n    def b(self):\n        pass\n    def c(self):\n        pass\n"
        r = _parse(tmp_path, "m.py", src)
        methods = [s for s in r.symbols if s.kind is SymbolKind.METHOD]
        assert [s.name for s in methods] == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# Variable and constant extraction
# ---------------------------------------------------------------------------


class TestVariableConstantExtraction:
    def test_module_level_constant(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "m.py", "MAX_SIZE = 100\n")
        s = r.symbols[0]
        assert s.name == "MAX_SIZE"
        assert s.kind is SymbolKind.CONSTANT
        assert s.parent is None

    def test_module_level_variable(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "m.py", "name = 'x'\n")
        s = r.symbols[0]
        assert s.name == "name"
        assert s.kind is SymbolKind.VARIABLE

    def test_annotated_variable(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "m.py", "count: int = 0\n")
        assert r.symbols[0].kind is SymbolKind.VARIABLE

    def test_annotated_constant(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "m.py", "MAX: int = 100\n")
        assert r.symbols[0].kind is SymbolKind.CONSTANT

    def test_annotation_only_no_value(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "m.py", "x: int\n")
        assert len(r.symbols) == 1
        assert r.symbols[0].name == "x"

    def test_class_level_variable(self, tmp_path: Path) -> None:
        src = "class Foo:\n    count = 0\n"
        r = _parse(tmp_path, "m.py", src)
        by_qn = _syms(r)
        assert "Foo.count" in by_qn
        assert by_qn["Foo.count"].kind is SymbolKind.VARIABLE
        assert by_qn["Foo.count"].parent == "Foo"

    def test_class_level_constant(self, tmp_path: Path) -> None:
        src = "class Foo:\n    MAX = 10\n"
        r = _parse(tmp_path, "m.py", src)
        assert _syms(r)["Foo.MAX"].kind is SymbolKind.CONSTANT

    def test_local_variable_not_extracted(self, tmp_path: Path) -> None:
        src = "def foo():\n    local = 42\n    ALSO_LOCAL = 99\n"
        r = _parse(tmp_path, "m.py", src)
        names = {s.name for s in r.symbols}
        assert "local" not in names
        assert "ALSO_LOCAL" not in names

    def test_tuple_unpack_not_extracted(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "m.py", "a, b = 1, 2\n")
        assert r.symbols == []

    def test_chained_assign_not_extracted(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "m.py", "a = b = 0\n")
        assert r.symbols == []

    def test_dunder_variable(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "m.py", "__version__ = '1.0'\n")
        s = r.symbols[0]
        assert s.kind is SymbolKind.VARIABLE  # lowercase letters → not constant

    def test_private_constant(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "m.py", "_MAX_RETRIES = 3\n")
        assert r.symbols[0].kind is SymbolKind.CONSTANT

    def test_variable_no_docstring(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "m.py", "X = 1\n")
        assert r.symbols[0].docstring is None

    def test_variable_no_decorators(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "m.py", "X = 1\n")
        assert r.symbols[0].decorators == []

    def test_variable_is_not_async(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "m.py", "X = 1\n")
        assert r.symbols[0].is_async is False

    def test_variable_qualified_name(self, tmp_path: Path) -> None:
        src = "class Foo:\n    BAR = 1\n"
        r = _parse(tmp_path, "m.py", src)
        by_qn = _syms(r)
        assert "Foo.BAR" in by_qn
        assert by_qn["Foo.BAR"].qualified_name == "Foo.BAR"


# ---------------------------------------------------------------------------
# Constant classification rules
# ---------------------------------------------------------------------------


class TestClassifyName:
    @pytest.mark.parametrize("name,expected", [
        ("MAX_RETRIES", SymbolKind.CONSTANT),
        ("HTTP_404", SymbolKind.CONSTANT),
        ("_PRIVATE", SymbolKind.CONSTANT),
        ("A", SymbolKind.CONSTANT),
        ("UPPER", SymbolKind.CONSTANT),
        ("HTTP2", SymbolKind.CONSTANT),
        ("variable", SymbolKind.VARIABLE),
        ("camelCase", SymbolKind.VARIABLE),
        ("MyClass", SymbolKind.VARIABLE),
        ("__version__", SymbolKind.VARIABLE),
        ("__all__", SymbolKind.VARIABLE),
        ("_private_var", SymbolKind.VARIABLE),
        ("mixedUPPER", SymbolKind.VARIABLE),
    ])
    def test_classify(self, name: str, expected: SymbolKind) -> None:
        assert _classify_name(name) is expected


# ---------------------------------------------------------------------------
# Decorator extraction
# ---------------------------------------------------------------------------


class TestDecoratorExtraction:
    def test_simple_name(self) -> None:
        node = ast.parse("@foo\ndef f(): pass\n").body[0]
        assert isinstance(node, ast.FunctionDef)
        assert _decorator_name(node.decorator_list[0]) == "foo"

    def test_attribute(self) -> None:
        node = ast.parse("@mod.dec\ndef f(): pass\n").body[0]
        assert isinstance(node, ast.FunctionDef)
        assert _decorator_name(node.decorator_list[0]) == "dec"

    def test_call_with_name(self) -> None:
        node = ast.parse("@lru_cache(maxsize=128)\ndef f(): pass\n").body[0]
        assert isinstance(node, ast.FunctionDef)
        assert _decorator_name(node.decorator_list[0]) == "lru_cache"

    def test_call_with_attribute(self) -> None:
        node = ast.parse("@functools.wraps(f)\ndef w(): pass\n").body[0]
        assert isinstance(node, ast.FunctionDef)
        assert _decorator_name(node.decorator_list[0]) == "wraps"

    def test_nested_call(self) -> None:
        node = ast.parse("@outer(inner())\ndef f(): pass\n").body[0]
        assert isinstance(node, ast.FunctionDef)
        assert _decorator_name(node.decorator_list[0]) == "outer"

    def test_chained_attribute(self) -> None:
        node = ast.parse("@a.b.c\ndef f(): pass\n").body[0]
        assert isinstance(node, ast.FunctionDef)
        assert _decorator_name(node.decorator_list[0]) == "c"

    def test_multiple_decorators(self, tmp_path: Path) -> None:
        src = "@classmethod\n@lru_cache(maxsize=None)\ndef foo(cls):\n    pass\n"
        r = _parse(tmp_path, "m.py", src)
        assert r.symbols[0].decorators == ["classmethod", "lru_cache"]


# ---------------------------------------------------------------------------
# Import extraction
# ---------------------------------------------------------------------------


class TestImportExtraction:
    def test_simple_import(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "m.py", "import os\n")
        assert "import os" in r.imports

    def test_multi_import(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "m.py", "import sys, pathlib\n")
        assert "import sys" in r.imports
        assert "import pathlib" in r.imports

    def test_from_import_single(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "m.py", "from pathlib import Path\n")
        assert "from pathlib import Path" in r.imports

    def test_from_import_multiple_names(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "m.py", "from pathlib import Path, PurePath\n")
        assert "from pathlib import Path, PurePath" in r.imports

    def test_relative_import(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "m.py", "from . import sibling\n")
        assert "from . import sibling" in r.imports

    def test_relative_import_with_module(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "m.py", "from ..utils import helper\n")
        assert "from ..utils import helper" in r.imports

    def test_import_inside_function_included(self, tmp_path: Path) -> None:
        src = "def foo():\n    import json\n    return json.loads('{}') \n"
        r = _parse(tmp_path, "m.py", src)
        assert "import json" in r.imports

    def test_import_inside_if_type_checking(self, tmp_path: Path) -> None:
        src = (
            "from __future__ import annotations\n"
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from pathlib import Path\n"
        )
        r = _parse(tmp_path, "m.py", src)
        assert any("from pathlib import Path" in imp for imp in r.imports)

    def test_no_duplicate_imports(self, tmp_path: Path) -> None:
        src = "import os\nimport sys\nfrom pathlib import Path\n"
        r = _parse(tmp_path, "m.py", src)
        assert len(r.imports) == len(set(r.imports))

    def test_typing_imports(self, tmp_path: Path) -> None:
        src = "from typing import Optional, List, Dict\n"
        r = _parse(tmp_path, "m.py", src)
        assert "from typing import Optional, List, Dict" in r.imports


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_syntax_error_captured(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "bad.py", "def foo(:\n    pass\n")
        assert len(r.errors) == 1
        assert "SyntaxError" in r.errors[0]
        assert r.symbols == []
        assert r.imports == []

    def test_syntax_error_includes_line_number(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "bad.py", "x = 1\ndef foo(:\n    pass\n")
        assert len(r.errors) == 1
        assert "line" in r.errors[0].lower()

    def test_syntax_error_does_not_raise(self, tmp_path: Path) -> None:
        # Must return ParseResult, not raise
        result = _parse(tmp_path, "bad.py", "class Foo\n    pass\n")
        assert isinstance(result, ParseResult)

    def test_unmatched_parenthesis_error(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "bad.py", "x = (\n")
        assert len(r.errors) == 1

    def test_os_error_missing_file(self, tmp_path: Path) -> None:
        ghost = FileInfo(
            path=tmp_path / "ghost.py",
            relative_path="ghost.py",
            extension=".py",
            language=Language.PYTHON,
            size=0,
            modified_at=0.0,
            content_hash="",
        )
        r = PythonParser().parse(ghost)
        assert len(r.errors) == 1
        assert "OSError" in r.errors[0] or "error" in r.errors[0].lower()
        assert r.symbols == []

    def test_latin1_file_parsed_without_error(self, tmp_path: Path) -> None:
        # A Python file saved with latin-1 encoding (Windows legacy)
        src = "# -*- coding: latin-1 -*-\nname = 'Ren\xe9'\n"
        r = _parse(tmp_path, "latin.py", src, encoding="latin-1")
        assert r.errors == []

    def test_binary_content_produces_error_not_crash(self, tmp_path: Path) -> None:
        # Write random bytes that are not valid in either UTF-8 or latin-1 scope
        # latin-1 can decode any byte 0-255, so this will succeed for latin-1.
        # To actually trigger an error we need to make the file unreadable.
        fi = _fi_bytes(tmp_path, "binary.py", b"\xff\xfe\x00\x00bad content")
        r = PythonParser().parse(fi)
        # Either parsed (latin-1 fallback) or error — must not raise
        assert isinstance(r, ParseResult)

    def test_read_only_file_parsed(self, tmp_path: Path) -> None:
        path = tmp_path / "readonly.py"
        path.write_text("X = 1\n", encoding="utf-8")
        os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        try:
            fi = FileInfo(path=path, relative_path="readonly.py", extension=".py",
                          language=Language.PYTHON, size=path.stat().st_size,
                          modified_at=path.stat().st_mtime, content_hash="")
            r = PythonParser().parse(fi)
            assert r.errors == []
            assert r.symbols[0].name == "X"
        finally:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)

    def test_malformed_file_error_has_file_info(self, tmp_path: Path) -> None:
        fi = _fi(tmp_path, "bad.py", "invalid syntax (((")
        r = PythonParser().parse(fi)
        assert r.file_info is fi


# ---------------------------------------------------------------------------
# Qualified name and parent tracking
# ---------------------------------------------------------------------------


class TestQualifiedNames:
    def test_top_level_class_no_parent(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "m.py", "class Foo:\n    pass\n")
        assert _syms(r)["Foo"].parent is None

    def test_nested_class_parent(self, tmp_path: Path) -> None:
        src = "class A:\n    class B:\n        pass\n"
        r = _parse(tmp_path, "m.py", src)
        assert _syms(r)["A.B"].parent == "A"

    def test_method_parent_is_class_qn(self, tmp_path: Path) -> None:
        src = "class Foo:\n    def bar(self):\n        pass\n"
        r = _parse(tmp_path, "m.py", src)
        assert _syms(r)["Foo.bar"].parent == "Foo"

    def test_nested_function_parent(self, tmp_path: Path) -> None:
        src = "def outer():\n    def inner():\n        pass\n"
        r = _parse(tmp_path, "m.py", src)
        assert _syms(r)["outer.inner"].parent == "outer"

    def test_deeply_nested_qualified_name(self, tmp_path: Path) -> None:
        src = (
            "class A:\n"
            "    class B:\n"
            "        def method(self):\n"
            "            def nested():\n"
            "                pass\n"
        )
        r = _parse(tmp_path, "m.py", src)
        by_qn = _syms(r)
        assert "A.B.method.nested" in by_qn
        assert by_qn["A.B.method.nested"].parent == "A.B.method"

    def test_class_variable_qualified_name(self, tmp_path: Path) -> None:
        src = "class Cfg:\n    DEBUG = True\n"
        r = _parse(tmp_path, "m.py", src)
        assert "Cfg.DEBUG" in _syms(r)

    def test_module_variable_no_parent(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "m.py", "X = 1\n")
        assert r.symbols[0].parent is None


# ---------------------------------------------------------------------------
# Rich real-world source fixture
# ---------------------------------------------------------------------------


_RICH_SOURCE = '''\
"""Module docstring."""
import os
import sys
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Dict

MAX_RETRIES: int = 3
_PRIVATE_CONST = "hidden"
module_var = "public"

class Base:
    """Base class."""
    class_var: int = 0
    UPPER: str = "UP"

    def __init__(self, x: int) -> None:
        """Initialise."""
        self.x = x

    @classmethod
    def create(cls) -> "Base":
        """Factory."""
        return cls(0)

    @staticmethod
    def helper() -> None:
        """Helper."""
        pass

    @property
    def value(self) -> int:
        """Value property."""
        return self.class_var

    async def async_method(self) -> None:
        """Async."""
        pass

    class Nested:
        """Nested class."""
        def nested_fn(self) -> None:
            pass

class Child(Base):
    """Child."""

    def __init__(self, name: str) -> None:
        super().__init__(0)
        self.name = name

def top_func(a: int, b: str = "x") -> bool:
    """Top-level function."""
    def inner() -> None:
        pass
    return True

async def async_top() -> None:
    """Async top-level."""
    pass
'''


class TestRichSource:
    def test_no_errors(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "rich.py", _RICH_SOURCE)
        assert r.errors == []

    def test_classes_extracted(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "rich.py", _RICH_SOURCE)
        by_qn = _syms(r)
        assert "Base" in by_qn
        assert "Child" in by_qn
        assert "Base.Nested" in by_qn

    def test_methods_extracted(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "rich.py", _RICH_SOURCE)
        by_qn = _syms(r)
        assert "Base.__init__" in by_qn
        assert "Base.create" in by_qn
        assert "Base.helper" in by_qn
        assert "Base.value" in by_qn
        assert "Base.async_method" in by_qn
        assert "Base.Nested.nested_fn" in by_qn

    def test_top_level_functions_extracted(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "rich.py", _RICH_SOURCE)
        by_qn = _syms(r)
        assert "top_func" in by_qn
        assert "async_top" in by_qn
        assert by_qn["async_top"].is_async is True

    def test_nested_function_extracted(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "rich.py", _RICH_SOURCE)
        assert "top_func.inner" in _syms(r)

    def test_module_constants_extracted(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "rich.py", _RICH_SOURCE)
        by_qn = _syms(r)
        assert by_qn["MAX_RETRIES"].kind is SymbolKind.CONSTANT
        assert by_qn["_PRIVATE_CONST"].kind is SymbolKind.CONSTANT

    def test_module_variable_extracted(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "rich.py", _RICH_SOURCE)
        assert _syms(r)["module_var"].kind is SymbolKind.VARIABLE

    def test_class_variables_extracted(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "rich.py", _RICH_SOURCE)
        by_qn = _syms(r)
        assert "Base.class_var" in by_qn
        assert "Base.UPPER" in by_qn
        assert by_qn["Base.UPPER"].kind is SymbolKind.CONSTANT

    def test_imports_extracted(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "rich.py", _RICH_SOURCE)
        assert "import os" in r.imports
        assert "import sys" in r.imports
        assert any("pathlib" in imp for imp in r.imports)
        # TYPE_CHECKING import captured too
        assert any("Dict" in imp for imp in r.imports)

    def test_docstrings_extracted(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "rich.py", _RICH_SOURCE)
        by_qn = _syms(r)
        assert by_qn["Base"].docstring == "Base class."
        assert by_qn["Base.__init__"].docstring == "Initialise."
        assert by_qn["Base.create"].docstring == "Factory."

    def test_async_method(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "rich.py", _RICH_SOURCE)
        assert _syms(r)["Base.async_method"].is_async is True

    def test_decorators_on_methods(self, tmp_path: Path) -> None:
        r = _parse(tmp_path, "rich.py", _RICH_SOURCE)
        by_qn = _syms(r)
        assert "classmethod" in by_qn["Base.create"].decorators
        assert "staticmethod" in by_qn["Base.helper"].decorators
        assert "property" in by_qn["Base.value"].decorators


# ---------------------------------------------------------------------------
# Integration — ParserRegistry.default()
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_default_registry_supports_python(self) -> None:
        registry = ParserRegistry.default()
        assert registry.supports(Language.PYTHON)

    def test_default_registry_parse_returns_result(self, tmp_path: Path) -> None:
        registry = ParserRegistry.default()
        fi = _fi(tmp_path, "m.py", "class Foo:\n    pass\n")
        result = registry.parse(fi)
        assert result is not None
        assert any(s.kind is SymbolKind.CLASS for s in result.symbols)

    def test_parse_many_python_files(self, tmp_path: Path) -> None:
        registry = ParserRegistry.default()
        files = []
        for i in range(5):
            fi = _fi(tmp_path, f"m{i}.py", f"class C{i}:\n    pass\n")
            files.append(fi)
        results = registry.parse_many(files)
        assert len(results) == 5
        assert all(r.language is Language.PYTHON for r in results)

    def test_parse_pearl_own_source(self) -> None:
        """Parse Pearl's own Python source files — must produce no errors."""
        src_dir = Path(__file__).parent.parent.parent / "src"
        parser = PythonParser()
        failures: list[str] = []

        for py_file in sorted(src_dir.rglob("*.py")):
            fi = FileInfo(
                path=py_file,
                relative_path=str(py_file.relative_to(src_dir.parent)),
                extension=".py",
                language=Language.PYTHON,
                size=py_file.stat().st_size,
                modified_at=py_file.stat().st_mtime,
                content_hash="",
            )
            result = parser.parse(fi)
            if result.errors:
                failures.append(f"{py_file.name}: {result.errors}")

        assert not failures, "Parse errors in Pearl's own source:\n" + "\n".join(failures)


# ---------------------------------------------------------------------------
# Layer rule
# ---------------------------------------------------------------------------


class TestLayerRule:
    def test_python_parser_imports_only_allowed_modules(self) -> None:
        src = Path(__file__).parent.parent.parent / "src" / "repository" / "parsers" / "python_parser.py"
        tree = ast.parse(src.read_text(encoding="utf-8"))
        src_imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("src."):
                    src_imports.add(node.module)
        forbidden = {"src.agent", "src.tools", "src.mcp", "src.llm", "src.prompts"}
        unexpected = {imp for imp in src_imports if any(imp.startswith(f) for f in forbidden)}
        assert not unexpected, f"python_parser.py has forbidden imports: {unexpected}"

    def test_only_stdlib_and_repository_imports(self) -> None:
        src = Path(__file__).parent.parent.parent / "src" / "repository" / "parsers" / "python_parser.py"
        tree = ast.parse(src.read_text(encoding="utf-8"))
        allowed_src = {"src.repository.models", "src.repository.parsers"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("src."):
                    assert node.module in allowed_src, (
                        f"python_parser.py imports from {node.module!r} — only {allowed_src} are allowed"
                    )


# ---------------------------------------------------------------------------
# Performance benchmarks
# ---------------------------------------------------------------------------


class TestPythonParserBenchmarks:
    def _large_source(self, num_classes: int = 20, methods_per_class: int = 10) -> str:
        """Generate a realistic large Python file."""
        lines = [
            "\"\"\"Auto-generated large Python file for benchmark.\"\"\"",
            "import os",
            "import sys",
            "from pathlib import Path",
            "from typing import Optional, List",
            "",
            "GLOBAL_CONST = 42",
            "global_var = 'value'",
            "",
        ]
        for i in range(num_classes):
            lines += [
                f"class Class{i}:",
                f'    """Class {i} docstring."""',
                f"    CLASS_CONST = {i}",
                "    class_var: int = 0",
                "",
            ]
            for j in range(methods_per_class):
                async_kw = "async " if j % 3 == 0 else ""
                dec = "@classmethod\n    " if j % 5 == 0 else ""
                lines += [
                    f"    {dec}{async_kw}def method_{j}(self) -> None:",
                    f'        """Method {j} of Class {i}."""',
                    "        pass",
                    "",
                ]
        for k in range(10):
            lines += [
                f"def top_func_{k}(x: int, y: str = 'default') -> bool:",
                f'    """Top function {k}."""',
                f"    def inner_{k}():",
                "        pass",
                "    return True",
                "",
            ]
        return "\n".join(lines)

    def test_single_large_file_under_500ms(self, tmp_path: Path) -> None:
        """Parsing a ~600-line Python file must complete in < 500 ms."""
        src = self._large_source(num_classes=20, methods_per_class=10)
        fi = _fi(tmp_path, "large.py", src)
        parser = PythonParser()

        start = time.monotonic()
        result = parser.parse(fi)
        elapsed_ms = (time.monotonic() - start) * 1000

        assert result.errors == []
        assert elapsed_ms < 500, f"Single file parse: {elapsed_ms:.1f} ms (limit: 500 ms)"
        print(f"\n    Large file benchmark: {len(result.symbols)} symbols in {elapsed_ms:.1f} ms")

    def test_parse_50_files_under_5000ms(self, tmp_path: Path) -> None:
        """Parsing 50 Python files must complete in < 5 000 ms."""
        src = self._large_source(num_classes=5, methods_per_class=5)
        files = [_fi(tmp_path, f"file_{i}.py", src) for i in range(50)]
        parser = PythonParser()

        start = time.monotonic()
        results = [parser.parse(fi) for fi in files]
        elapsed_ms = (time.monotonic() - start) * 1000

        assert all(r.errors == [] for r in results)
        assert elapsed_ms < 5000, f"50-file parse: {elapsed_ms:.1f} ms (limit: 5 000 ms)"
        print(f"\n    50-file benchmark: {elapsed_ms:.1f} ms ({elapsed_ms/50:.1f} ms/file)")

    def test_symbol_count_reasonable(self, tmp_path: Path) -> None:
        """Symbol extraction must be complete — no silent truncation."""
        src = self._large_source(num_classes=5, methods_per_class=4)
        r = _parse(tmp_path, "m.py", src)
        assert r.errors == []
        # 5 classes × (1 class + 2 vars + 4 methods) + 10 top funcs + 10 inner + 2 module vars/consts
        assert len(r.symbols) > 50
