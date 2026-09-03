"""
Rust parser — regex-based, no native dependencies.

Extracts functions, structs, enums, traits, and impl blocks.
"""
from __future__ import annotations

import re

from src.repository.models import FileInfo, Language
from src.repository.parsers import BaseParser, ParseResult, SymbolDef, SymbolKind

_FN_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+(?P<name>\w+)\s*(?:<[^>]*>)?\s*\(",
    re.MULTILINE,
)
_STRUCT_RE = re.compile(
    r"^(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait|union)\s+(?P<name>\w+)",
    re.MULTILINE,
)
_IMPL_RE = re.compile(
    r"^impl(?:<[^>]*>)?\s+(?:(?P<trait>\w+)\s+for\s+)?(?P<type>\w+)",
    re.MULTILINE,
)
_USE_RE = re.compile(r"^use\s+([^;]+);", re.MULTILINE)


class RustParser(BaseParser):
    """Regex-based parser for Rust."""

    @property
    def language(self) -> Language:
        return Language.RUST

    @property
    def supported_extensions(self) -> frozenset[str]:
        return frozenset({".rs"})

    def parse(self, file_info: FileInfo) -> ParseResult:
        symbols: list[SymbolDef] = []
        imports: list[str] = []
        errors: list[str] = []

        try:
            source = file_info.path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return ParseResult(file_info=file_info, errors=[f"Read error: {exc}"])

        lines = source.splitlines()

        imports = [f"use {m.group(1)};" for m in _USE_RE.finditer(source)]

        for m in _STRUCT_RE.finditer(source):
            name = m.group("name")
            line_no = source[: m.start()].count("\n") + 1
            raw = m.group(0)
            if "struct" in raw or "union" in raw:
                kind = SymbolKind.CLASS
            elif "enum" in raw:
                kind = SymbolKind.CONSTANT
            else:
                kind = SymbolKind.UNKNOWN
            symbols.append(SymbolDef(
                name=name,
                qualified_name=name,
                kind=kind,
                line_start=line_no,
                line_end=_estimate_end(lines, line_no - 1),
            ))

        # Track current impl context for method qualification
        impl_ranges: list[tuple[int, int, str]] = []
        for m in _IMPL_RE.finditer(source):
            type_name = m.group("type")
            line_no = source[: m.start()].count("\n") + 1
            end_line = _estimate_end(lines, line_no - 1)
            impl_ranges.append((line_no, end_line, type_name))

        for m in _FN_RE.finditer(source):
            name = m.group("name")
            indent = len(m.group("indent"))
            line_no = source[: m.start()].count("\n") + 1
            is_async = "async" in m.group(0)

            # Determine parent from impl context
            parent = None
            for (impl_start, impl_end, impl_type) in impl_ranges:
                if impl_start <= line_no <= impl_end and indent > 0:
                    parent = impl_type
                    break

            kind = SymbolKind.METHOD if parent else SymbolKind.FUNCTION
            qname = f"{parent}.{name}" if parent else name

            symbols.append(SymbolDef(
                name=name,
                qualified_name=qname,
                kind=kind,
                line_start=line_no,
                line_end=_estimate_end(lines, line_no - 1),
                is_async=is_async,
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
