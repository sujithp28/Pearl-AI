"""
C, C++ and C# parsers — regex-based, no native dependencies.

One module because the three share their declaration shapes closely
enough that separate files would be near-duplicates; where they differ
(C# properties and namespaces, C++ templates and `::` definitions) each
class contributes its own patterns rather than the base guessing.

The hard case for all three is telling a function definition from a call
or a control-flow statement, since `if (x) {` and `foo(x) {` are the same
shape. Keyword exclusion handles it: a "function" named `if` is not one.
"""
from __future__ import annotations

import re
from typing import ClassVar

from src.repository.models import FileInfo, Language
from src.repository.parsers import BaseParser, ParseResult, SymbolDef, SymbolKind
from src.repository.parsers._block import estimate_brace_block_end

# Control-flow keywords share their shape with function definitions.
# Without this every `if (…) {` is indexed as a function.
_KEYWORDS = frozenset(
    {
        "if", "else", "for", "while", "do", "switch", "case", "default",
        "try", "catch", "finally", "return", "throw", "new", "delete",
        "sizeof", "typedef", "using", "namespace", "template", "typename",
        "public", "private", "protected", "static", "const", "inline",
        "virtual", "explicit", "friend", "operator", "struct", "class",
        "enum", "union", "extern", "register", "volatile", "goto",
        "lock", "unsafe", "fixed", "checked", "unchecked", "foreach",
    }
)

_TYPE_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:(?:public|private|protected|internal|static|"
    r"abstract|sealed|partial|final|export)\s+)*"
    r"(?P<kind>class|struct|enum|union|interface|record)\s+(?P<name>\w+)",
    re.MULTILINE,
)

# A definition, not a declaration: requires an opening brace, so
# prototypes in headers are not reported as definitions.
_FUNC_RE = re.compile(
    r"^(?P<indent>[ \t]*)"
    r"(?:(?:public|private|protected|internal|static|virtual|override|"
    r"abstract|async|extern|inline|explicit|constexpr|friend|sealed|"
    r"unsafe|new|partial)\s+)*"
    r"(?:[\w:<>,\s\*&\[\]]+?[\s\*&]+)"          # return type
    r"(?P<name>~?\w+)\s*"                        # name (or C++ destructor)
    r"\((?P<params>[^;{)]*)\)\s*"
    r"(?:const\s*)?(?:noexcept\s*)?(?:override\s*)?(?:final\s*)?"
    r"(?::[^{;]+)?"                              # C++ member-init list
    r"\{",
    re.MULTILINE,
)

_CS_PROPERTY_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:(?:public|private|protected|internal|static|"
    r"virtual|override|abstract|readonly)\s+)+"
    r"[\w<>,\[\]\?]+\s+(?P<name>\w+)\s*\{\s*(?:get|set)",
    re.MULTILINE,
)

_INCLUDE_RE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.MULTILINE)
_CS_USING_RE = re.compile(r"^\s*using\s+(?:static\s+)?([\w.]+)\s*;", re.MULTILINE)


class _CFamilyParser(BaseParser):
    """Shared implementation for the brace-and-type C-family languages."""

    _LANGUAGE: ClassVar[Language]
    _EXTENSIONS: ClassVar[frozenset[str]]

    @property
    def language(self) -> Language:
        return self._LANGUAGE

    @property
    def supported_extensions(self) -> frozenset[str]:
        return self._EXTENSIONS

    def parse(self, file_info: FileInfo) -> ParseResult:
        try:
            source = file_info.path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return ParseResult(file_info=file_info, errors=[f"Read error: {exc}"])

        lines = source.splitlines()
        symbols: list[SymbolDef] = []

        for match in _TYPE_RE.finditer(source):
            line_no = source[: match.start()].count("\n") + 1
            raw_kind = match.group("kind")
            symbols.append(
                SymbolDef(
                    name=match.group("name"),
                    qualified_name=match.group("name"),
                    kind=(
                        SymbolKind.CONSTANT
                        if raw_kind == "enum"
                        else SymbolKind.CLASS
                    ),
                    line_start=line_no,
                    line_end=estimate_brace_block_end(lines, line_no - 1),
                )
            )

        # Types are indexed first, so a function can be attributed to the
        # type whose line range encloses it.
        type_ranges = [(s.line_start, s.line_end, s.name) for s in symbols]

        for match in _FUNC_RE.finditer(source):
            name = match.group("name")
            if name.lstrip("~") in _KEYWORDS:
                continue

            line_no = source[: match.start()].count("\n") + 1
            parent = _enclosing(type_ranges, line_no)

            symbols.append(
                SymbolDef(
                    name=name,
                    qualified_name=f"{parent}.{name}" if parent else name,
                    kind=SymbolKind.METHOD if parent else SymbolKind.FUNCTION,
                    line_start=line_no,
                    line_end=estimate_brace_block_end(lines, line_no - 1),
                    parent=parent,
                    signature=f"({match.group('params').strip()})",
                )
            )

        symbols.extend(self._extra_symbols(source, lines, type_ranges))

        symbols.sort(key=lambda s: s.line_start)
        return ParseResult(
            file_info=file_info,
            symbols=symbols,
            imports=self._imports(source),
        )

    # -- Hooks for the language-specific parts ---------------------------

    def _imports(self, source: str) -> list[str]:
        return [f"#include {m.group(1)}" for m in _INCLUDE_RE.finditer(source)]

    def _extra_symbols(
        self,
        source: str,
        lines: list[str],
        type_ranges: list[tuple[int, int, str]],
    ) -> list[SymbolDef]:
        return []


def _enclosing(
    ranges: list[tuple[int, int, str]], line_no: int
) -> str | None:
    """Return the innermost type whose range contains `line_no`."""
    best: tuple[int, str] | None = None
    for start, end, name in ranges:
        if start < line_no <= end:
            # Innermost wins: the latest start that still encloses.
            if best is None or start > best[0]:
                best = (start, name)
    return best[1] if best else None


class CParser(_CFamilyParser):
    _LANGUAGE = Language.C
    _EXTENSIONS = frozenset({".c", ".h"})


class CppParser(_CFamilyParser):
    _LANGUAGE = Language.CPP
    _EXTENSIONS = frozenset({".cpp", ".cc", ".cxx", ".hpp", ".hxx"})


class CSharpParser(_CFamilyParser):
    _LANGUAGE = Language.CSHARP
    _EXTENSIONS = frozenset({".cs"})

    def _imports(self, source: str) -> list[str]:
        return [f"using {m.group(1)};" for m in _CS_USING_RE.finditer(source)]

    def _extra_symbols(
        self,
        source: str,
        lines: list[str],
        type_ranges: list[tuple[int, int, str]],
    ) -> list[SymbolDef]:
        """C# properties — a first-class member with no C/C++ equivalent."""
        found: list[SymbolDef] = []
        for match in _CS_PROPERTY_RE.finditer(source):
            line_no = source[: match.start()].count("\n") + 1
            parent = _enclosing(type_ranges, line_no)
            name = match.group("name")
            found.append(
                SymbolDef(
                    name=name,
                    qualified_name=f"{parent}.{name}" if parent else name,
                    kind=SymbolKind.VARIABLE,
                    line_start=line_no,
                    line_end=estimate_brace_block_end(lines, line_no - 1),
                    parent=parent,
                )
            )
        return found
