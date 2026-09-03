"""
Go parser — regex-based, no native dependencies.

Extracts functions, methods, and struct/interface types.
Import blocks are parsed as a single grouped import statement.
"""
from __future__ import annotations

import re

from src.repository.models import FileInfo, Language
from src.repository.parsers import BaseParser, ParseResult, SymbolDef, SymbolKind

_FUNC_RE = re.compile(
    r"^func\s+(?:\((?P<recv>[^)]+)\)\s+)?(?P<name>\w+)\s*\(",
    re.MULTILINE,
)
_TYPE_RE = re.compile(
    r"^type\s+(?P<name>\w+)\s+(?P<kind>struct|interface)\b",
    re.MULTILINE,
)
_IMPORT_SINGLE_RE = re.compile(r'^import\s+"([^"]+)"', re.MULTILINE)
_IMPORT_BLOCK_RE = re.compile(r'"([^"]+)"', re.MULTILINE)
_IMPORT_BLOCK_START_RE = re.compile(r"^import\s*\(", re.MULTILINE)


class GoParser(BaseParser):
    """Regex-based parser for Go."""

    @property
    def language(self) -> Language:
        return Language.GO

    @property
    def supported_extensions(self) -> frozenset[str]:
        return frozenset({".go"})

    def parse(self, file_info: FileInfo) -> ParseResult:
        symbols: list[SymbolDef] = []
        imports: list[str] = []
        errors: list[str] = []

        try:
            source = file_info.path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return ParseResult(file_info=file_info, errors=[f"Read error: {exc}"])

        lines = source.splitlines()

        # Imports
        for m in _IMPORT_SINGLE_RE.finditer(source):
            imports.append(f'import "{m.group(1)}"')
        for m in _IMPORT_BLOCK_START_RE.finditer(source):
            # Find the matching close paren
            block_start = m.end()
            close = source.find(")", block_start)
            if close != -1:
                block = source[block_start:close]
                for pkg in _IMPORT_BLOCK_RE.findall(block):
                    imports.append(f'import "{pkg}"')

        # Structs and interfaces
        for m in _TYPE_RE.finditer(source):
            name = m.group("name")
            raw_kind = m.group("kind")
            kind = SymbolKind.CLASS if raw_kind == "struct" else SymbolKind.UNKNOWN
            line_no = source[: m.start()].count("\n") + 1
            symbols.append(SymbolDef(
                name=name,
                qualified_name=name,
                kind=kind,
                line_start=line_no,
                line_end=_estimate_end(lines, line_no - 1),
            ))

        # Functions and methods
        for m in _FUNC_RE.finditer(source):
            name = m.group("name")
            recv = m.group("recv")
            line_no = source[: m.start()].count("\n") + 1
            if recv:
                # Method — receiver type is the parent
                recv_type = recv.strip().split()[-1].lstrip("*")
                qname = f"{recv_type}.{name}"
                kind = SymbolKind.METHOD
                parent = recv_type
            else:
                qname = name
                kind = SymbolKind.FUNCTION
                parent = None
            symbols.append(SymbolDef(
                name=name,
                qualified_name=qname,
                kind=kind,
                line_start=line_no,
                line_end=_estimate_end(lines, line_no - 1),
                parent=parent,
            ))

        symbols.sort(key=lambda s: s.line_start)
        return ParseResult(file_info=file_info, symbols=symbols, imports=imports, errors=errors)


def _estimate_end(lines: list[str], start_idx: int, max_scan: int = 200) -> int:
    if start_idx >= len(lines):
        return start_idx + 1
    depth = 0
    for i in range(start_idx, min(start_idx + max_scan, len(lines))):
        depth += lines[i].count("{") - lines[i].count("}")
        if i > start_idx and depth <= 0:
            return i + 1
    return min(start_idx + max_scan, len(lines))
