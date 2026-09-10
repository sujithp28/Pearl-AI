"""
Ruby parser — regex-based, no native dependencies.

Ruby is the one supported language that is not brace-delimited: blocks
close with `end`, so nesting has to be tracked by keyword rather than by
counting braces. Parent attribution follows the same lexical nesting,
which is what makes `Foo::bar` come out as a method of `Foo` rather than
a bare function.
"""

from __future__ import annotations

import re

from src.repository.models import FileInfo, Language
from src.repository.parsers import BaseParser, ParseResult, SymbolDef, SymbolKind
from src.repository.parsers._block import estimate_keyword_block_end

_CLASS_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<kind>class|module)\s+(?P<name>[A-Z][\w:]*)",
    re.MULTILINE,
)
_DEF_RE = re.compile(
    r"^(?P<indent>[ \t]*)def\s+(?:self\.)?(?P<name>[\w?!=\[\]<>+\-*/]+)"
    r"(?:\((?P<params>[^)]*)\))?",
    re.MULTILINE,
)
_REQUIRE_RE = re.compile(
    r"^\s*require(?:_relative)?\s+['\"]([^'\"]+)['\"]", re.MULTILINE
)
_ATTR_RE = re.compile(
    r"^(?P<indent>[ \t]*)attr_(?:accessor|reader|writer)\s+(?P<names>.+)$",
    re.MULTILINE,
)

# Keywords that open a block Ruby closes with `end`. Needed so a method
# containing an `if` does not end at that `if`'s `end`.
_BLOCK_OPENERS = (
    "class",
    "module",
    "def",
    "if",
    "unless",
    "while",
    "until",
    "case",
    "begin",
    "for",
    "do",
)


class RubyParser(BaseParser):
    """Regex-based parser for Ruby."""

    @property
    def language(self) -> Language:
        return Language.RUBY

    @property
    def supported_extensions(self) -> frozenset[str]:
        return frozenset({".rb", ".rake"})

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
                    kind=SymbolKind.CLASS
                    if match.group("kind") == "class"
                    else SymbolKind.MODULE,
                    line_start=line_no,
                    line_end=estimate_keyword_block_end(
                        lines, line_no - 1, _BLOCK_OPENERS
                    ),
                )
            )

        containers = [(s.line_start, s.line_end, s.name) for s in symbols]

        for match in _DEF_RE.finditer(source):
            line_no = source[: match.start()].count("\n") + 1
            name = match.group("name")

            parent = None
            for start, end, cname in containers:
                if start < line_no <= end and (
                    parent is None or start > _start_of(containers, parent)
                ):
                    parent = cname

            params = match.group("params")
            symbols.append(
                SymbolDef(
                    name=name,
                    qualified_name=f"{parent}#{name}" if parent else name,
                    kind=SymbolKind.METHOD if parent else SymbolKind.FUNCTION,
                    line_start=line_no,
                    line_end=estimate_keyword_block_end(
                        lines, line_no - 1, _BLOCK_OPENERS
                    ),
                    parent=parent,
                    signature=f"({params.strip()})" if params else None,
                )
            )

        # attr_accessor generates real methods; a reader searching for
        # `name` should find something on a class that declares it.
        for match in _ATTR_RE.finditer(source):
            line_no = source[: match.start()].count("\n") + 1
            parent = next((c for s, e, c in containers if s < line_no <= e), None)
            for raw in match.group("names").split(","):
                attr = raw.strip().lstrip(":").strip()
                if not attr or not attr.replace("_", "").isalnum():
                    continue
                symbols.append(
                    SymbolDef(
                        name=attr,
                        qualified_name=f"{parent}#{attr}" if parent else attr,
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
            imports=[f"require '{m.group(1)}'" for m in _REQUIRE_RE.finditer(source)],
        )


def _start_of(containers: list[tuple[int, int, str]], name: str) -> int:
    for start, _end, cname in containers:
        if cname == name:
            return start
    return -1
