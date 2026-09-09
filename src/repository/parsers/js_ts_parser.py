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

        # (name, indent, first line, last line) for every class, so a
        # method match below can be attributed to the class it sits in.
        class_spans: list[tuple[str, int, int, int]] = []

        for match in _CLASS_RE.finditer(source):
            name = match.group("name")
            line_no = source[: match.start()].count("\n") + 1
            line_end = _estimate_end(lines, line_no - 1)
            symbols.append(SymbolDef(
                name=name,
                qualified_name=name,
                kind=SymbolKind.CLASS,
                line_start=line_no,
                line_end=line_end,
            ))
            class_spans.append(
                (name, len(match.group("indent")), line_no, line_end)
            )

        for match in _METHOD_RE.finditer(source):
            name = match.group("name")
            line_no = source[: match.start()].count("\n") + 1
            indent = len(match.group("indent"))

            owner = _enclosing_class(class_spans, line_no, indent)
            if owner is None:
                continue

            # `constructor` is in _BUILTIN_NAMES to stop it matching
            # outside a class; inside one it is a real symbol.
            if name in _BUILTIN_NAMES and name != "constructor":
                continue

            # _METHOD_RE is deliberately loose — `name(` also matches a
            # bare call statement like `doSomething(1);`. What separates
            # a signature from a call is that a signature opens a body.
            if not _opens_a_body(source, match.end()):
                continue

            symbols.append(SymbolDef(
                name=name,
                qualified_name=f"{owner}.{name}",
                kind=SymbolKind.METHOD,
                line_start=line_no,
                line_end=_estimate_end(lines, line_no - 1),
                is_async="async" in match.group(0),
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


def _opens_a_body(source: str, after_open_paren: int) -> bool:
    """
    Return whether the parameter list starting at `after_open_paren` is
    followed by `{` on the same line — i.e. this is a method signature
    rather than a call statement that happens to look like one.

    Walks to the parameter list's own matching `)` with depth counting,
    rather than taking the last `)` on the line, so a method whose body
    contains a call still resolves to the right paren:

        async findUser(id) { return this.db.get(id); }
                          ^ this one, not the one after `get(id`

    A signature whose parameters wrap across lines returns False and the
    method is missed. That is the standing trade-off of a regex parser
    that must never raise on input an AST parser would reject.
    """

    depth = 1
    i = after_open_paren

    while i < len(source) and depth:
        char = source[i]
        if char == "\n":
            return False
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        i += 1

    if depth:
        return False

    line_end = source.find("\n", i)
    rest = source[i:] if line_end == -1 else source[i:line_end]

    brace = rest.find("{")
    if brace == -1:
        return False

    semicolon = rest.find(";")

    return semicolon == -1 or brace < semicolon


def _enclosing_class(
    class_spans: list[tuple[str, int, int, int]],
    line_no: int,
    indent: int,
) -> str | None:
    """
    Return the name of the class whose body contains `line_no`, or None.

    Requires the candidate to be indented deeper than the class header,
    which is what separates a method from the `class` line itself and
    from anything that follows the closing brace. The innermost matching
    class wins, so a class nested inside another attributes correctly.
    """

    best: tuple[int, str] | None = None

    for name, class_indent, start, end in class_spans:
        if start < line_no <= end and indent > class_indent:
            if best is None or class_indent > best[0]:
                best = (class_indent, name)

    return best[1] if best else None


def _estimate_end(lines: list[str], start_idx: int, max_scan: int = 200) -> int:
    """
    Estimate the last line of a block starting at `start_idx` by
    scanning for a closing brace at the same indent level.
    """
    if start_idx >= len(lines):
        return start_idx + 1
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
