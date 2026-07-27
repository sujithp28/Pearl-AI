"""
Pearl Repository Intelligence subsystem — ``src.repository``.

This package transforms Pearl from a file-executor into a
repository-aware engineering assistant that can answer questions like
"Where is authentication implemented?", "What calls this function?",
and "Which files are most relevant to this task?" without reading the
entire repository on every turn.

Current phase (Phase 1 — Repository Scanner):

    from src.repository import RepositoryScanner, Language

    scanner = RepositoryScanner(Path("/my/project"))
    result  = scanner.scan()

    for fi in result.source_files(Language.PYTHON):
        print(fi.relative_path, fi.size)

Subsequent phases will add parsers, indexes, a reference graph, a
context builder, a ranking engine, and high-level :class:`Repository`
APIs.  Each phase is a separate module; importing this package at any
phase always exposes the most recent public surface.
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
