"""
Pearl Repository Intelligence subsystem — ``src.repository``.

This package transforms Pearl from a file-executor into a
repository-aware engineering assistant that can answer questions like
"Where is authentication implemented?", "What calls this function?",
and "Which files are most relevant to this task?" without reading the
entire repository on every turn.

Phase 1 — Repository Scanner::

    from src.repository import RepositoryScanner, Language
    from pathlib import Path

    scanner = RepositoryScanner(Path("."))
    result  = scanner.scan()

    for fi in result.source_files(Language.PYTHON):
        print(fi.relative_path, fi.size)

Phase 2 — Language Parser Framework::

    from src.repository.parsers import ParserRegistry, SymbolKind

    registry = ParserRegistry.default()
    results  = registry.parse_many(result.source_files(Language.PYTHON))

    for pr in results:
        funcs = [s for s in pr.symbols if s.kind is SymbolKind.FUNCTION]
        print(f"{pr.file_info.relative_path}: {len(funcs)} function(s)")

Each phase is a separate module; importing this package at any phase
always exposes the most recent public surface.
"""

from src.repository.models import (
    EXTENSION_TO_LANGUAGE,
    FileInfo,
    Language,
    ScanResult,
    detect_language,
)
from src.repository.scanner import GitignoreRules, RepositoryScanner

__all__ = [
    "EXTENSION_TO_LANGUAGE",
    "FileInfo",
    "GitignoreRules",
    "Language",
    "RepositoryScanner",
    "ScanResult",
    "detect_language",
]
