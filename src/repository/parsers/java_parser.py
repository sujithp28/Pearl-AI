"""
Java parser — regex-based, no native dependencies.

Extracts classes, interfaces, enums, and methods.
"""
from __future__ import annotations

import re

from src.repository.models import FileInfo, Language
from src.repository.parsers import BaseParser, ParseResult, SymbolDef, SymbolKind

_CLASS_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:(?:public|private|protected|abstract|final|static)\s+)*"
    r"(?:class|interface|enum|record)\s+(?P<name>\w+)",
    re.MULTILINE,
)
_METHOD_RE = re.compile(
    r"^(?P<indent>[ \t]+)(?:(?:public|private|protected|static|final|abstract|"
    r"synchronized|native|strictfp|default)\s+)*"
    r"(?:<[^>]+>\s+)?(?:\w[\w.<>\[\]]*\s+)+(?P<name>\w+)\s*\([^)]*\)\s*"
    r"(?:throws\s+[\w,\s]+)?\s*\{",
    re.MULTILINE,
)
_IMPORT_RE = re.compile(r"^import\s+(?:static\s+)?([^;]+);", re.MULTILINE)

_KEYWORD_NAMES = frozenset({
    "if", "else", "for", "while", "do", "switch", "try", "catch", "finally",
    "return", "throw", "new", "this", "super",
})


class JavaParser(BaseParser):
    """Regex-based parser for Java."""

    @property
    def language(self) -> Language:
        return Language.JAVA

    @property
    def supported_extensions(self) -> frozenset[str]:
        return frozenset({".java"})

    def parse(self, file_info: FileInfo) -> ParseResult:
        symbols: list[SymbolDef] = []
        imports: list[str] = []
        errors: list[str] = []

        try:
            source = file_info.path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return ParseResult(file_info=file_info, errors=[f"Read error: {exc}"])

        lines = source.splitlines()

        imports = [f"import {m.group(1)};" for m in _IMPORT_RE.finditer(source)]

        # Track classes and their indent levels for method parent resolution
        class_stack: list[tuple[int, str, int]] = []  # (line_no, name, indent)

        for m in _CLASS_RE.finditer(source):
            name = m.group("name")
            indent = len(m.group("indent"))
            line_no = source[: m.start()].count("\n") + 1
            end_line = _estimate_end(lines, line_no - 1)

            raw = m.group(0)
            if "interface" in raw:
                kind = SymbolKind.UNKNOWN
            elif "enum" in raw:
                kind = SymbolKind.CONSTANT
            else:
                kind = SymbolKind.CLASS

            symbols.append(SymbolDef(
                name=name,
                qualified_name=name,
                kind=kind,
                line_start=line_no,
                line_end=end_line,
            ))
            class_stack.append((line_no, name, indent))

        for m in _METHOD_RE.finditer(source):
            name = m.group("name")
            if name in _KEYWORD_NAMES:
                continue
            indent = len(m.group("indent"))
            line_no = source[: m.start()].count("\n") + 1

            # Find enclosing class
            parent = None
            for (cls_line, cls_name, cls_indent) in reversed(class_stack):
                if cls_line < line_no and cls_indent < indent:
                    parent = cls_name
                    break

            qname = f"{parent}.{name}" if parent else name
            symbols.append(SymbolDef(
                name=name,
                qualified_name=qname,
                kind=SymbolKind.METHOD if parent else SymbolKind.FUNCTION,
                line_start=line_no,
                line_end=_estimate_end(lines, line_no - 1),
                parent=parent,
            ))

        symbols.sort(key=lambda s: s.line_start)
        return ParseResult(file_info=file_info, symbols=symbols, imports=imports, errors=errors)


def _estimate_end(lines: list[str], start_idx: int, max_scan: int = 300) -> int:
    if start_idx >= len(lines):
        return start_idx + 1
    depth = 0
    for i in range(start_idx, min(start_idx + max_scan, len(lines))):
        depth += lines[i].count("{") - lines[i].count("}")
        if i > start_idx and depth <= 0:
            return i + 1
    return min(start_idx + max_scan, len(lines))
