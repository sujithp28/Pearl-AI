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
5. [Phase 3 — Python AST Parser](#5-phase-3--python-ast-parser)
6. [Phase 4 — Repository Index](#6-phase-4--repository-index)
7. [SymbolDef Extended Fields (Phase 4)](#7-symboldef-extended-fields-phase-4)
8. [Phase 5 — Reference Graph (RepositoryGraph)](#8-phase-5--reference-graph-repositorygraph)
9. [Future Phases (roadmap)](#9-future-phases-roadmap)
10. [Public API Reference](#10-public-api-reference)
11. [Extension Guide](#11-extension-guide)
12. [Performance Notes](#12-performance-notes)
13. [Design Decisions](#13-design-decisions)

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
├── index.py             Phase 4 — Repository Index
│                          SymbolEntry, IndexStats, RepositoryIndex
├── graph.py             Phase 5 — Reference Graph
│                          NodeKind, EdgeKind, Node, Edge
│                          ImpactResult, GraphStats, RepositoryGraph
└── parsers/
    ├── __init__.py      Phase 2 — Language Parser Framework
    │                      BaseParser, ParserRegistry, ParseResult, SymbolDef
    └── python_parser.py Phase 3 — Python AST Parser
```

Dependencies flow downward.  `scanner.py` imports only from `models.py`.
`parsers/` imports only from `models.py` and the Python standard library.
`index.py` imports only from `models.py` and `parsers/`.
`graph.py` imports only from `index.py`, `parsers/`, and the Python
standard library.
None of the repository modules import from `src/agent/`, `src/tools/`,
or `src/mcp/`.

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

## 5. Phase 3 — Python AST Parser

### Goal

Extract all symbols, imports, and structural metadata from Python source
files using only the standard-library `ast` module.  The parser **never
executes user code**.

### Supported files

`.py`, `.pyi` (stub files), `.pyx` (Cython source)

### Data flow

```
PythonParser.parse(file_info)
       │
       ├─ _read()  — UTF-8 → latin-1 fallback → OSError capture
       │
       ├─ ast.parse()  — SyntaxError / ValueError captured as errors
       │
       ├─ _extract(tree.body)  — recursive, parent-tracked walk
       │     ├─ ClassDef       → SymbolKind.CLASS, recurse body in_class=True
       │     ├─ FunctionDef    → SymbolKind.FUNCTION or METHOD
       │     ├─ AsyncFunctionDef → same + is_async=True
       │     ├─ Assign         → SymbolKind.CONSTANT or VARIABLE (module/class scope only)
       │     └─ AnnAssign      → same
       │
       └─ ast.walk()  — one pass for all Import / ImportFrom nodes
```

### SymbolDef contract for Python

Every field is deterministic and derived from AST information only —
no heuristics, no execution.

#### Classes (`ClassDef`)

| Field | Value |
|---|---|
| `kind` | `SymbolKind.CLASS` |
| `name` | `node.name` |
| `qualified_name` | `"Parent.ClassName"` — dot-joined ancestor chain |
| `line_start` | `node.lineno` (line of `class Foo:`) |
| `line_end` | `node.end_lineno` (last line of class body) |
| `docstring` | `ast.get_docstring(node)` — `None` if absent |
| `decorators` | decorator names in source order — see *Decorator extraction* below |
| `is_async` | always `False` (Python classes are not async) |
| `parent` | qualified name of enclosing class/function, or `None` |

#### Functions and methods (`FunctionDef`, `AsyncFunctionDef`)

| Field | Value |
|---|---|
| `kind` | `SymbolKind.METHOD` if direct parent is a class body; `SymbolKind.FUNCTION` otherwise |
| `name` | `node.name` |
| `qualified_name` | `"Class.method_name"` — dot-joined ancestor chain |
| `line_start` | `node.lineno` |
| `line_end` | `node.end_lineno` |
| `docstring` | `ast.get_docstring(node)` — `None` if absent |
| `decorators` | decorator names in source order |
| `is_async` | `True` for `AsyncFunctionDef`, `False` for `FunctionDef` |
| `parent` | qualified name of enclosing scope, or `None` |

#### Variables and constants (`Assign`, `AnnAssign`)

Only extracted at **module scope** and **class body scope** — not inside
function bodies (local variables are noise for symbol search).

Only simple single-name targets are extracted: `NAME = value` and
`name: type = value`.  Tuple unpacking (`a, b = ...`) and chained
assignment (`a = b = ...`) are skipped.

| Field | Value |
|---|---|
| `kind` | `SymbolKind.CONSTANT` if `name == name.upper() and any(c.isalpha() for c in name)`; `SymbolKind.VARIABLE` otherwise |
| `name` | target variable name |
| `qualified_name` | `"Class.CONSTANT_NAME"` or just `"CONSTANT_NAME"` at module scope |
| `line_start` | `node.lineno` |
| `line_end` | `node.end_lineno` |
| `docstring` | always `None` (assignments have no docstring) |
| `decorators` | always `[]` |
| `is_async` | always `False` |
| `parent` | class qualified name, or `None` at module scope |

Examples of the ALL_CAPS constant rule:

| Name | Kind |
|---|---|
| `MAX_RETRIES` | CONSTANT |
| `HTTP_404` | CONSTANT |
| `_PRIVATE_CONST` | CONSTANT (starts with `_`, rest is upper) |
| `variable` | VARIABLE |
| `camelCase` | VARIABLE |
| `__version__` | VARIABLE (contains lowercase letters) |
| `MyClass` | VARIABLE (if assigned, not defined as ClassDef) |

#### Decorator extraction

Decorator names are extracted recursively:

| Decorator syntax | Extracted string |
|---|---|
| `@classmethod` | `"classmethod"` |
| `@property` | `"property"` |
| `@some_module.decorator` | `"decorator"` (rightmost attribute name) |
| `@lru_cache(maxsize=None)` | `"lru_cache"` (function name of the call) |
| `@tool(name="x", ...)` | `"tool"` |
| Any other expression | `ast.unparse(node)` as fallback |

#### Import extraction

All imports are extracted regardless of scope (module-level, inside
functions, inside `if TYPE_CHECKING:` blocks).

| Source | Extracted string |
|---|---|
| `import os` | `"import os"` |
| `import sys, pathlib` | `"import sys"`, `"import pathlib"` (one per name) |
| `from pathlib import Path` | `"from pathlib import Path"` |
| `from pathlib import Path, PurePath` | `"from pathlib import Path, PurePath"` |
| `from . import sibling` | `"from . import sibling"` |
| `from ..utils import helper` | `"from ..utils import helper"` |

#### Error handling

All errors are non-fatal — the parser always returns a `ParseResult`:

| Error type | Behaviour |
|---|---|
| `SyntaxError` from `ast.parse()` | Error string `"SyntaxError at line N: <msg>"` in `ParseResult.errors`; symbols/imports from that point onward are unavailable |
| `UnicodeDecodeError` on read | Retry with `latin-1`; if that also fails, error in `ParseResult.errors` |
| `OSError` (file not found, permissions) | Error in `ParseResult.errors`; empty symbols and imports |
| Any other exception | Caught by `ParserRegistry.parse()`; error in `ParseResult.errors` |

#### What is NOT extracted

| Excluded | Reason |
|---|---|
| Local variables inside functions | Too noisy; not useful for symbol search or context selection |
| Tuple-unpacked assignments (`a, b = ...`) | Ambiguous scope; conservative exclusion |
| Chained assignments (`a = b = 0`) | Unusual; conservative exclusion |
| Lambda bodies | Lambdas are anonymous; only the variable they're assigned to appears |
| Comprehension variables | Scoped to the comprehension; not meaningful symbols |
| Type comments | Covered by annotation syntax in modern Python |

---

## 6. Phase 4 — Repository Index

### Goal

Build a production-grade in-memory index from the parse results of every
file in a repository.  The index is the primary data layer for all
downstream phases — it answers every structured query about symbols and
imports in O(1) or O(n) time without re-parsing files.

### Architecture

Five internal dicts are populated in a single O(n) pass over parse results:

| Internal dict | Key | Value | Answers |
|---|---|---|---|
| `_by_file` | `relative_path` | `list[SymbolEntry]` | "what symbols live in this file?" |
| `_by_name` | unqualified name | `list[SymbolEntry]` | "where is `PatchManager` defined?" |
| `_by_qualified_name` | qualified name | `SymbolEntry` | O(1) exact lookup |
| `_by_kind` | `SymbolKind` | `list[SymbolEntry]` | "list all `CLASS` symbols" |
| `_imports_by_file` | `relative_path` | `list[str]` | "what does this file import?" |

### Key classes

#### `SymbolEntry` (index.py)

A flat, denormalised record that joins `SymbolDef` with its `FileInfo`.
Callers never need to join two data structures to answer a query.

```python
from src.repository.index import SymbolEntry

entry: SymbolEntry
entry.symbol        # SymbolDef — full symbol metadata
entry.file_info     # FileInfo  — file the symbol came from
entry.relative_path # str       — convenience alias
entry.language      # Language  — convenience alias
```

#### `IndexStats` (index.py)

Lightweight statistics snapshot, returned by `RepositoryIndex.stats()`.

```python
from src.repository.index import IndexStats

s: IndexStats
s.file_count        # int          — indexed files
s.symbol_count      # int          — total symbols
s.import_count      # int          — total import strings
s.build_duration_ms # float        — wall-clock build time
s.languages         # frozenset[Language]
s.error_file_count  # int          — files with parse errors
```

#### `RepositoryIndex` (index.py)

```python
from src.repository.index import RepositoryIndex
from src.repository.parsers import SymbolKind

index = RepositoryIndex.build(parse_results)

# Exact O(1) lookup by qualified name
entry = index.lookup_qualified("PearlAgent.run_autonomous")

# All definitions of a name (multi-file)
entries = index.lookup("PatchManager")

# All symbols in one file, in source order
file_syms = index.symbols_in_file("src/agent/agent.py")

# All symbols of a kind
methods = index.symbols_by_kind(SymbolKind.METHOD)

# Glob search on qualified names
managers = index.search("*Manager")
members  = index.search("PearlAgent.*")

# Import queries
deps   = index.imports_for("src/agent/agent.py")
users  = index.files_importing("asyncio")
files  = index.indexed_files()    # all FileInfo, sorted by path

# Diagnostics
print(index.stats())
print(len(index))   # total symbol count
```

### Data flow

```
list[ParseResult]
       │
       ▼
RepositoryIndex.build()   ← O(n) single pass
       │
       ├── _by_file[path]          → list[SymbolEntry]
       ├── _by_name[name]          → list[SymbolEntry]
       ├── _by_qualified_name[qn]  → SymbolEntry
       ├── _by_kind[kind]          → list[SymbolEntry]
       └── _imports_by_file[path]  → list[str]
```

### What each future phase uses

| Phase | Methods it calls |
|---|---|
| 5 — Reference Graph | `imports_for()`, `files_importing()`, `symbols_in_file()` |
| 6 — Context Builder | `lookup()`, `lookup_qualified()`, `symbols_in_file()` |
| 7 — Search | `search()`, `symbols_by_kind()`, `lookup()` |
| 8 — Ranking | `symbols_by_kind()`, `files_importing()`, `stats()` |

### Performance

| Workload | Observed |
|---|---|
| Build 5 000 symbols | < 50 ms |
| 1 000 `lookup()` calls | < 10 ms |
| `search("*sym_*")` over 5 000 symbols | < 50 ms |

---

## 7. SymbolDef Extended Fields (Phase 4)

Phase 4 adds three optional fields to `SymbolDef` that downstream phases
will use.  All three default to safe values (`None` or `[]`) so all
existing parsers work without modification.  The Python parser populates
all three.

| Field | Type | Populated by | Purpose |
|---|---|---|---|
| `signature` | `str \| None` | Python parser | Full parameter list — e.g. `"(self, path: str) -> None"`. Used by Context Builder (Phase 6) to enrich LLM prompts. |
| `return_type` | `str \| None` | Python parser | Return annotation text — e.g. `"str \| None"`. Used by Reference Graph (Phase 5) for type-level resolution. |
| `raises` | `list[str]` | Python parser | Exception type names raised directly in the body — e.g. `["ValueError", "OSError"]`. Used by Context Builder (Phase 6) for error-path analysis. |

Extraction rules for the Python parser:

| Field | Source |
|---|---|
| `signature` | `f"({ast.unparse(node.args)}){' -> ' + ast.unparse(node.returns) if node.returns else ''}"` |
| `return_type` | `ast.unparse(node.returns)` if present, else `None` |
| `raises` | Top-level `raise SomeExc(...)` and `raise SomeExc` statements in the function body. Bare `raise` and nested raises excluded. |

---

## 8. Phase 5 — Reference Graph (RepositoryGraph)

### Goal

Build a directed, typed graph of all file and symbol relationships so that
downstream phases can answer structural questions a flat index cannot:

- **What would break if I change this file?** (impact analysis)
- **What is the shortest dependency path between two files?** (BFS)
- **Which import cycles exist?** (Tarjan's SCC on the IMPORTS subgraph)
- **What classes inherit from this base?** (INHERITS edges)
- **What symbols does a file define?** (DEFINES edges)

The graph is built from a `RepositoryIndex` and is the data layer for Phase 6
(Context Builder), Phase 7 (Search), and Phase 8 (Ranking).

### Node design

Every node has a unique `id` string.  Two node kinds exist:

| Kind | ID scheme | Example |
|---|---|---|
| `NodeKind.FILE` | repository-relative file path | `"src/agent/agent.py"` |
| `NodeKind.SYMBOL` | `"{relative_path}#{qualified_name}"` | `"src/agent/agent.py#PearlAgent.run"` |

The `#` separator cannot appear in file paths or Python qualified names so
it creates a collision-free composite key.

### Edge design

Five typed edges model every structural relationship Pearl needs:

| Edge kind | Source → Target | Meaning | Phase populated |
|---|---|---|---|
| `IMPORTS` | FILE → FILE | file imports another file | 5 |
| `DEFINES` | FILE → SYMBOL | file defines a symbol | 5 |
| `CONTAINS` | SYMBOL → SYMBOL | parent symbol contains a child | 5 |
| `INHERITS` | SYMBOL → SYMBOL | class inherits from a base class | 5 |
| `CALLS` | SYMBOL → SYMBOL | symbol calls another | reserved Phase 7 |

Edges are deduplicated — the same `(source, target, kind)` triple is never
stored twice.

### Import resolution

Import strings like `"from src.agent import PearlAgent"` are resolved to file
paths via a **module map** built from the index.

**Module map construction:**

| File path | Dotted module key |
|---|---|
| `src/repository/index.py` | `src.repository.index` |
| `src/repository/parsers/__init__.py` | `src.repository.parsers` |
| `src/repository/__init__.py` | `src.repository` |

Rule: `__init__.py` files map to their package name (directory path with `/`→`.`);
all others strip the extension and join directory + stem.

**Resolution algorithm:**

*Absolute imports* (`import X`, `from X import Y`):
1. Extract the module name (everything before `import`).
2. Look up in the module map.  No match → stdlib or third-party; no edge created.

*Relative imports* (`from . import X`, `from ..utils import Y`):
1. Count leading dots → `n_dots`.
2. Compute the current package: directory parts of the importing file's path.
3. Go up `n_dots − 1` directory levels from the current package.
4. Append the module suffix (if any) to get the target dotted name.
5. Look up in the module map.

### Cycle detection

Tarjan's SCC algorithm (iterative, no recursion limit) runs on the IMPORTS
subgraph.  An SCC of size > 1 is a circular import group.

```python
cycles = graph.detect_cycles()
# → [["src/a.py", "src/b.py"], ...]  — one list per cycle
```

### Impact analysis

`RepositoryGraph.impact(path)` answers "what would break if this file changes?":

1. Collect direct dependents — files that have an IMPORTS edge pointing to the
   changed file.
2. BFS over reverse IMPORTS edges to collect all transitive dependents.
3. Count DEFINES edges from all dependent files to estimate affected symbols.
4. Classify risk: **LOW** (≤ 2 transitive dependents), **MEDIUM** (3–10),
   **HIGH** (> 10).

### Data flow

```
RepositoryIndex
       │
       ▼
RepositoryGraph.build(index)
       │
       ├─ _build_module_map()           dotted name → file path
       │
       ├─ Add FileNode per indexed file
       │
       ├─ Add SymbolNode per symbol
       │   + DEFINES edge (file → symbol)
       │   + CONTAINS edge (parent_sym → child_sym)
       │
       ├─ Resolve import strings → IMPORTS edges (file → file)
       │
       ├─ Resolve base_classes → INHERITS edges (class → base class symbol)
       │
       └─ Tarjan's SCC → cycle_count for GraphStats
```

### Internal storage

```python
class RepositoryGraph:
    _nodes:    dict[str, Node]                        # id → Node
    _adj_out:  dict[str, list[Edge]]                  # source → outgoing edges
    _adj_in:   dict[str, list[Edge]]                  # target → incoming edges
    _edge_set: set[tuple[str, str, EdgeKind]]          # deduplication
    _stats:    GraphStats | None
```

Both adjacency lists are maintained simultaneously so that all traversals
(forward dependencies, reverse dependents, BFS, SCC) run without
constructing transposed graphs on the fly.

### Key classes (graph.py)

#### `NodeKind` / `EdgeKind` (graph.py)

```python
from src.repository.graph import NodeKind, EdgeKind

NodeKind.FILE    # "file"
NodeKind.SYMBOL  # "symbol"

EdgeKind.IMPORTS   # "imports"
EdgeKind.DEFINES   # "defines"
EdgeKind.CONTAINS  # "contains"
EdgeKind.INHERITS  # "inherits"
EdgeKind.CALLS     # "calls"  (reserved)
```

#### `Node` / `Edge` (graph.py)

```python
from src.repository.graph import Node, Edge

node: Node
node.id          # str   — unique identifier
node.kind        # NodeKind
node.label       # str   — qualified_name for SYMBOL, relative_path for FILE
node.language    # str | None — Language.value for FILE nodes
node.size        # int   — bytes for FILE nodes
node.symbol_kind # str | None — SymbolKind.value for SYMBOL nodes
node.file_path   # str | None — relative_path for SYMBOL nodes
node.line_start  # int
node.line_end    # int

edge: Edge
edge.source          # str — source node id
edge.target          # str — target node id
edge.kind            # EdgeKind
edge.imported_names  # tuple[str, ...] — names imported (IMPORTS edges)
edge.base_name       # str — base class name (INHERITS edges)
```

#### `RepositoryGraph` (graph.py)

```python
from src.repository.graph import RepositoryGraph
from src.repository.graph import NodeKind, EdgeKind

graph = RepositoryGraph.build(index)

# Node queries
node  = graph.node("src/agent/agent.py")          # Node | None
files = graph.nodes(kind=NodeKind.FILE)            # list[Node]
syms  = graph.nodes(kind=NodeKind.SYMBOL)          # list[Node]

# Edge queries
all_edges   = graph.edges()                        # list[Edge]
imp_edges   = graph.edges(kind=EdgeKind.IMPORTS)   # list[Edge]
out_edges   = graph.edges_out("src/a.py", kind=EdgeKind.IMPORTS)
in_edges    = graph.edges_in("src/b.py", kind=EdgeKind.IMPORTS)

# Neighbour traversal
deps        = graph.neighbors_out("src/a.py", kind=EdgeKind.IMPORTS)
importers   = graph.neighbors_in("src/b.py", kind=EdgeKind.IMPORTS)

# Algorithms
cycles      = graph.detect_cycles()               # list[list[str]] — circular import groups
path        = graph.shortest_path("src/a.py", "src/c.py", kind=EdgeKind.IMPORTS)
                                                   # list[str] | None
fwd_deps    = graph.transitive_dependencies("src/a.py")   # sorted list of file paths
rev_deps    = graph.transitive_dependents("src/b.py")     # sorted list of file paths
result      = graph.impact("src/b.py")            # ImpactResult

# Statistics
print(graph.stats())   # GraphStats
print(len(graph))      # total node count
"src/a.py" in graph    # bool
```

#### `ImpactResult` (graph.py)

```python
from src.repository.graph import ImpactResult

r: ImpactResult
r.changed_file           # str   — the file that was changed
r.direct_dependents      # list[str] — files with a direct IMPORTS edge
r.transitive_dependents  # list[str] — all reachable dependents
r.affected_symbol_count  # int  — symbols in all dependent files
r.risk_level             # "LOW" | "MEDIUM" | "HIGH"
```

#### `GraphStats` (graph.py)

```python
from src.repository.graph import GraphStats

s: GraphStats
s.node_count          # int
s.edge_count          # int
s.file_node_count     # int
s.symbol_node_count   # int
s.imports_edge_count  # int
s.defines_edge_count  # int
s.contains_edge_count # int
s.inherits_edge_count # int
s.cycle_count         # int
s.build_duration_ms   # float
```

### `base_classes` — SymbolDef extension (Phase 5)

Phase 5 adds one field to `SymbolDef`:

| Field | Type | Default | Populated by |
|---|---|---|---|
| `base_classes` | `list[str]` | `[]` | Python parser |

The Python parser extracts base class names from `ClassDef.bases`:

| Source | `base_classes` value |
|---|---|
| `class Foo:` | `[]` |
| `class Foo(Base):` | `["Base"]` |
| `class Foo(Base, Mixin):` | `["Base", "Mixin"]` |
| `class Foo(module.Base):` | `["module.Base"]` |

The field defaults to `[]` so all existing parsers and tests are backward-compatible.

---

## 9. Future Phases (roadmap)

| Phase | Module | Status | Goal |
|---|---|---|---|
| 5 | `graph.py` | **Complete** | Reference graph — import graph, call graph, inheritance |
| 6 | `context_builder.py` | Planned | Automatically select most-relevant files for a task |
| 7 | `search.py` | Planned | Cross-repository symbol and text search |
| 8 | `ranking.py` | Planned | Result ranking by reference count, proximity, importance |
| 9 | `repository.py` | Planned | High-level `Repository` facade — `scan()`, `search()`, `index()` |
| 10 | — | Planned | Multi-language readiness audit |

Each phase adds one module.  No existing module is rewritten.  Public
surfaces are extended, never broken.

---

## 10. Public API Reference

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
    # Phase 4
    RepositoryIndex,
    SymbolEntry,
    IndexStats,
    # Phase 5
    NodeKind,
    EdgeKind,
    Node,
    Edge,
    ImpactResult,
    GraphStats,
    RepositoryGraph,
)

# Parser framework (Phase 2+)
from src.repository.parsers import (
    BaseParser,
    ParserRegistry,
    ParseResult,
    SymbolDef,
    SymbolKind,
)

# Python parser (Phase 3+)
from src.repository.parsers.python_parser import PythonParser

# Index (Phase 4+)
from src.repository.index import RepositoryIndex, SymbolEntry, IndexStats

# Graph (Phase 5+)
from src.repository.graph import (
    NodeKind, EdgeKind, Node, Edge,
    ImpactResult, GraphStats, RepositoryGraph,
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
results  = registry.parse_many(py_files)
for pr in results:
    classes = [s for s in pr.symbols if s.kind.name == "CLASS"]
    print(f"  {pr.file_info.relative_path}: {len(classes)} class(es)")

# 4. Index (Phase 4+)
from src.repository.index import RepositoryIndex
index = RepositoryIndex.build(results)

entry = index.lookup_qualified("MyClass.my_method")
if entry:
    print(f"Found at {entry.relative_path} L{entry.symbol.line_start}")

managers = index.search("*Manager")
users    = index.files_importing("asyncio")
print(index.stats())

# 5. Graph (Phase 5+)
from src.repository.graph import RepositoryGraph

graph = RepositoryGraph.build(index)

cycles  = graph.detect_cycles()
impact  = graph.impact("src/agent/agent.py")
print(f"Risk: {impact.risk_level}, transitive dependents: {len(impact.transitive_dependents)}")
print(graph.stats())
```

---

## 11. Extension Guide

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

## 12. Performance Notes

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

## 13. Design Decisions

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
