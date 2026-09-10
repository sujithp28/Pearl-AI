"""
Swift and Kotlin parsers — regex-based, no native dependencies.

Grouped because both are modern brace-delimited languages with the same
declaration shape (`keyword Name`, `fun`/`func name(...)`) and differ
only in vocabulary. Each contributes its own patterns; nothing is shared
by guessing.
"""

from __future__ import annotations

import re
from typing import ClassVar, Pattern

from src.repository.models import FileInfo, Language
from src.repository.parsers import BaseParser, ParseResult, SymbolDef, SymbolKind
from src.repository.parsers._block import estimate_brace_block_end

# ── Swift ────────────────────────────────────────────────────────────────────

_SWIFT_TYPE_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:(?:public|private|internal|fileprivate|open|"
    r"final|static)\s+)*(?P<kind>class|struct|enum|protocol|extension|actor)"
    r"\s+(?P<name>\w+)",
    re.MULTILINE,
)
_SWIFT_FUNC_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:(?:public|private|internal|fileprivate|open|"
    r"static|final|override|mutating|class|convenience|required)\s+)*"
    r"(?:func\s+(?P<name>\w+)|(?P<init>init))\s*"
    r"(?:<[^>]*>)?\((?P<params>[^)]*)\)",
    re.MULTILINE,
)
_SWIFT_PROP_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:(?:public|private|internal|fileprivate|open|"
    r"static|final|lazy)\s+)*(?P<kind>let|var)\s+(?P<name>\w+)\s*[:=]",
    re.MULTILINE,
)
_SWIFT_IMPORT_RE = re.compile(r"^\s*import\s+(\w+)", re.MULTILINE)

# ── Kotlin ───────────────────────────────────────────────────────────────────

_KOTLIN_TYPE_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:(?:public|private|internal|protected|open|"
    r"abstract|sealed|final|data|inner|enum|annotation|value)\s+)*"
    r"(?P<kind>class|interface|object)\s+(?P<name>\w+)",
    re.MULTILINE,
)
_KOTLIN_FUNC_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:(?:public|private|internal|protected|open|"
    r"override|abstract|final|suspend|inline|operator|tailrec|external)\s+)*"
    r"fun\s+(?:<[^>]*>\s*)?(?:[\w.]+\.)?(?P<name>\w+)\s*\((?P<params>[^)]*)\)",
    re.MULTILINE,
)
_KOTLIN_PROP_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:(?:public|private|internal|protected|open|"
    r"override|const|lateinit)\s+)*(?P<kind>val|var)\s+(?P<name>\w+)\s*[:=]",
    re.MULTILINE,
)
_KOTLIN_IMPORT_RE = re.compile(r"^\s*import\s+([\w.*]+)", re.MULTILINE)


class _BraceLangParser(BaseParser):
    """Shared traversal for Swift and Kotlin."""

    _LANGUAGE: ClassVar[Language]
    _EXTENSIONS: ClassVar[frozenset[str]]
    _TYPE_RE: ClassVar[Pattern[str]]
    _FUNC_RE: ClassVar[Pattern[str]]
    _PROP_RE: ClassVar[Pattern[str]]
    _IMPORT_RE: ClassVar[Pattern[str]]

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

        for match in self._TYPE_RE.finditer(source):
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

        for match in self._FUNC_RE.finditer(source):
            groups = match.groupdict()
            name = groups.get("name") or groups.get("init")
            if not name:
                continue

            line_no = source[: match.start()].count("\n") + 1
            parent = _innermost(containers, line_no)
            symbols.append(
                SymbolDef(
                    name=name,
                    qualified_name=f"{parent}.{name}" if parent else name,
                    kind=SymbolKind.METHOD if parent else SymbolKind.FUNCTION,
                    line_start=line_no,
                    line_end=estimate_brace_block_end(lines, line_no - 1),
                    parent=parent,
                    signature=f"({groups.get('params', '').strip()})",
                )
            )

        # Only type-level properties: a local `let x = 1` inside a function
        # body is not a symbol anyone searches a repository for.
        for match in self._PROP_RE.finditer(source):
            line_no = source[: match.start()].count("\n") + 1
            parent = _innermost(containers, line_no)
            if parent is None:
                continue
            name = match.group("name")
            symbols.append(
                SymbolDef(
                    name=name,
                    qualified_name=f"{parent}.{name}",
                    kind=SymbolKind.VARIABLE,
                    line_start=line_no,
                    line_end=line_no,
                    parent=parent,
                )
            )

        symbols.sort(key=lambda s: s.line_start)
        return ParseResult(
            file_info=file_info,
            symbols=symbols,
            imports=[f"import {m.group(1)}" for m in self._IMPORT_RE.finditer(source)],
        )


def _innermost(containers: list[tuple[int, int, str]], line_no: int) -> str | None:
    best: tuple[int, str] | None = None
    for start, end, name in containers:
        if start < line_no <= end and (best is None or start > best[0]):
            best = (start, name)
    return best[1] if best else None


class SwiftParser(_BraceLangParser):
    _LANGUAGE = Language.SWIFT
    _EXTENSIONS = frozenset({".swift"})
    _TYPE_RE = _SWIFT_TYPE_RE
    _FUNC_RE = _SWIFT_FUNC_RE
    _PROP_RE = _SWIFT_PROP_RE
    _IMPORT_RE = _SWIFT_IMPORT_RE


class KotlinParser(_BraceLangParser):
    _LANGUAGE = Language.KOTLIN
    _EXTENSIONS = frozenset({".kt", ".kts"})
    _TYPE_RE = _KOTLIN_TYPE_RE
    _FUNC_RE = _KOTLIN_FUNC_RE
    _PROP_RE = _KOTLIN_PROP_RE
    _IMPORT_RE = _KOTLIN_IMPORT_RE
