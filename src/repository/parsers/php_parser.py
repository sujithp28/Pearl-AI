"""
PHP parser — regex-based, no native dependencies.

Extracts classes, interfaces, traits, enums, methods and free functions,
plus `use`/`require` imports. Visibility modifiers are optional in PHP,
so the method pattern must match a bare `function foo()` inside a class
as readily as `public static function foo()`.
"""

from __future__ import annotations

import re

from src.repository.models import FileInfo, Language
from src.repository.parsers import BaseParser, ParseResult, SymbolDef, SymbolKind
from src.repository.parsers._block import estimate_brace_block_end

_CLASS_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:(?:final|abstract|readonly)\s+)*"
    r"(?P<kind>class|interface|trait|enum)\s+(?P<name>\w+)",
    re.MULTILINE,
)
_FUNC_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:(?:public|private|protected|static|final|"
    r"abstract)\s+)*function\s+&?(?P<name>\w+)\s*\((?P<params>[^)]*)\)",
    re.MULTILINE,
)
_USE_RE = re.compile(r"^\s*use\s+([\w\\]+)(?:\s+as\s+\w+)?\s*;", re.MULTILINE)
_REQUIRE_RE = re.compile(
    r"^\s*(?:require|include)(?:_once)?\s*\(?\s*['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)


class PhpParser(BaseParser):
    """Regex-based parser for PHP."""

    @property
    def language(self) -> Language:
        return Language.PHP

    @property
    def supported_extensions(self) -> frozenset[str]:
        return frozenset({".php"})

    def parse(self, file_info: FileInfo) -> ParseResult:
        try:
            source = file_info.path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return ParseResult(file_info=file_info, errors=[f"Read error: {exc}"])

        lines = source.splitlines()
        symbols: list[SymbolDef] = []

        for match in _CLASS_RE.finditer(source):
            line_no = source[: match.start()].count("\n") + 1
            name = match.group("name")
            symbols.append(
                SymbolDef(
                    name=name,
                    qualified_name=name,
                    kind=SymbolKind.CLASS,
                    line_start=line_no,
                    line_end=estimate_brace_block_end(lines, line_no - 1),
                )
            )

        containers = [(s.line_start, s.line_end, s.name) for s in symbols]

        for match in _FUNC_RE.finditer(source):
            line_no = source[: match.start()].count("\n") + 1
            name = match.group("name")
            parent = next((c for s, e, c in containers if s < line_no <= e), None)
            symbols.append(
                SymbolDef(
                    name=name,
                    qualified_name=f"{parent}::{name}" if parent else name,
                    kind=SymbolKind.METHOD if parent else SymbolKind.FUNCTION,
                    line_start=line_no,
                    line_end=estimate_brace_block_end(lines, line_no - 1),
                    parent=parent,
                    signature=f"({match.group('params').strip()})",
                )
            )

        imports = [f"use {m.group(1)};" for m in _USE_RE.finditer(source)]
        imports += [f"require '{m.group(1)}'" for m in _REQUIRE_RE.finditer(source)]

        symbols.sort(key=lambda s: s.line_start)
        return ParseResult(file_info=file_info, symbols=symbols, imports=imports)
