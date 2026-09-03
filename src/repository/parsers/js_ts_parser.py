"""
JavaScript / TypeScript parser — regex-based, no native dependencies.

Extracts classes, functions (declarations + arrow assignments), and
import statements.  tree-sitter would give better accuracy, but regex
gracefully handles minified or syntactically unusual files that would
crash an AST parser.
"""
from __future__ import annotations

import re
from typing import ClassVar

from src.repository.models import FileInfo, Language
from src.repository.parsers import BaseParser, ParseResult, SymbolDef, SymbolKind

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

_CLASS_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:export\s+(?:default\s+)?)?(?:abstract\s+)?class\s+(?P<name>\w+)",
    re.MULTILINE,
)
_FUNC_DECL_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s*\*?\s*(?P<name>\w+)\s*\(",
    re.MULTILINE,
)
_ARROW_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:export\s+)?(?:const|let|var)\s+(?P<name>\w+)\s*=\s*(?:async\s+)?"
    r"(?:\([^)]*\)[^=\n]*|\w+)\s*=>",
    re.MULTILINE,
)
_METHOD_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:(?:public|private|protected|static|async|override)\s+)*"
    r"(?:async\s+)?(?P<name>\w+)\s*\(",
    re.MULTILINE,
)
_IMPORT_RE = re.compile(
    r"^(?:import\s.*?from\s+['\"][^'\"]+['\"]|import\s+['\"][^'\"]+['\"]|require\(['\"][^'\"]+['\"]\))",
    re.MULTILINE,
)
_EXPORT_RE = re.compile(r"^export\s+(?:default\s+)?(?:const|let|var|function|class)\s+(\w+)", re.MULTILINE)

_BUILTIN_NAMES = frozenset({"if", "else", "for", "while", "switch", "try", "catch", "return", "constructor"})


class JsTsParser(BaseParser):
    """Regex-based parser for JavaScript and TypeScript."""

    _JS_EXTENSIONS: ClassVar[frozenset[str]] = frozenset({".js", ".mjs", ".cjs", ".jsx"})
    _TS_EXTENSIONS: ClassVar[frozenset[str]] = frozenset({".ts", ".tsx", ".mts", ".cts"})

    def __init__(self, for_typescript: bool = False) -> None:
        self._for_typescript = for_typescript

    @property
    def language(self) -> Language:
        return Language.TYPESCRIPT if self._for_typescript else Language.JAVASCRIPT

    @property
    def supported_extensions(self) -> frozenset[str]:
        return self._TS_EXTENSIONS if self._for_typescript else self._JS_EXTENSIONS

    def parse(self, file_info: FileInfo) -> ParseResult:
        symbols: list[SymbolDef] = []
        imports: list[str] = []
        errors: list[str] = []

        try:
            source = file_info.path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return ParseResult(file_info=file_info, errors=[f"Read error: {exc}"])

        lines = source.splitlines()

        imports = [m.group(0).strip() for m in _IMPORT_RE.finditer(source)]

        current_class: str | None = None
        current_class_indent: int = -1

        for match in _CLASS_RE.finditer(source):
            name = match.group("name")
            line_no = source[: match.start()].count("\n") + 1
            indent = len(match.group("indent"))
            qname = name
            symbols.append(SymbolDef(
                name=name,
                qualified_name=qname,
                kind=SymbolKind.CLASS,
                line_start=line_no,
                line_end=_estimate_end(lines, line_no - 1),
            ))

        for match in _FUNC_DECL_RE.finditer(source):
            name = match.group("name")
            if name in _BUILTIN_NAMES:
                continue
            line_no = source[: match.start()].count("\n") + 1
            is_async = "async" in match.group(0)
            symbols.append(SymbolDef(
                name=name,
                qualified_name=name,
                kind=SymbolKind.FUNCTION,
                line_start=line_no,
                line_end=_estimate_end(lines, line_no - 1),
                is_async=is_async,
            ))

        for match in _ARROW_RE.finditer(source):
            name = match.group("name")
            if name in _BUILTIN_NAMES:
                continue
            line_no = source[: match.start()].count("\n") + 1
            is_async = "async" in match.group(0)
            symbols.append(SymbolDef(
                name=name,
                qualified_name=name,
                kind=SymbolKind.FUNCTION,
                line_start=line_no,
                line_end=line_no,
                is_async=is_async,
            ))

        symbols.sort(key=lambda s: s.line_start)
        return ParseResult(file_info=file_info, symbols=symbols, imports=imports, errors=errors)


def _estimate_end(lines: list[str], start_idx: int, max_scan: int = 200) -> int:
    """
    Estimate the last line of a block starting at `start_idx` by
    scanning for a closing brace at the same indent level.
    """
    if start_idx >= len(lines):
        return start_idx + 1
    base_indent = len(lines[start_idx]) - len(lines[start_idx].lstrip())
    depth = 0
    for i in range(start_idx, min(start_idx + max_scan, len(lines))):
        depth += lines[i].count("{") - lines[i].count("}")
        if i > start_idx and depth <= 0:
            return i + 1
    return min(start_idx + max_scan, len(lines))


class JavaScriptParser(JsTsParser):
    def __init__(self) -> None:
        super().__init__(for_typescript=False)


class TypeScriptParser(JsTsParser):
    def __init__(self) -> None:
        super().__init__(for_typescript=True)
