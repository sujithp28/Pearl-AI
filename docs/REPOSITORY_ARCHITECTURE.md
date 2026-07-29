# Pearl Repository Intelligence — Architecture

This document is the living reference for Pearl's Repository Intelligence
subsystem (`src/repository/`).  It covers the design of every completed
phase, the extension guide for adding new parsers and phases, and the
public API catalogue with runnable examples.

Update this document in the same PR as any change to `src/repository/`.

---

## Table of Contents

1. [Why Repository Intelligence?](#1-why-repository-intelligence)
2. [Subsystem Map](#2-subsystem-map)
3. [Phase 1 — Repository Scanner](#3-phase-1--repository-scanner)
4. [Phase 2 — Language Parser Framework](#4-phase-2--language-parser-framework)
5. [Future Phases (roadmap)](#5-future-phases-roadmap)
6. [Public API Reference](#6-public-api-reference)
7. [Extension Guide](#7-extension-guide)
8. [Performance Notes](#8-performance-notes)
9. [Design Decisions](#9-design-decisions)

---

## 1. Why Repository Intelligence?

Pearl began as a tool-execution agent: it received a task, asked the LLM
what tools to call, and executed them.  The LLM's context window was
filled manually — whatever files the user happened to mention, plus
whatever Pearl read during execution.

Repository Intelligence replaces that ad-hoc approach with a systematic
understanding of the entire codebase:

- **Before sending anything to the LLM**, Pearl scans the repository,
  parses source files into structured symbol tables, and builds a
  dependency graph.
- **Context selection** becomes a ranked retrieval problem rather than
  a guessing game: Pearl can find the five most relevant files for a
  task, not just the three the user remembered to mention.
- **Semantic search** ("find the class that handles authentication")
  works across the entire repository, not just the currently open file.

---

## 2. Subsystem Map

```
src/repository/
├── __init__.py          Public surface — re-exports everything callers need.
├── models.py            Core data models shared by all phases.
│                          Language, EXTENSION_TO_LANGUAGE, detect_language
│                          FileInfo, ScanResult
├── scanner.py           Phase 1 — Repository Scanner
│                          GitignoreRules, RepositoryScanner
└── parsers/
    ├── __init__.py      Phase 2 — Language Parser Framework
    │                      BaseParser, ParserRegistry, ParseResult, SymbolDef
    └── python_parser.py Phase 3 — Python AST Parser (planned)
```

Dependencies flow downward.  `scanner.py` imports only from `models.py`.
`parsers/` imports only from `models.py` and the Python standard library.
Neither imports from `src/agent/`, `src/tools/`, or `src/mcp/`.

---

## 3. Phase 1 — Repository Scanner

### Goal

Walk a repository tree efficiently and return structured file metadata
for every non-ignored file.

### Data Flow

```
RepositoryScanner(root)
       │
       ▼
  os.walk(topdown=True)
       │  in-place dirnames mutation prunes entire ignored subtrees
       │
       ├─ _ALWAYS_IGNORE_DIRS check (O(1) frozenset lookup)
       │
       ├─ GitignoreRules.is_ignored() per directory and file
       │
       ▼
  list[Path]   (all eligible file paths)
       │
       ▼
  ThreadPoolExecutor  (parallel MD5 hashing + stat)
       │
       ▼
  list[FileInfo]  sorted by relative_path
       │
       ▼
  ScanResult
```

### Key classes

#### `Language` (models.py)

An enum of every programming language Pearl understands.  Used by both
the scanner (language detection) and the parser registry (parser lookup).

```python
from src.repository import Language

Language.PYTHON     # "python"
Language.TYPESCRIPT # "typescript"
Language.UNKNOWN    # "unknown" — for unrecognised extensions
```

#### `EXTENSION_TO_LANGUAGE` (models.py)

The single source of truth for extension → language mapping.  Covers
38 extensions across 21 languages.  Both the scanner and the parser
registry use this table — there is no duplicate mapping.

```python
from src.repository import EXTENSION_TO_LANGUAGE

EXTENSION_TO_LANGUAGE[".py"]   # Language.PYTHON
EXTENSION_TO_LANGUAGE[".tsx"]  # Language.TYPESCRIPT
```

#### `detect_language(path)` (models.py)

Derive the language from a file path.  Case-insensitive.

```python
from pathlib import Path
from src.repository import detect_language, Language

detect_language(Path("main.py"))     # Language.PYTHON
detect_language(Path("FOO.PY"))      # Language.PYTHON  (case-insensitive)
detect_language(Path("data.xyzzy")) # Language.UNKNOWN
```

#### `FileInfo` (models.py)

Immutable metadata record for one file.  All fields are populated by
the scanner at scan time.

```python
from src.repository import FileInfo

fi: FileInfo  # from ScanResult.files
fi.path           # Path — absolute, resolved
fi.relative_path  # str  — forward slashes, relative to repo root
fi.extension      # str  — lower-cased, e.g. ".py"
fi.language       # Language
fi.size           # int  — bytes
fi.modified_at    # float — Unix timestamp
fi.content_hash   # str  — MD5 hex digest; "" if file was unreadable
```

#### `ScanResult` (models.py)

The output of one scanner run.

```python
from src.repository import ScanResult

result: ScanResult  # from RepositoryScanner.scan()
result.root               # Path — resolved repository root
result.files              # list[FileInfo] — sorted by relative_path
result.scan_duration_ms   # float — wall-clock time
result.gitignore_patterns # int   — compiled rules across all .gitignore files
result.errors             # list[str] — non-fatal errors (empty = clean scan)
len(result)               # int — number of files

# Convenience views
result.by_language()                          # dict[Language, list[FileInfo]]
result.by_extension()                         # dict[str, list[FileInfo]]
result.source_files()                         # all files with known language
result.source_files(Language.PYTHON)          # Python files only
result.source_files(Language.PYTHON,
                    Language.TYPESCRIPT)      # Python + TypeScript
```

#### `GitignoreRules` (scanner.py)

Compiled rules from a single `.gitignore` file.  Implements the last-
rule-wins semantics defined by the gitignore specification.

```python
from pathlib import Path
from src.repository import GitignoreRules

rules = GitignoreRules(base=Path("/my/project/src"), raw_lines=["*.log", "!important.log"])
rules.is_ignored(Path("/my/project/src/debug.log"), is_dir=False)     # True
rules.is_ignored(Path("/my/project/src/important.log"), is_dir=False) # False
rules.is_ignored(Path("/elsewhere/debug.log"), is_dir=False)          # None (out of scope)
```

Return values of `is_ignored()`:
- `True` — path is ignored by this rule set
- `False` — a negation rule explicitly un-ignores it
- `None` — no rule matched; caller should continue checking other rule sets

#### `RepositoryScanner` (scanner.py)

```python
from pathlib import Path
from src.repository import RepositoryScanner, Language

scanner = RepositoryScanner(
    root=Path("/my/project"),
    extra_ignore_dirs={"fixtures"},  # merged with built-in blacklist
    max_workers=8,                   # thread-pool size for hashing
    max_file_size_bytes=50_000_000,  # files larger than this are skipped
)
result = scanner.scan()

# --- Common queries ---

# All Python files
py_files = result.source_files(Language.PYTHON)

# Group everything by language
for lang, files in result.by_language().items():
    print(f"{lang.value}: {len(files)} files")

# Check a specific path
by_rel = {fi.relative_path: fi for fi in result.files}
info = by_rel.get("src/agent/executor.py")

# Statistics
print(f"Scanned {len(result)} files in {result.scan_duration_ms:.0f} ms")
print(f"Gitignore rules applied: {result.gitignore_patterns}")
if result.errors:
    print(f"Non-fatal errors: {result.errors}")
```

### Always-ignored directories

These directories are pruned unconditionally before any `.gitignore`
logic runs.  They are never descended into, regardless of `.gitignore`
content.

| Category | Directories |
|---|---|
| Version control | `.git`, `.hg`, `.svn` |
| Python artefacts | `__pycache__`, `.mypy_cache`, `.ruff_cache`, `.pytest_cache`, `.tox`, `.eggs` |
| Virtual environments | `venv`, `.venv`, `env`, `.env` |
| JavaScript | `node_modules`, `.npm` |
| Build outputs | `dist`, `build`, `.build`, `out`, `target` |
| Caches / tool state | `.cache`, `.DS_Store`, `coverage`, `htmlcov` |
| IDE | `.idea`, `.vscode` |
| Generated | `__MACOSX`, `.next`, `.nuxt`, `.svelte-kit` |

### Gitignore features supported

| Feature | Example | Notes |
|---|---|---|
| Comment lines | `# ignore logs` | Skipped |
| Blank lines | | Skipped |
| Simple glob | `*.pyc` | Matches at any depth |
| Directory-only | `dist/` | Only matches directories |
| Negation | `!important.log` | Un-ignores a previously matched path |
| Anchored (leading `/`) | `/secrets.txt` | Root-relative only |
| Anchored (internal `/`) | `src/gen` | Relative to `.gitignore` base |
| Double-star prefix | `**/logs/` | Matches at any depth |
| Double-star inline | `src/**/test_*.py` | Spans directory boundaries |
| Question mark | `test?.py` | One character, not `/` |
| Last-rule-wins | `*.log` then `!debug.log` | Standard gitignore semantics |
| Nested `.gitignore` | `src/.gitignore` | Scoped to its directory |

Non-UTF-8 `.gitignore` files (Latin-1, UTF-16, binary) are read with
`errors="replace"` — invalid bytes become `�` and pattern parsing
continues on all remaining valid lines.

---

## 4. Phase 2 — Language Parser Framework

### Goal

A pluggable parser architecture that allows any language parser to be
registered and discovered without modifying the framework code.

### Data Flow

```
ParserRegistry
       │  register(parser)
       │
       ├─ parser_for(language)  →  BaseParser subclass
       │
       └─ parse(file_info)      →  ParseResult
                                     symbols: list[SymbolDef]
                                     imports: list[str]
                                     errors:  list[str]
```

### Key classes

#### `SymbolDef` (parsers/__init__.py)

One extracted symbol (class, function, method, variable, constant).

```python
from src.repository.parsers import SymbolDef, SymbolKind

sym: SymbolDef
sym.name          # str   — unqualified name, e.g. "authenticate"
sym.qualified_name# str   — dotted path, e.g. "auth.middleware.authenticate"
sym.kind          # SymbolKind.FUNCTION | CLASS | METHOD | ...
sym.line_start    # int   — 1-indexed
sym.line_end      # int   — 1-indexed, inclusive
sym.docstring     # str | None
sym.decorators    # list[str]
sym.is_async      # bool
sym.parent        # str | None — qualified name of enclosing symbol
```

#### `ParseResult` (parsers/__init__.py)

Output of one parser run on one file.

```python
from src.repository.parsers import ParseResult

pr: ParseResult
pr.file_info      # FileInfo — the file that was parsed
pr.symbols        # list[SymbolDef] — all extracted symbols
pr.imports        # list[str] — raw import strings
pr.errors         # list[str] — non-fatal parse errors
pr.language       # Language — convenience alias for file_info.language
```

#### `BaseParser` (parsers/__init__.py)

Abstract base class every language parser implements.

```python
from src.repository.parsers import BaseParser, ParseResult
from src.repository.models import FileInfo, Language

class MyLangParser(BaseParser):
    @property
    def language(self) -> Language:
        return Language.MYLANG          # must match registry key

    @property
    def supported_extensions(self) -> frozenset[str]:
        return frozenset({".ml", ".mli"})

    def parse(self, file_info: FileInfo) -> ParseResult:
        ...                             # read file, extract symbols
```

#### `ParserRegistry` (parsers/__init__.py)

Singleton registry that maps languages to parser instances.

```python
from src.repository.parsers import ParserRegistry
from src.repository.models import Language

registry = ParserRegistry()

# Register a parser
registry.register(MyLangParser())

# Check capability
registry.supports(Language.PYTHON)          # bool
registry.supported_languages()              # frozenset[Language]

# Parse a file
result = registry.parse(file_info)          # ParseResult | None

# Batch parse
results = registry.parse_many(file_infos)   # list[ParseResult]
```

---

## 5. Future Phases (roadmap)

| Phase | Module | Goal |
|---|---|---|
| 3 | `parsers/python_parser.py` | Python AST parser — classes, functions, imports, type hints |
| 4 | `index.py` | Symbol index — fast lookup by name, kind, file |
| 5 | `graph.py` | Reference graph — import graph, call graph, inheritance |
| 6 | `context_builder.py` | Automatically select most-relevant files for a task |
| 7 | `search.py` | Cross-repository symbol and text search |
| 8 | `ranking.py` | Result ranking by reference count, proximity, importance |
| 9 | `repository.py` | High-level `Repository` facade — `scan()`, `search()`, `index()` |
| 10 | — | Multi-language readiness audit |

Each phase adds one module.  No existing module is rewritten.  Public
surfaces are extended, never broken.

---

## 6. Public API Reference

### Importing

```python
# Everything a caller needs is available from the package root.
from src.repository import (
    RepositoryScanner,
    ScanResult,
    FileInfo,
    Language,
    GitignoreRules,
    detect_language,
    EXTENSION_TO_LANGUAGE,
)

# Parser framework (Phase 2+)
from src.repository.parsers import (
    BaseParser,
    ParserRegistry,
    ParseResult,
    SymbolDef,
    SymbolKind,
)
```

### End-to-end example: scan + filter + parse

```python
from pathlib import Path
from src.repository import RepositoryScanner, Language
from src.repository.parsers import ParserRegistry

# 1. Scan
scanner = RepositoryScanner(Path("."))
scan = scanner.scan()
print(f"Repository: {len(scan)} files, {scan.scan_duration_ms:.0f} ms")

# 2. Filter to Python source
py_files = scan.source_files(Language.PYTHON)
print(f"Python files: {len(py_files)}")

# 3. Parse (Phase 2+)
registry = ParserRegistry.default()   # pre-loaded with built-in parsers
for fi in py_files:
    result = registry.parse(fi)
    if result:
        classes = [s for s in result.symbols if s.kind.name == "CLASS"]
        print(f"  {fi.relative_path}: {len(classes)} class(es)")
```

---

## 7. Extension Guide

### Adding a new language extension

Add one entry to `EXTENSION_TO_LANGUAGE` in `src/repository/models.py`.
Both the scanner (language detection) and the parser registry (parser
lookup) pick it up automatically — no other change required.

```python
# models.py — EXTENSION_TO_LANGUAGE
".ex":  Language.ELIXIR,   # example
".exs": Language.ELIXIR,
```

### Adding a new language to the `Language` enum

1. Add the member to `Language` in `src/repository/models.py`.
2. Add its extension(s) to `EXTENSION_TO_LANGUAGE` in the same file.
3. Implement a parser in `src/repository/parsers/elixir_parser.py`
   (Phase 3+ pattern).
4. Register it in `ParserRegistry.default()`.

### Adding a new parser (Phase 2+ pattern)

```python
# src/repository/parsers/go_parser.py
from src.repository.parsers import BaseParser, ParseResult, SymbolDef, SymbolKind
from src.repository.models import FileInfo, Language

class GoParser(BaseParser):
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
        # ... parse logic using only the standard library or safe AST tools
        return ParseResult(
            file_info=file_info,
            symbols=symbols,
            imports=imports,
            errors=errors,
        )
```

Then register:

```python
# In ParserRegistry.default() or at application startup:
registry.register(GoParser())
```

---

## 8. Performance Notes

### Scanner

| Technique | Why |
|---|---|
| `os.walk(topdown=True)` + `dirnames` mutation | Entire ignored subtrees (`node_modules`, `.git`) are never opened |
| `frozenset` for always-ignore blacklist | O(1) membership test per directory |
| `.gitignore` rules compiled to `re.Pattern` at load time | Per-file matching is a handful of regex searches, not string parsing |
| `ThreadPoolExecutor` for MD5 hashing | I/O parallelism; directory walk is not bottlenecked by disk |
| Stable sort on `relative_path` | Deterministic output without re-sorting on every query |

### Benchmarks (observed on pearl-agent codebase)

| Workload | Result |
|---|---|
| pearl-agent repo (209 files) | ~120 ms |
| 500-file synthetic repo | < 1 s |
| 2 000-file synthetic repo | ~1 s (~2 000 files/s) |

### Limits

| Parameter | Default | Purpose |
|---|---|---|
| `max_file_size_bytes` | 50 MB | Files larger than this are skipped entirely |
| `max_workers` | 8 | Thread-pool size for hashing |

---

## 9. Design Decisions

### Why MD5 for content hashing?

MD5 is fast, produces a short digest, and is sufficient for change
detection (not security).  `hashlib.md5(usedforsecurity=False)` is used
explicitly so the call works on platforms where MD5 is restricted in
FIPS mode.

### Why `followlinks=False`?

Symlinks to directories create cycles that cause infinite walks.
`followlinks=False` prevents this unconditionally.  Symlinks to
*files* are still followed via `path.stat()` (which follows symlinks),
so symlinked files appear in the scan result.

### Why hardcode the always-ignore list instead of using `.gitignore`?

`.gitignore` files are per-project and may not exclude `node_modules` or
`.git`.  Pearl needs a guaranteed baseline that does not depend on
project configuration — especially important when scanning a repository
that has no `.gitignore` at all.

### Why `errors="replace"` for `.gitignore` decoding?

Git itself accepts any byte sequence in `.gitignore` files.  Projects
with non-UTF-8 team members may have Latin-1 or UTF-16 encoded
`.gitignore` files.  Using `errors="replace"` means invalid bytes become
`�` and all remaining valid ASCII patterns (which is all that
matters for typical gitignore content) are preserved.

### Why is the extension → language mapping in `models.py`?

Keeping it in one place eliminates the risk of the scanner and the
parser registry using different mappings.  `detect_language()` in the
scanner and `parser_for()` in the registry both derive their knowledge
from the same `EXTENSION_TO_LANGUAGE` dict.
