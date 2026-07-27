"""
Core data models for Pearl's Repository Intelligence subsystem (M3).

This module is the single source of truth for:

- :class:`Language` — every programming language Pearl understands
- :data:`EXTENSION_TO_LANGUAGE` — extension → language mapping used by
  both the scanner and the parser registry, so language detection is
  never duplicated
- :func:`detect_language` — derive language from a file path
- :class:`FileInfo` — immutable metadata record for one file
- :class:`ScanResult` — the result of a single repository scan
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Language(str, Enum):
    """Programming language of a source file."""

    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    JAVA = "java"
    GO = "go"
    RUST = "rust"
    CSHARP = "csharp"
    CPP = "cpp"
    C = "c"
    RUBY = "ruby"
    PHP = "php"
    SWIFT = "swift"
    KOTLIN = "kotlin"
    MARKDOWN = "markdown"
    JSON = "json"
    YAML = "yaml"
    TOML = "toml"
    HTML = "html"
    CSS = "css"
    SHELL = "shell"
    SQL = "sql"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Extension → Language mapping
# ---------------------------------------------------------------------------
# Used by both RepositoryScanner (Phase 1) and ParserRegistry (Phase 2).
# Add new mappings here; both systems pick them up automatically.

EXTENSION_TO_LANGUAGE: dict[str, Language] = {
    # Python
    ".py": Language.PYTHON,
    ".pyi": Language.PYTHON,
    ".pyx": Language.PYTHON,
    # JavaScript
    ".js": Language.JAVASCRIPT,
    ".mjs": Language.JAVASCRIPT,
    ".cjs": Language.JAVASCRIPT,
    ".jsx": Language.JAVASCRIPT,
    # TypeScript
    ".ts": Language.TYPESCRIPT,
    ".tsx": Language.TYPESCRIPT,
    ".mts": Language.TYPESCRIPT,
    ".cts": Language.TYPESCRIPT,
    # Java
    ".java": Language.JAVA,
    # Go
    ".go": Language.GO,
    # Rust
    ".rs": Language.RUST,
    # C#
    ".cs": Language.CSHARP,
    # C++
    ".cpp": Language.CPP,
    ".cc": Language.CPP,
    ".cxx": Language.CPP,
    ".hxx": Language.CPP,
    ".hpp": Language.CPP,
    # C
    ".c": Language.C,
    ".h": Language.C,
    # Ruby
    ".rb": Language.RUBY,
    ".rake": Language.RUBY,
    # PHP
    ".php": Language.PHP,
    # Swift
    ".swift": Language.SWIFT,
    # Kotlin
    ".kt": Language.KOTLIN,
    ".kts": Language.KOTLIN,
    # Markup / config
    ".md": Language.MARKDOWN,
    ".markdown": Language.MARKDOWN,
    ".json": Language.JSON,
    ".jsonc": Language.JSON,
    ".yaml": Language.YAML,
    ".yml": Language.YAML,
    ".toml": Language.TOML,
    # Web
    ".html": Language.HTML,
    ".htm": Language.HTML,
    ".css": Language.CSS,
    ".scss": Language.CSS,
    ".sass": Language.CSS,
    ".less": Language.CSS,
    # Shell
    ".sh": Language.SHELL,
    ".bash": Language.SHELL,
    ".zsh": Language.SHELL,
    ".fish": Language.SHELL,
    # SQL
    ".sql": Language.SQL,
}


def detect_language(path: Path) -> Language:
    """Return the :class:`Language` for *path* based on its extension.

    Always lower-cases the extension before lookup so ``FOO.PY`` is
    correctly identified as :attr:`Language.PYTHON`.  Returns
    :attr:`Language.UNKNOWN` for unrecognised or missing extensions.
    """
    return EXTENSION_TO_LANGUAGE.get(path.suffix.lower(), Language.UNKNOWN)


# ---------------------------------------------------------------------------
# File metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileInfo:
    """Immutable metadata record for one file in the repository.

    All fields are populated by the scanner and remain stable for the
    lifetime of a :class:`ScanResult`.  The scanner never opens a file
    for reasons other than hashing — content reading is the parser's
    job.
    """

    #: Absolute, resolved path on disk.
    path: Path
    #: Path relative to the repository root, always using forward slashes
    #: regardless of the host OS, so callers can do simple string matching.
    relative_path: str
    #: Lower-cased file extension including the leading dot (e.g. ``".py"``).
    #: Empty string for files without an extension.
    extension: str
    #: Detected programming language.
    language: Language
    #: File size in bytes (follows symlinks).
    size: int
    #: Last-modification time as a Unix timestamp (follows symlinks).
    modified_at: float
    #: MD5 hex digest of the file content.  Empty string if the file
    #: was unreadable at scan time.
    content_hash: str


# ---------------------------------------------------------------------------
# Scan result
# ---------------------------------------------------------------------------


@dataclass
class ScanResult:
    """The complete output of one :class:`~scanner.RepositoryScanner` run.

    Attributes
    ----------
    root:
        Resolved absolute path of the scanned repository root.
    files:
        All non-ignored files, sorted by :attr:`FileInfo.relative_path`.
    scan_duration_ms:
        Wall-clock time for the scan in milliseconds.
    gitignore_patterns:
        Total number of compiled ``.gitignore`` rules applied across
        all ``.gitignore`` files found during the walk.
    errors:
        Human-readable descriptions of any non-fatal errors that
        occurred (e.g. unreadable files).  Populated entries do not
        block the scan from completing.
    """

    root: Path
    files: list[FileInfo] = field(default_factory=list)
    scan_duration_ms: float = 0.0
    gitignore_patterns: int = 0
    errors: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Convenience helpers — derived views over ``self.files``
    # ------------------------------------------------------------------

    def by_language(self) -> dict[Language, list[FileInfo]]:
        """Return files grouped by language."""
        result: dict[Language, list[FileInfo]] = {}
        for fi in self.files:
            result.setdefault(fi.language, []).append(fi)
        return result

    def by_extension(self) -> dict[str, list[FileInfo]]:
        """Return files grouped by extension."""
        result: dict[str, list[FileInfo]] = {}
        for fi in self.files:
            result.setdefault(fi.extension, []).append(fi)
        return result

    def source_files(self, *languages: Language) -> list[FileInfo]:
        """Return files whose language is in *languages*.

        With no arguments returns every file that has a recognised
        language (i.e. excludes :attr:`Language.UNKNOWN`).
        """
        if not languages:
            return [fi for fi in self.files if fi.language is not Language.UNKNOWN]
        lang_set = set(languages)
        return [fi for fi in self.files if fi.language in lang_set]

    def __len__(self) -> int:
        return len(self.files)
