# 02 — Coding Standards

**Pearl AI Coding Agent — Engineering Standards Series**  
Document owner: Lead Software Architect  
Applies to: Every sprint, every contributor, every line of code  
Status: Authoritative — changes require architect sign-off

---

## Purpose

This document defines the coding standards for the Pearl AI Coding Agent. It governs
every line of Python and TypeScript written in this codebase. Standards exist not to
enforce uniformity for its own sake, but to ensure that every file in this repository
reads as if it were written by the same deliberate, experienced author.

Rules in this document are **binding**. "It works" is not a sufficient defence for a
standards violation. Every rule states a rationale; challenge the rule rather than the
enforcement.

---

## Table of Contents

1. [Python Coding Standards](#1-python-coding-standards)
2. [TypeScript Coding Standards](#2-typescript-coding-standards)
3. [Naming Conventions](#3-naming-conventions)
4. [File Organization](#4-file-organization)
5. [Error Handling](#5-error-handling)
6. [Logging Standards](#6-logging-standards)
7. [Async Programming](#7-async-programming)
8. [Dependency Management](#8-dependency-management)
9. [Design Patterns](#9-design-patterns)
10. [Anti-Patterns](#10-anti-patterns)
11. [Documentation Requirements](#11-documentation-requirements)
12. [Testing Expectations](#12-testing-expectations)
13. [Code Review Rules](#13-code-review-rules)
14. [Refactoring Rules](#14-refactoring-rules)
15. [Performance Considerations](#15-performance-considerations)
16. [Security Considerations](#16-security-considerations)
17. [Common Mistakes](#17-common-mistakes)
18. [Coding Standards Review Checklist](#18-coding-standards-review-checklist)
19. [AI-Assisted Development Rules](#19-ai-assisted-development-rules)
20. [Technical Debt Policy](#20-technical-debt-policy)
21. [Feature Flag Guidelines](#21-feature-flag-guidelines)
22. [Engineering Metrics](#22-engineering-metrics)
23. [Public API Stability](#23-public-api-stability)
24. [Git Commit Message Standards](#24-git-commit-message-standards)
25. [Benchmark Policy](#25-benchmark-policy)

---

## 1. Python Coding Standards

### 1.1 Formatting and Style Enforcement

**Rule PY-1:** All Python code MUST be formatted with `ruff format` and pass
`ruff check` with zero warnings before merging. No manual override flags (`# noqa`,
`# fmt: skip`) are permitted without an accompanying comment explaining the specific
reason.

```bash
ruff format src/ tests/
ruff check src/ tests/
```

**Rule PY-2:** Maximum line length is **88 characters** (ruff's default, matching
Black). The 88-character limit is a pragmatic balance between readability and
horizontal scroll. Exceptions: string literals that cannot be broken without changing
semantics, and long URLs in comments.

**Rule PY-3:** Every Python module MUST begin with:

```python
from __future__ import annotations
```

This enables PEP 563 postponed evaluation of annotations, allowing forward references
without string quoting and keeping behavior consistent across Python 3.10+.

### 1.2 Type Annotations

**Rule PY-4:** ALL public functions and methods MUST have complete type annotations —
every parameter and the return type. This is non-negotiable.

```python
# CORRECT
def read_file(path: str, encoding: str = "utf-8") -> str:
    ...

# VIOLATION — missing annotations
def read_file(path, encoding="utf-8"):
    ...
```

**Rule PY-5:** Private functions (prefixed `_`) MUST also be fully annotated unless
they are trivial one-liners whose types are obvious from context. When in doubt,
annotate.

**Rule PY-6:** Use `X | Y` union syntax (PEP 604), not `Union[X, Y]`.

```python
# CORRECT
def load(path: str | None = None) -> dict[str, str] | None: ...

# VIOLATION — old-style Union
def load(path: Optional[str] = None) -> Optional[Dict[str, str]]: ...
```

**Rule PY-7:** Prefer built-in generic types over `typing` equivalents for annotations:
`list[str]` not `List[str]`, `dict[str, int]` not `Dict[str, int]`,
`tuple[str, ...]` not `Tuple[str, ...]`. These are available without imports in
Python 3.9+ (and deferred under `from __future__ import annotations` in 3.7+).

**Rule PY-8:** Use `Any` only as a last resort. Every use of `typing.Any` MUST have
a comment explaining why a more specific type is not possible.

```python
# CORRECT — Any justified with explanation
def execute(
    self,
    tool_name: str,
    **kwargs: Any,  # tool parameters vary by tool; see ToolRegistry for per-tool schemas
) -> Any:  # return type is tool-specific
    ...
```

**Rule PY-9:** Use `TypeAlias` for type aliases that would otherwise be ambiguous:

```python
from typing import TypeAlias

ToolResult: TypeAlias = str | dict[str, Any] | list[Any]
CancelCheck: TypeAlias = Callable[[], bool]
```

### 1.3 Imports

**Rule PY-10:** Imports MUST be organized in three groups, separated by blank lines,
in this order:
1. Standard library imports
2. Third-party imports
3. Local (`src.*`) imports

Within each group, imports are sorted alphabetically. `ruff check --fix` enforces this.

```python
# CORRECT
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable

import httpx

from src.config.settings import Settings
from src.tools.metadata import tool
```

**Rule PY-11:** Use `from module import name` for specific names. Use `import module`
only when the module name itself is used (e.g., `import json; json.dumps(...)`).

**Rule PY-12:** Never use wildcard imports (`from module import *`). They make the
source of any name ambiguous to both humans and static analysis tools.

**Rule PY-13:** Circular imports are a design smell. If two modules need each other,
one of them is at the wrong layer. Resolve the dependency direction before proceeding
(see `01_ARCHITECTURE_RULES.md`, Section 2).

### 1.4 Functions and Methods

**Rule PY-14:** Functions MUST do one thing. A function that does two unrelated things
is two functions waiting to be separated. The test is: can you write a one-sentence
description of what this function does without using "and"?

**Rule PY-15:** Function length: prefer functions under 40 lines. A function that
exceeds 60 lines is almost always doing too much and MUST be refactored before review.

**Rule PY-16:** Parameter count: prefer four or fewer parameters. When a function
requires more than four, introduce a configuration dataclass:

```python
# VIOLATION — 7 parameters is too many
def run(prompt, max_iter, max_replans, on_progress, checkpoints, memory, cancel):
    ...

# CORRECT — group related parameters into a config object
@dataclass
class RunConfig:
    max_iterations: int = DEFAULT_MAX_ITERATIONS
    max_replans: int = DEFAULT_MAX_REPLANS
    on_progress: Callable[[ProgressEvent], None] | None = None

def run(prompt: str, config: RunConfig | None = None) -> ExecutionReport:
    config = config or RunConfig()
    ...
```

**Rule PY-17:** Default parameter values MUST be immutable. Never use a mutable
default (list, dict, set) — Python creates the default once at function definition
time and reuses the same object across all calls.

```python
# VIOLATION — mutable default; same list shared across all calls
def process(items: list[str] = []) -> list[str]: ...

# CORRECT
def process(items: list[str] | None = None) -> list[str]:
    if items is None:
        items = []
    ...
```

**Rule PY-18:** Use keyword-only arguments (after `*`) for parameters that have no
natural positional meaning or that could be confused with each other:

```python
# CORRECT — caller must name encoding and errors explicitly
def read_file(path: str, *, encoding: str = "utf-8", errors: str = "strict") -> str:
    ...
```

**Rule PY-19:** Prefer `pathlib.Path` over `os.path` and string manipulation for all
file path operations. `Path` is safer, more readable, and cross-platform.

```python
# CORRECT
config_path = Path(__file__).resolve().parent / "config.toml"

# VIOLATION
config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.toml")
```

### 1.5 Classes

**Rule PY-20:** Use `@dataclass` for data-holding classes. Use `@dataclass(frozen=True)`
for immutable value objects. Avoid writing `__init__` by hand when a dataclass would
do the same job.

```python
# CORRECT
@dataclass(frozen=True)
class ToolCall:
    tool_name: str
    kwargs: dict[str, Any]
    reasoning: str = ""
```

**Rule PY-21:** Use `@dataclass` or `TypedDict` for structured return values instead
of bare tuples or generic dicts. A caller that receives `result["path"]` has no
static guarantee that "path" exists; a caller that receives `result.path` does.

**Rule PY-22:** Class length: prefer classes under 200 lines. A class that exceeds
300 lines almost always has hidden abstraction boundaries. Identify them and split.

**Rule PY-23:** Prefer composition over inheritance. Inheritance is appropriate for:
(a) framework hooks (e.g., `BaseProvider`), (b) exception hierarchies. It is NOT
appropriate for code reuse where a helper function or composition would suffice.

**Rule PY-24:** Abstract base classes MUST use `abc.ABC` and `@abc.abstractmethod`.
A class that intends to be abstract but doesn't declare it allows silent instantiation
of incomplete implementations.

```python
# CORRECT
from abc import ABC, abstractmethod

class BaseProvider(ABC):
    @abstractmethod
    def generate(self, prompt: str, **kwargs: Any) -> str: ...

    @abstractmethod
    def generate_json(self, prompt: str, **kwargs: Any) -> dict[str, Any]: ...
```

### 1.6 Module-Level Code

**Rule PY-25:** Module-level code MUST be limited to: imports, `__all__` declarations,
type aliases, module-level constants, `@dataclass` definitions, and function/class
definitions. No executable logic runs at import time except logging setup and constant
initialization.

**Rule PY-26:** Module-level mutable globals are forbidden except for:
- Logger instances: `logger = logging.getLogger(__name__)`
- Module-level caches that have explicit ownership documented (see
  `01_ARCHITECTURE_RULES.md`, Rule S-3)

```python
# CORRECT — logger is the expected pattern
logger = logging.getLogger(__name__)

# VIOLATION — mutable default at module level leaks state between tests
_active_sessions: list[Session] = []
```

**Rule PY-27:** Constants at module level MUST use `SCREAMING_SNAKE_CASE` and be
type-annotated:

```python
MAX_SEARCH_RESULTS: int = 200
DEFAULT_ENCODING: str = "utf-8"
IGNORED_DIRS: frozenset[str] = frozenset({".git", "__pycache__", "node_modules"})
```

---

## 2. TypeScript Coding Standards

### 2.1 Formatting and Style Enforcement

**Rule TS-1:** All TypeScript code MUST be formatted with Prettier and pass ESLint
with zero errors before merging. Prettier configuration is authoritative; ESLint
auto-fixable rules are applied as part of the format step.

**Rule TS-2:** `tsconfig.json` MUST include `"strict": true`. This enables the full
strict mode flag set: `strictNullChecks`, `noImplicitAny`, `strictFunctionTypes`,
`strictPropertyInitialization`, and others. No project-wide relaxation of strict mode
is permitted.

**Rule TS-3:** Maximum line length is **100 characters** for TypeScript (slightly
wider than Python to accommodate common TypeScript patterns like long generic types).

### 2.2 Types and Interfaces

**Rule TS-4:** Use `interface` for object shapes that consumers may extend or implement.
Use `type` for union types, intersection types, and function type aliases.

```typescript
// CORRECT — interface for extensible shape
interface ToolCallResult {
  isError: boolean;
  content: Array<{ type: string; text: string }>;
}

// CORRECT — type for union
type StopReason = "completed" | "cancelled" | "awaiting_approval" | "fatal_error" | "max_iterations";

// CORRECT — type for function alias
type ProgressHandler = (event: ProgressEvent) => void;
```

**Rule TS-5:** `any` is forbidden. Use `unknown` when a type cannot be known and
narrow it before use. Use `never` to assert exhaustiveness.

```typescript
// VIOLATION
function parseResult(data: any): string { ... }

// CORRECT
function parseResult(data: unknown): string {
  if (typeof data !== "string") {
    throw new Error(`Expected string, got ${typeof data}`);
  }
  return data;
}
```

**Rule TS-6:** Never use non-null assertion (`!`) on a value that could legitimately
be null or undefined. Use optional chaining (`?.`) and nullish coalescing (`??`)
instead.

```typescript
// VIOLATION
const text = response!.content[0]!.text;

// CORRECT
const text = response?.content?.[0]?.text ?? "";
```

**Rule TS-7:** Prefer `readonly` on array and object properties that should not be
mutated after construction:

```typescript
interface ExecutionReport {
  readonly steps: readonly ExecutionStep[];
  readonly stopReason: StopReason;
  readonly succeeded: boolean;
}
```

**Rule TS-8:** Use exhaustiveness checks in `switch` statements over discriminated
unions:

```typescript
function handleStopReason(reason: StopReason): string {
  switch (reason) {
    case "completed":        return "Run completed successfully.";
    case "cancelled":        return "Run was cancelled.";
    case "awaiting_approval": return "Awaiting patch approval.";
    case "fatal_error":      return "Run failed with an unrecoverable error.";
    case "max_iterations":   return "Run hit the iteration limit.";
    default: {
      const _exhaustive: never = reason;  // compile error if a case is missing
      return _exhaustive;
    }
  }
}
```

### 2.3 Imports and Module Structure

**Rule TS-9:** Use ES module syntax (`import`/`export`). Never use `require()` in new
code.

**Rule TS-10:** Organize imports in three groups:
1. Node.js built-ins (prefixed `node:`)
2. Third-party packages
3. Local imports (relative paths)

```typescript
// CORRECT
import { spawn } from "node:child_process";
import { EventEmitter } from "node:events";

import * as vscode from "vscode";

import { PearlClient } from "./pearl-client";
import type { ProgressEvent } from "./types";
```

**Rule TS-11:** Import types with `import type` when the import is used only as a
type (not as a value). This ensures the import is erased at compile time and doesn't
create runtime side effects.

```typescript
// CORRECT
import type { ExecutionReport } from "./types";

// VIOLATION — ProgressEvent is only used as a type, not as a value
import { ProgressEvent } from "./types";
```

### 2.4 Functions and Classes

**Rule TS-12:** Prefer `function` declarations over `const fn = () =>` for top-level
functions. Arrow functions should be reserved for callbacks and closures where `this`
binding matters.

**Rule TS-13:** All public class members MUST have explicit access modifiers
(`public`, `private`, `protected`, `readonly`). Default visibility (no modifier) is
not permitted — it is ambiguous and non-obvious.

```typescript
// CORRECT
class PearlExtension {
  private readonly client: PearlClient;
  private statusBar: vscode.StatusBarItem;
  public readonly version: string;

  constructor(context: vscode.ExtensionContext) { ... }

  public async activate(): Promise<void> { ... }
  private handleProgress(event: ProgressEvent): void { ... }
}
```

**Rule TS-14:** Avoid class when a function or plain object would suffice. A class
that has no state (only static methods) should be a module-level function. A class
with one method should be a function.

### 2.5 Extension-specific Rules

**Rule TS-15:** VS Code API calls that return `Thenable` MUST be awaited or returned.
Floating promises in the VS Code extension context are silently swallowed by the
extension host and produce unpredictable behavior.

```typescript
// VIOLATION — floating promise
vscode.window.showInformationMessage("Pearl ready.");

// CORRECT
await vscode.window.showInformationMessage("Pearl ready.");
// or
void vscode.window.showInformationMessage("Pearl ready."); // explicit "intentional fire-and-forget"
```

**Rule TS-16:** All VS Code disposables MUST be added to `context.subscriptions`.
Unregistered disposables leak memory and event listeners across the extension's
lifetime.

```typescript
// CORRECT
const disposable = vscode.commands.registerCommand("pearl.run", handler);
context.subscriptions.push(disposable);
```

**Rule TS-17:** MCP messages sent over stdio MUST be newline-delimited JSON. The
extension MUST buffer incoming data and split on `\n` before parsing. Parsing
incomplete frames produces silent JSON errors.

---

## 3. Naming Conventions

### 3.1 Python Naming

| Entity | Convention | Example |
|---|---|---|
| Module | `snake_case` | `repo_tools.py`, `patch_manager.py` |
| Package | `snake_case` | `src/agent/`, `src/llm/` |
| Class | `PascalCase` | `AutonomousExecutor`, `PatchManager` |
| Exception | `PascalCase` + `Error` suffix | `ToolExecutionError`, `CheckpointError` |
| Function | `snake_case` | `build_registry`, `refresh_indexed_file` |
| Method | `snake_case` | `run_autonomous`, `approve` |
| Public constant | `SCREAMING_SNAKE_CASE` | `MAX_SEARCH_RESULTS`, `DEFAULT_ENCODING` |
| Private function/method | `_leading_underscore` | `_ensure_within_workspace`, `_build_prompt` |
| Private constant | `_SCREAMING_SNAKE_CASE` | `_INDEX_CACHE`, `_ACTIVE_PATCH_MANAGER` |
| Type alias | `PascalCase` | `CancelCheck`, `ToolResult` |
| `TypeVar` | `PascalCase`, single letter or short name | `T`, `ProviderT` |
| Test function | `test_<what>_<condition>` | `test_approve_writes_staged_patches` |
| Fixture | `<noun>` (noun, not verb) | `registry`, `executor`, `tmp_workspace` |

**Rule N-1:** Names MUST describe what something IS, not how it is implemented.
`result` is better than `return_value`. `staged_paths` is better than `list_of_paths`.

**Rule N-2:** Boolean variables and properties MUST use an `is_`, `has_`, `can_`,
`should_` prefix:

```python
# CORRECT
is_awaiting_approval: bool
has_active_patch_manager: bool
can_replan: bool

# VIOLATION
awaiting_approval: bool
active_patch_manager: bool
```

**Rule N-3:** Prefer full words over abbreviations. Abbreviations are permitted only
for universally understood shorthands (`llm`, `mcp`, `url`, `id`, `idx`).

```python
# VIOLATION
def proc_req(req: dict) -> dict: ...

# CORRECT
def process_request(request: dict[str, Any]) -> dict[str, Any]: ...
```

### 3.2 TypeScript Naming

| Entity | Convention | Example |
|---|---|---|
| Variable | `camelCase` | `progressEvent`, `toolCallResult` |
| Function | `camelCase` | `handleProgress`, `sendRequest` |
| Class | `PascalCase` | `PearlClient`, `ChatPanel` |
| Interface | `PascalCase` | `ExecutionReport`, `ToolCallResult` |
| Type alias | `PascalCase` | `StopReason`, `ProgressHandler` |
| Enum | `PascalCase` | `LogLevel`, `ConnectionState` |
| Enum member | `PascalCase` | `LogLevel.Warning` |
| Private member | `#camelCase` (native private) | `#client`, `#outputChannel` |
| Constant | `SCREAMING_SNAKE_CASE` | `MAX_RECONNECT_ATTEMPTS` |
| File | `kebab-case.ts` | `pearl-client.ts`, `chat-panel.ts` |

**Rule N-4:** TypeScript interfaces MUST NOT be prefixed with `I`. This prefix is a
relic of older conventions and adds noise without information.

```typescript
// VIOLATION
interface IToolCallResult { ... }

// CORRECT
interface ToolCallResult { ... }
```

### 3.3 Tool Names

**Rule N-5:** Tool function names MUST follow the `verb_noun` pattern. The verb
describes the action; the noun describes what is acted upon.

| Action | Noun | Example |
|---|---|---|
| `read_` | target object | `read_file` |
| `write_` | target object | `write_file` |
| `find_` | what is found | `find_symbol`, `find_references` |
| `create_` | what is created | `create_file` |
| `delete_` | what is deleted | `delete_file` |
| `list_` | what is listed | `list_directory` |
| `search_` | what is searched | `search_text` |
| `replace_` | what is replaced | `replace_in_file`, `replace_function` |
| `index_` | what is indexed | `index_repository` |
| `explain_` | what is explained | `explain_file` |
| `summarize_` | what is summarized | `summarize_project` |
| `execute_` | what executes | `execute_shell` |
| `run_` | what is run | `run_python` |
| `git_` | git action | `git_status`, `git_diff` |

**Rule N-6:** Tool names are part of the MCP protocol. Renaming a tool is a breaking
change that requires a deprecation cycle. Choose names with care.

### 3.4 MCP Method Names

**Rule N-7:** Standard MCP method names follow the `resource/verb` pattern:
`tools/list`, `tools/call`, `initialize`, `shutdown`.

**Rule N-8:** Pearl extension methods follow `pearl/camelCaseVerb`:
`pearl/runAutonomous`, `pearl/approvePatches`, `pearl/rejectPatches`,
`pearl/cancelRun`.

**Rule N-9:** New Pearl methods MUST start with `pearl/` and use a verb that clearly
indicates the action. Nouns-only (`pearl/status`) and vague verbs (`pearl/update`)
are not permitted.

### 3.5 Configuration Keys

**Rule N-10:** All configuration constants in `Settings` MUST use `SCREAMING_SNAKE_CASE`
and be grouped by concern with a blank line between groups:

```python
class Settings:
    # LLM provider settings
    LLM_PROVIDER: str = "ollama"
    OLLAMA_BASE_URL: str = "http://localhost:11434/v1"
    DEFAULT_MODEL: str = "qwen2.5-coder:14b"

    # Execution limits
    MAX_ITERATIONS: int = 20
    MAX_REPLANS: int = 3
    MAX_CONTEXT_TOKENS: int = 32_000

    # Search and index
    MAX_SEARCH_RESULTS: int = 200
    MAX_FILE_SIZE_BYTES: int = 512_000
```

---

## 4. File Organization

### 4.1 Python Module Structure

Every Python module MUST follow this internal structure, in this order:

```
1. Module docstring (required for all public modules)
2. from __future__ import annotations
3. Standard library imports
4. Third-party imports
5. Local imports
6. __all__ (if the module has a public API)
7. Module-level constants
8. Type aliases
9. Exception definitions
10. Dataclass/Protocol definitions
11. Class definitions
12. Public functions
13. Private functions
```

**Rule FO-1:** Module docstrings MUST be present on all public modules (anything not
prefixed `_`). The docstring should state in one or two sentences what the module
does, not how.

```python
"""
Repository intelligence tools.

Provides file search, symbol lookup, reference finding, and project summary
capabilities backed by a single session-scoped in-memory index.
"""
```

**Rule FO-2:** `__all__` MUST be defined for any module that has a public API and is
imported by other modules. It makes the public surface explicit and prevents accidental
re-exports.

### 4.2 TypeScript Module Structure

Every TypeScript module MUST follow this internal structure:

```
1. Node.js built-in imports
2. Third-party imports
3. Local imports (type imports last within each group)
4. Module-level constants
5. Type/interface definitions
6. Class definitions
7. Exported functions
8. Private (non-exported) functions
```

### 4.3 File Naming

**Rule FO-3:** Python file names MUST be `snake_case.py`. One module = one file.
Exception: `__init__.py` for package entry points (MUST be minimal — only re-exports).

**Rule FO-4:** TypeScript file names MUST be `kebab-case.ts`. Test files are
`kebab-case.test.ts` in the same directory as the source file.

**Rule FO-5:** Test files MUST mirror the structure of the source tree. If
`src/agent/executor.py` is the source, `tests/test_executor.py` is its test.

### 4.4 Directory Layout Rules

**Rule FO-6:** No new top-level directories inside `src/` without architect sign-off.
The six-layer structure defined in `01_ARCHITECTURE_RULES.md` Section 2 is the
authoritative layout. New functionality goes into an existing layer, or the layer
structure itself must be extended via ADR.

**Rule FO-7:** Test utilities (fixtures, factories, helpers) MUST live in `tests/`
under a `conftest.py` or `helpers/` subdirectory. They MUST NOT be imported by
`src/` code.

**Rule FO-8:** One-off scripts, benchmarks, and experiments MUST live in `scripts/`
or `benchmarks/`. They MUST NOT be added to `src/`.

---

## 5. Error Handling

### 5.1 Error Classification

Pearl uses a layered error taxonomy. Each layer handles the errors it can resolve and
propagates the rest upward.

| Error class | Raised by | Meaning |
|---|---|---|
| `ValueError` | Tools | Invalid input (bad path, malformed argument) |
| `FileNotFoundError` | Tools | File or directory does not exist |
| `PermissionError` | Tools | OS-level permission denied |
| `WorkspaceBoundaryError` | Tools | Path escapes the workspace root |
| `subprocess.TimeoutExpired` | `execute_shell` | Shell command exceeded time limit |
| `ToolNotFoundError` | Dispatcher | Requested tool is not registered |
| `ToolExecutionError` | Dispatcher | Wraps any exception raised by a tool |
| `LLMCancelled` | LLM client | Cancellation was requested during an LLM call |
| `LLMProviderError` | LLM client | Provider returned an unexpected response |
| `MCPProtocolError` | MCP server | Malformed JSON-RPC request |
| `CheckpointError` | CheckpointManager | Shadow-repo operation failed |

### 5.2 Python Error Rules

**Rule ERR-1:** Tools MUST raise the most specific available exception. Use Python
built-ins (`ValueError`, `FileNotFoundError`, `PermissionError`) when they are
semantically correct. Use Pearl's custom exceptions when a built-in doesn't carry
enough context.

**Rule ERR-2:** Error messages MUST be actionable. They appear in the LLM's plan
evaluation and in the user-facing output. A message that answers "what happened and
what should I do about it" is correct. A message that says only "Error" is not.

```python
# VIOLATION
raise ValueError("invalid path")

# CORRECT
raise ValueError(
    f"Path {path!r} escapes the workspace root {workspace_root!r}. "
    "All file operations must stay within the workspace."
)
```

**Rule ERR-3:** Never catch `Exception` broadly in tool code. Catch specific
exceptions you know how to handle. Let unknown exceptions propagate to the dispatcher,
which wraps them for structured reporting.

```python
# VIOLATION — too broad; hides bugs
try:
    content = path.read_text()
except Exception:
    return ""

# CORRECT
try:
    content = path.read_text(encoding=encoding, errors=errors)
except FileNotFoundError:
    raise FileNotFoundError(f"File not found: {path}")
except PermissionError:
    raise PermissionError(f"Permission denied reading: {path}")
```

**Rule ERR-4:** Never use `return None` to signal an error. Callers that forget to
check for `None` produce silent incorrect behavior. Raise an exception with a clear
message instead.

```python
# VIOLATION
def find_symbol(name: str) -> dict | None:
    ...
    return None  # not found? caller must remember to check

# CORRECT
def find_symbol(name: str) -> dict:
    ...
    raise ValueError(f"Symbol {name!r} not found in the repository index.")
```

**Rule ERR-5:** `sys.exit()` and `SystemExit` MUST NOT be raised from tool or agent
code. They terminate the process without cleanup. Raise an appropriate exception and
let the entry point (`main()` or the MCP server) handle shutdown.

### 5.3 Error Handling Decision Tree

```
An exception has occurred. Where are you?
│
├─ In a tool function
│   ├─ Is it an expected failure (bad input, file not found, permission)?
│   │   └─ Raise the most specific built-in exception with an actionable message.
│   └─ Is it unexpected (bug in the tool itself)?
│       └─ Let it propagate — don't catch it here.
│
├─ In the Dispatcher
│   └─ Catch all exceptions from tool execution.
│       Wrap as ToolExecutionError. Record in ExecutionStep.error.
│
├─ In the Executor
│   ├─ Is it LLMCancelled?
│   │   └─ Finalize report as stop_reason="cancelled". Do not re-raise.
│   ├─ Is it ToolExecutionError?
│   │   └─ Record in the step. Attempt replan if replan budget allows.
│   └─ Is it any other exception?
│       └─ Log at ERROR. Finalize report as stop_reason="fatal_error".
│
├─ In the MCP Server handler
│   └─ Catch all exceptions.
│       Return JSON-RPC error object. Never let exception escape to stdio framing.
│
└─ In the CLI main()
    └─ Catch all exceptions.
        Print: f"\nError: {exc}\n". No traceback. No sys.exit with non-zero codes.
```

### 5.4 TypeScript Error Rules

**Rule ERR-6:** Never swallow errors with empty `catch` blocks. At minimum, log the
error.

```typescript
// VIOLATION
try {
  await client.send(request);
} catch {}

// CORRECT
try {
  await client.send(request);
} catch (error) {
  this.outputChannel.appendLine(`[ERROR] Failed to send request: ${error}`);
  throw error;
}
```

**Rule ERR-7:** Use `instanceof` checks to discriminate error types. Do not rely on
`error.message` string matching for control flow.

```typescript
// VIOLATION
if (error.message.includes("connection refused")) { ... }

// CORRECT
if (error instanceof ConnectionRefusedError) { ... }
```

**Rule ERR-8:** Surface MCP protocol errors to the user via VS Code's notification
API with a clear message and a suggested action.

---

## 6. Logging Standards

### 6.1 Logger Setup

**Rule LOG-1:** Every Python module MUST create its logger as a module-level
constant using its own `__name__`:

```python
logger = logging.getLogger(__name__)
```

This produces hierarchical logger names (`src.agent.executor`, `src.tools.repo_tools`)
that can be filtered independently.

**Rule LOG-2:** Never configure the root logger in library code. Root logger
configuration belongs exclusively in `src/main.py` (for the CLI) and
`src/mcp/__main__.py` (for the MCP server). Library code only calls `getLogger()`.

### 6.2 Log Levels

| Level | Use | Example |
|---|---|---|
| `DEBUG` | Detailed trace, off by default | Token counts, raw LLM prompts, step-by-step iteration |
| `INFO` | Lifecycle, state transitions, key actions | Agent init, tool execution start/end, checkpoint saved |
| `WARNING` | Recoverable failures | Progress callback failed, checkpoint skipped, index stale |
| `ERROR` | Non-recoverable failures | LLM provider error, plan parse failure, fatal executor error |

**Rule LOG-3:** Log initialization of every major component at `INFO`:

```python
logger.info("AutonomousExecutor initialized (max_iterations=%d, max_replans=%d)",
            max_iterations, max_replans)
```

**Rule LOG-4:** Log every tool execution start at `DEBUG` and every completion
(success or failure) at `INFO`:

```python
logger.debug("Executing tool: %s with kwargs: %s", tool_name, list(kwargs.keys()))
logger.info("Tool %s completed in %dms", tool_name, elapsed_ms)
```

### 6.3 Log Format Rules

**Rule LOG-5:** Use `%s`-style formatting, not f-strings, for log calls. This defers
string interpolation to when the message is actually emitted (which doesn't happen at
`DEBUG` level unless debug logging is enabled).

```python
# CORRECT — lazy interpolation
logger.info("Processing file: %s (%d bytes)", path, size)

# AVOID — always interpolates even if INFO is disabled
logger.info(f"Processing file: {path} ({size} bytes)")
```

**Rule LOG-6:** Include relevant context in log messages. A bare "Failed" is
useless. Include: what was being attempted, what specifically failed, and any IDs
that connect the log line to related entries.

```python
# VIOLATION
logger.error("Failed")

# CORRECT
logger.error("Plan generation failed on attempt %d/%d: %s", attempt, max_replans, exc)
```

### 6.4 What MUST NOT Appear in Logs

**Rule LOG-7:** File contents MUST NOT be logged at any level. Log the file path and
byte count:

```python
logger.debug("Read %d bytes from %s", len(content), path)  # CORRECT
logger.debug("File content: %s", content)                   # VIOLATION
```

**Rule LOG-8:** Raw LLM response text MUST NOT be logged at `INFO` or above. At
`DEBUG` level, truncate to the first 200 characters:

```python
logger.debug("LLM response (first 200 chars): %.200s", response)
```

**Rule LOG-9:** Credentials, API keys, and tokens MUST NEVER be logged at any level.
When logging settings that may contain secrets, log only the key name and whether
a value is set:

```python
logger.info("API key: %s", "***" if Settings.OPENAI_API_KEY else "(not set)")
```

**Rule LOG-10:** User prompts MUST be logged ONCE at `INFO` level by the agent
facade. They MUST NOT be logged again by the planner, executor, or any tool. Duplicate
logging of prompts pollutes the log without adding information.

---

## 7. Async Programming

### 7.1 Pearl's Concurrency Model

Pearl's Python backend is deliberately synchronous. It uses `threading.Event` for
cancellation and `contextvars.ContextVar` for thread-local state. The MCP server
processes one request at a time.

**Rule ASYNC-1:** Do NOT introduce `asyncio` into the Python backend without architect
sign-off. Mixing synchronous code with `asyncio` creates event loop ownership problems
that are difficult to diagnose. Pearl's current synchronous model is intentional.

**Rule ASYNC-2:** Blocking operations (file I/O, LLM API calls, subprocess execution)
MUST NOT run on threads that are not allowed to block. For the MCP server's stdio
reader thread, all work MUST be dispatched to worker threads.

### 7.2 Cancellation

**Rule ASYNC-3:** Any function that may run for more than a few hundred milliseconds
MUST accept a `cancel_check: Callable[[], bool] | None = None` parameter. If
`cancel_check()` returns `True`, the function MUST raise `LLMCancelled` (for LLM
calls) or stop and return a partial result (for other long operations).

```python
def generate(
    self,
    prompt: str,
    *,
    cancel_check: Callable[[], bool] | None = None,
    **kwargs: Any,
) -> str:
    for chunk in self._stream(prompt, **kwargs):
        if cancel_check is not None and cancel_check():
            raise LLMCancelled("Generation cancelled by caller.")
        result += chunk
    return result
```

**Rule ASYNC-4:** `cancel_check` MUST be checked at granular intervals — after each
token batch from a streaming LLM, after each file in a walk, after each iteration
in a planning loop. Checking only at the start of a long operation provides
cancellation in name only.

**Rule ASYNC-5:** Cancellation MUST be clean. When a cancellation is detected, any
staged patches or partial results MUST be discarded before returning. The system
MUST be left in a consistent state — not mid-write, not mid-plan.

### 7.3 TypeScript Async Rules

**Rule ASYNC-6:** ALL async operations in TypeScript MUST use `async/await`. Promise
chains (`.then()`/`.catch()`) are not permitted in new code. They are harder to read,
harder to debug, and make cancellation logic unwieldy.

```typescript
// VIOLATION
client.send(request)
  .then(response => handleResponse(response))
  .catch(error => handleError(error));

// CORRECT
try {
  const response = await client.send(request);
  handleResponse(response);
} catch (error) {
  handleError(error);
}
```

**Rule ASYNC-7:** Parallel async operations that don't depend on each other MUST use
`Promise.all()`:

```typescript
// VIOLATION — sequential when parallel is possible
const files = await listFiles(dir);
const symbols = await findSymbols(dir);

// CORRECT
const [files, symbols] = await Promise.all([
  listFiles(dir),
  findSymbols(dir),
]);
```

**Rule ASYNC-8:** Long-running operations in the extension MUST be cancellable via
VS Code's `CancellationToken`:

```typescript
async function runWithCancellation(
  prompt: string,
  token: vscode.CancellationToken,
): Promise<ExecutionReport> {
  return new Promise((resolve, reject) => {
    token.onCancellationRequested(() => {
      client.cancel();
      reject(new vscode.CancellationError());
    });
    client.runAutonomous(prompt).then(resolve, reject);
  });
}
```

---

## 8. Dependency Management

### 8.1 Python Dependencies

**Rule DEP-1:** All Python dependencies MUST be declared in `pyproject.toml` under
`[project.dependencies]`. No implicit dependencies.

**Rule DEP-2:** Development-only dependencies (testing, linting, type checking) MUST
be declared under `[project.optional-dependencies]` with a named group (`dev`, `test`,
`lint`).

**Rule DEP-3:** Every dependency MUST have a minimum version pin. Unpinned
dependencies (`requests` with no version) allow silent regressions when upstream
releases breaking changes.

```toml
# CORRECT
dependencies = [
    "httpx>=0.27.0",
    "openai>=1.40.0",
    "anthropic>=0.34.0",
]

# VIOLATION
dependencies = [
    "httpx",
    "openai",
]
```

**Rule DEP-4:** Do NOT pin to exact versions in `pyproject.toml` unless the
dependency has a known stability problem. Exact pins (`==1.40.0`) make security
updates harder and create conflicts in consumer environments.

**Rule DEP-5:** Before adding a new dependency, evaluate:
1. Is it actively maintained? (last commit within 12 months)
2. Does it have a security track record? (check CVE history)
3. Is it the right scope? (a 50-line function doesn't need a 2 MB library)
4. Does it already exist in the dependency tree transitively?

**Rule DEP-6:** Standard library is always preferred over a third-party equivalent.
If `pathlib` covers the use case, don't add `pathlib2`. If `json` covers parsing,
don't add `simplejson`.

### 8.2 TypeScript Dependencies

**Rule DEP-7:** All TypeScript dependencies MUST be declared in `package.json` with
`^` (caret) version ranges for minor-compatible updates. Exact locks are in
`package-lock.json` or `pnpm-lock.yaml`.

**Rule DEP-8:** VS Code API (`vscode`) is a peer dependency provided by the extension
host. It MUST be declared in `peerDependencies`, not `dependencies`.

**Rule DEP-9:** The `vscode-extension/` directory MUST NOT have dependencies that
duplicate Python backend functionality. The extension is a UI layer; business logic
belongs in the Python backend.

### 8.3 Adding a New Dependency

Decision tree for adding a dependency:

```
I need external functionality. Should I add a package?
│
├─ Does the standard library cover it?
│   └─ YES → Use the standard library. Do not add a package.
│
├─ Is it already transitively available?
│   └─ YES → Import it directly. Do not re-add it explicitly unless required.
│
├─ Is the functionality small enough to implement inline?
│   └─ YES (< 50 lines) → Implement it. Do not add a package.
│
├─ Is the package actively maintained and trustworthy?
│   └─ NO → Do not add it. Find an alternative or implement.
│
└─ YES to all above → Add it, minimum-version pinned. Document rationale in PR.
```

---

## 9. Design Patterns

### 9.1 Patterns in Use

Pearl uses a small, consistent set of patterns. New code SHOULD reach for these
before inventing alternatives.

#### Registry Pattern

Used by: `ToolRegistry`

A central store that maps names to callables. Allows runtime discovery without
hard-coded dispatch.

```python
registry = ToolRegistry()
registry.register(read_file)      # registers with the name from @tool decorator
registry.get_tool("read_file")    # retrieves the callable
registry.get_tools()              # returns all tool descriptions (for planner prompt)
```

**When to use:** When a set of named, interchangeable implementations must be
discovered at runtime.

#### Decorator Pattern

Used by: `@tool` metadata decorator

Attaches behavior (metadata) to a function without changing its signature.

```python
@tool(
    description="Read the contents of a UTF-8 text file.",
    parameters={"path": "str"},
    returns="str",
)
def read_file(path: str) -> str: ...
```

**When to use:** When you need to attach metadata or behavior to a function
without altering its interface.

#### Facade Pattern

Used by: `PearlAgent`

Provides a simplified interface to a complex subsystem (Planner + Executor + Dispatcher
+ Memory + Checkpoints).

```python
# User of the facade sees only a clean API
agent = PearlAgent(registry)
report = agent.run_autonomous(prompt)
if report.stop_reason == "awaiting_approval":
    report = agent.approve()
```

**When to use:** When a subsystem has a complex, multi-object API and callers
only need a subset of its capabilities.

#### Context Variable Pattern

Used by: `_ACTIVE_PATCH_MANAGER`, `_ACTIVE_COMMAND_APPROVER`

Thread-local state that write tools read to know whether they're inside an autonomous
execution context.

```python
_ACTIVE_PATCH_MANAGER: ContextVar[PatchManager | None] = ContextVar(
    "_ACTIVE_PATCH_MANAGER", default=None
)

def set_active_patch_manager(pm: PatchManager | None) -> None:
    _ACTIVE_PATCH_MANAGER.set(pm)

def get_active_patch_manager() -> PatchManager | None:
    return _ACTIVE_PATCH_MANAGER.get()
```

**When to use:** When a value must be "active" for the duration of a call tree
without being passed through every intermediate function.

#### Strategy Pattern

Used by: `BaseProvider` and its implementations (`OllamaProvider`,
`OpenAIProvider`, `AnthropicProvider`, etc.)

Interchangeable algorithms behind a common interface.

```python
class LLMClient:
    def __init__(self) -> None:
        self._provider: BaseProvider = _load_provider(Settings.LLM_PROVIDER)

    def generate(self, prompt: str, **kwargs: Any) -> str:
        return self._provider.generate(prompt, **kwargs)
```

**When to use:** When the same operation has multiple implementations that must
be interchangeable at runtime.

#### Observer Pattern

Used by: `on_progress` callbacks

Decoupled notification from the executor to the UI layer without a direct dependency.

```python
executor = AutonomousExecutor(
    ...,
    on_progress=lambda event: output_channel.appendLine(event.message),
)
```

**When to use:** When an event producer (executor) must notify multiple consumers
(UI, logs, tests) without knowing who they are.

### 9.2 Pattern Decision Tree

```
I need to reuse behavior across multiple objects/modules. Which pattern?
│
├─ Is the behavior metadata attached to a function?
│   └─ YES → Decorator pattern.
│
├─ Is the behavior an interchangeable algorithm with a common interface?
│   └─ YES → Strategy pattern.
│
├─ Do I need to notify listeners about events without coupling to them?
│   └─ YES → Observer pattern (callback / event emitter).
│
├─ Do I need to hide a complex subsystem behind a clean interface?
│   └─ YES → Facade pattern.
│
├─ Do I need to discover implementations by name at runtime?
│   └─ YES → Registry pattern.
│
└─ Do I need thread-scoped ambient state without parameter threading?
    └─ YES → Context Variable pattern. (Justify with a comment — it is the
             least obvious choice and must be auditable.)
```

---

## 10. Anti-Patterns

### 10.1 Python Anti-Patterns

#### The Returning None on Failure Anti-Pattern

```python
# ANTI-PATTERN — callers forget to check
def find_tool(name: str) -> Tool | None:
    return self._tools.get(name)

caller_code = registry.find_tool("create_file")
caller_code.execute(...)  # AttributeError: NoneType has no attribute execute
```

**Fix:** Raise `ToolNotFoundError` with the tool name. Never return `None` to signal
"not found" in a code path that expects an answer.

#### The God Object Anti-Pattern

```python
# ANTI-PATTERN — one class does everything
class PearlSystem:
    def parse_request(self): ...
    def plan(self): ...
    def execute(self): ...
    def approve(self): ...
    def send_response(self): ...
    def index_repository(self): ...
    def save_checkpoint(self): ...
```

**Fix:** Decompose into single-responsibility classes. `MCPServer` handles protocol;
`PearlAgent` handles orchestration; `AutonomousExecutor` handles execution; etc.

#### The Boolean Flag Parameter Anti-Pattern

```python
# ANTI-PATTERN — flag parameter switches behavior
def read_file(path: str, raw: bool = False) -> str | bytes:
    if raw:
        return Path(path).read_bytes()
    return Path(path).read_text()
```

**Fix:** Two separate functions with clear names: `read_file(path)` and
`read_file_bytes(path)`. Boolean parameters that change what a function does are
two functions masquerading as one.

#### The Stringly-Typed API Anti-Pattern

```python
# ANTI-PATTERN — behavior controlled by string literals
agent.run("autonomous")
executor.finish("awaiting_approval")
report.check("stop_reason", "completed")
```

**Fix:** Use enums or typed constants. `StopReason` is a `Literal` type; its valid
values are checked by the type system, not by runtime string comparison.

#### The Exception Silencer Anti-Pattern

```python
# ANTI-PATTERN — exception swallowed without log
try:
    result = do_something()
except Exception:
    result = default_value
```

**Fix:** Either handle the exception specifically with a log at `WARNING`, or let
it propagate. Swallowing exceptions without a trace is the leading cause of
"it worked then suddenly stopped working" bugs.

### 10.2 TypeScript Anti-Patterns

#### The Callback Hell Anti-Pattern

```typescript
// ANTI-PATTERN
client.connect(url, () => {
  client.initialize(caps, (initResult) => {
    client.listTools(initResult.tools, (tools) => {
      tools.forEach(tool => { ... });
    });
  });
});
```

**Fix:** `async/await` at every level (see Rule ASYNC-6).

#### The `as` Escape Hatch Anti-Pattern

```typescript
// ANTI-PATTERN — defeats the type system
const result = response as ToolCallResult;
result.content[0].text;  // crashes at runtime if content is empty
```

**Fix:** Validate the shape with a type guard before accessing it:

```typescript
function isToolCallResult(value: unknown): value is ToolCallResult {
  return (
    typeof value === "object" &&
    value !== null &&
    "isError" in value &&
    "content" in value &&
    Array.isArray((value as ToolCallResult).content)
  );
}
```

#### The Premature Abstraction Anti-Pattern

Adding interfaces, factories, and dependency injection for a component that has
exactly one implementation and will likely always have one.

**Fix:** Write the concrete implementation first. Extract an interface only when a
second implementation actually exists or when testability requires mocking.

### 10.3 Architectural Anti-Patterns

These are catalogued in `01_ARCHITECTURE_RULES.md` Section 12 in full. Summary:

| Anti-pattern | Detection | Reference |
|---|---|---|
| Approval bypass | `dispatcher.execute()` outside executor context | Section 12.1 |
| Upward dependency | Lower layer importing higher layer | Section 12.2 |
| Direct `os.environ` access | `os.environ.get(` outside `settings.py` | Section 12.3 |
| Planner logic contamination | `ContextBuilder` in `planner.py` | Section 12.4 |
| Shared mutable executor | Single executor reused across requests | Section 12.5 |
| Direct LLM provider import | `import openai` outside `src/llm/providers/` | Section 12.6 |
| Unbounded search returns | No `MAX_SEARCH_RESULTS` cap in walk/search tools | Section 12.7 |

---

## 11. Documentation Requirements

### 11.1 When to Document

**Rule DOC-1:** Default is NO comments. Code that expresses its own intent through
naming, structure, and types does not need a comment. A comment that restates what
the code already says adds noise.

**Rule DOC-2:** Write a comment when ANY of the following is true:
- The **why** is non-obvious: a hidden constraint, a regulatory requirement, a
  counterintuitive invariant
- The code deliberately appears wrong (intentional performance trick, workaround
  for a specific bug)
- The code is a known exception to a general rule (cite the permitted exception code)
- A future reader would likely "fix" the code in a way that reintroduces a bug

```python
# WRONG — comment restates the code
# Increment i by 1
i += 1

# CORRECT — comment explains WHY
# Truncate at MAX_SEARCH_RESULTS before serializing into the planning prompt;
# an unbounded result set can exhaust the LLM context window silently (Rule T-7).
if len(matches) >= MAX_SEARCH_RESULTS:
    matches.append(_truncation_notice(matches, MAX_SEARCH_RESULTS))
```

### 11.2 Docstrings

**Rule DOC-3:** Public modules MUST have a module docstring (one to three sentences:
what this module is, what it provides).

**Rule DOC-4:** Public classes MUST have a class docstring describing the class's
responsibility and its primary lifecycle (construction, use, teardown if applicable).

**Rule DOC-5:** Public functions MUST have a docstring when:
- The function's behavior is not fully captured by its name and type signature
- The function has important preconditions or postconditions
- The function has non-obvious side effects

When the name + signature is already self-documenting, a docstring is optional.

**Rule DOC-6:** Docstrings MUST NOT exceed three lines for a simple function.
Multi-paragraph docstrings are reserved for complex lifecycle descriptions (agent
classes, executor, MCP server). Never write a docstring that lists all the parameters
in prose when the type annotation already does that.

```python
# CORRECT — concise, adds information beyond the signature
def approve(self) -> ExecutionReport:
    """
    Apply all staged patches and commands, then resume execution where the run paused.
    Raises RuntimeError if no run is currently awaiting approval.
    """
```

### 11.3 TODO and FIXME Comments

**Rule DOC-7:** `TODO` comments MUST reference a sprint or issue:
`# TODO(Sprint N): ...` or `# TODO(#issue): ...`. A bare `# TODO` with no reference
is not actionable and will rot.

**Rule DOC-8:** `FIXME` comments indicate a known bug or incorrect behavior. They
MUST be resolved within one sprint of being written. A `FIXME` that outlives its
sprint becomes a `BUG` entry in the CHANGELOG.

**Rule DOC-9:** `DEPRECATED(ADR-NNN):` comments mark deprecated code (see
`01_ARCHITECTURE_RULES.md`, Rule EV-1). They MUST cite the ADR that documents the
deprecation.

### 11.4 CHANGELOG Requirements

**Rule DOC-10:** Every PR that changes observable behavior MUST add an entry to
`CHANGELOG.md` under `[Unreleased]`. Entries fall into: `Added`, `Changed`,
`Deprecated`, `Removed`, `Fixed`, `Security`.

**Rule DOC-11:** CHANGELOG entries MUST be written from the user's perspective, not
the developer's. "Fixed a null pointer exception in Executor" is a developer entry.
"Fixed: tool execution no longer crashes when the LLM returns an empty plan" is a
user entry.

---

## 12. Testing Expectations

### 12.1 Test Philosophy

Tests are first-class code. They are subject to all the same coding standards as
production code. A test that is hard to read is a test that will be broken to fix a
"test failure" rather than a real bug.

**Rule TEST-1:** Every new behavior MUST have at least one test. "I tested it
manually" does not satisfy this rule.

**Rule TEST-2:** Every bug fix MUST have a regression test that fails before the fix
and passes after.

**Rule TEST-3:** Tests MUST be deterministic. A test that sometimes passes and
sometimes fails (flaky test) is worse than no test — it trains contributors to ignore
test failures.

### 12.2 Unit Tests

**Rule TEST-4:** A unit test tests ONE behavior of ONE unit. It does not test how two
real components interact.

**Rule TEST-5:** Unit tests MUST be fast (< 100ms each). A slow unit test usually
means it's doing I/O — mock it.

**Rule TEST-6:** Use `pytest` fixtures for setup. Do not call `setUp()` or put setup
in the test body unless it's a one-liner that's specific to that test.

**Rule TEST-7:** Use `pytest.raises()` to test exception behavior, not a bare
`try/except`:

```python
# CORRECT
def test_read_file_raises_on_missing_file(tmp_path):
    missing = tmp_path / "does_not_exist.txt"
    with pytest.raises(FileNotFoundError, match="does_not_exist.txt"):
        read_file(str(missing))

# VIOLATION
def test_read_file_raises_on_missing_file(tmp_path):
    try:
        read_file(str(tmp_path / "does_not_exist.txt"))
        assert False, "Should have raised"
    except FileNotFoundError:
        pass
```

### 12.3 Integration Tests

**Rule TEST-8:** Integration tests live in `tests/integration/`. They may spin up
real subprocesses, real temp directories, and real scripted LLM providers. They
MUST NOT connect to real external APIs.

**Rule TEST-9:** Integration tests MUST use the `scripted` LLM provider
(`PEARL_LLM_PROVIDER=scripted`) for any test that involves planning. This makes them
deterministic without an Ollama or API dependency.

**Rule TEST-10:** The approval invariant MUST have at least one integration test per
sprint that:
1. Runs an autonomous task that would write a file
2. Verifies the file does NOT exist before approval
3. Calls `approve()`
4. Verifies the file EXISTS after approval

### 12.4 Test Naming

**Rule TEST-11:** Test function names MUST follow: `test_<what>_<condition>_<expected>`.
The name should read as a specification:

```python
def test_read_file_with_missing_path_raises_file_not_found(): ...
def test_approve_with_no_pending_run_raises_runtime_error(): ...
def test_search_text_caps_results_at_max_search_results(): ...
def test_executor_emits_progress_event_on_tool_start(): ...
```

**Rule TEST-12:** Test descriptions in `pytest.mark.parametrize` MUST be meaningful:

```python
# VIOLATION
@pytest.mark.parametrize("mode", ["none", "minimal", "normal", "fun"])

# CORRECT
@pytest.mark.parametrize("mode,expected_has_emoji", [
    ("none",    False),
    ("minimal", True),
    ("normal",  True),
    ("fun",     True),
], ids=["no-emoji", "emoji-on-significant-only", "emoji-on-every-event", "emoji-plus-accent"])
```

### 12.5 Fixtures and Mocking

**Rule TEST-13:** Fixtures MUST be named as nouns (the thing they provide), not
verbs or sentences:

```python
# CORRECT
@pytest.fixture
def registry(): ...

@pytest.fixture
def tmp_workspace(tmp_path): ...

@pytest.fixture
def scripted_executor(): ...
```

**Rule TEST-14:** Mock only at the boundary you're testing. When testing the executor,
mock the LLM client and the dispatcher. When testing a tool, use a real temp directory.
Do not mock things that the code under test doesn't actually call.

**Rule TEST-15:** `unittest.mock.patch` MUST specify the full dotted name of the
object where it is USED, not where it is DEFINED. A patch applied in the wrong module
has no effect and produces false positives.

```python
# CORRECT — patching where it's USED
with patch("src.tools.repo_tools._get_index") as mock_index: ...

# VIOLATION — patching where it's DEFINED (may not affect the module under test)
with patch("src.index.Index.build") as mock_build: ...
```

### 12.6 Coverage Expectations

**Rule TEST-16:** Minimum code coverage for the `src/` package is **90%**. Coverage
below this level gates merging.

**Rule TEST-17:** Coverage is measured by line, not by branch. Branch coverage is
desirable but not enforced. Lines that CANNOT be tested (abstract methods, `...` stubs)
MUST be excluded with `# pragma: no cover`.

**Rule TEST-18:** 100% coverage is not the goal. Trivial getters, framework callbacks,
and unreachable defensive assertions do not need tests. What needs tests is behavior
that would surprise a user if it broke.

---

## 13. Code Review Rules

### 13.1 Author Responsibilities

Before opening a pull request, the author MUST:

- [ ] Run `ruff format` and `ruff check` — zero violations
- [ ] Run `pyright` or `mypy` — zero errors on public interfaces
- [ ] Run the full test suite — all tests pass
- [ ] Self-review the diff line by line — catch obvious issues before asking for review
- [ ] Write a PR description that explains WHY, not just WHAT
- [ ] Add CHANGELOG entries for every observable behavior change
- [ ] Verify the PR size: prefer < 400 lines changed; PRs over 800 lines MUST be split

**Rule CR-1:** The PR description MUST answer three questions:
1. What problem does this change solve?
2. How does this change solve it?
3. How was it tested?

### 13.2 Reviewer Responsibilities

**Rule CR-2:** Reviewers MUST use the Architecture Review Checklist
(`01_ARCHITECTURE_RULES.md`, Section 16) for any PR touching core components.

**Rule CR-3:** Reviewers MUST distinguish between blocking and non-blocking feedback:
- **BLOCKING:** Correctness issues, security issues, standards violations, missing tests
- **NON-BLOCKING:** Style preferences, alternative approaches, suggestions for future
  improvement (prefix with `nit:` or `suggestion:`)

**Rule CR-4:** Blocking feedback MUST reference the specific rule being violated.
"This doesn't look right to me" is not actionable. "This violates Rule T-4 — all
write tools must call `_ensure_within_workspace`" is.

**Rule CR-5:** A review MUST NOT be approved while blocking feedback remains
unresolved. An author who disagrees with blocking feedback must discuss it, not ignore
it.

### 13.3 Review Turnaround

**Rule CR-6:** Reviews MUST be completed within 48 hours of the PR being opened. A
PR waiting longer than 48 hours MUST be escalated in the team channel.

**Rule CR-7:** A PR MUST have at least one approval from a reviewer other than the
author before merging.

### 13.4 Code Review Decision Tree

```
A change is proposed. Should it be approved?
│
├─ Does it pass all automated checks (ruff, pyright, tests)?
│   └─ NO → Request changes. Do not approve.
│
├─ Does it have an ADR for any architectural change?
│   └─ NO (and one is required per Section 17 of 01_ARCHITECTURE_RULES.md) → Block.
│
├─ Does it have tests for new behavior and regression tests for bug fixes?
│   └─ NO → Block.
│
├─ Does the CHANGELOG reflect the observable changes?
│   └─ NO → Block.
│
├─ Are there blocking violations of standards?
│   └─ YES → Request changes with specific rule citations.
│
└─ All checks pass? → Approve.
```

---

## 14. Refactoring Rules

### 14.1 When to Refactor

**Rule REF-1:** Refactor when ANY of the following is true:
- A function exceeds 60 lines
- A class exceeds 300 lines
- The same logic appears in three or more places
- Adding a test for the existing code requires mocking more than two things
- A new feature would require changing an existing feature's internals

**Rule REF-2:** Do NOT refactor in the same PR as a feature or bug fix. Mixing
refactoring and functional changes makes the diff impossible to review. Refactoring
is a separate commit or PR.

**Rule REF-3:** Refactoring MUST NOT change observable behavior. Before refactoring,
ensure test coverage exists. After refactoring, all tests MUST pass unchanged. If a
test needs to change as a result of refactoring, explain why in the commit message.

### 14.2 Safe Refactoring Patterns

#### Extract Function

Split a long function into smaller named functions. Each extracted function MUST be
testable independently.

```python
# BEFORE — 80-line function
def run_autonomous(self, prompt: str) -> ExecutionReport:
    # plan
    ...
    # execute each step
    ...
    # handle approval
    ...

# AFTER — three focused functions
def run_autonomous(self, prompt: str) -> ExecutionReport:
    plan = self._build_plan(prompt)
    return self._execute_plan(plan)

def _build_plan(self, prompt: str) -> list[ToolCall]: ...
def _execute_plan(self, plan: list[ToolCall]) -> ExecutionReport: ...
```

#### Introduce Parameter Object

Replace a long parameter list with a dataclass.

#### Rename for Clarity

Rename a poorly-named variable, function, or class. Always use IDE-assisted rename
to ensure all call sites are updated.

#### Move to Right Layer

Move a function or class to the module that owns its concern (see
`01_ARCHITECTURE_RULES.md`, Section 2).

### 14.3 Refactoring Boundaries

**Rule REF-4:** Refactoring MUST NOT cross process or language boundaries in a single
change. Do not simultaneously refactor the Python executor AND the TypeScript client
in the same PR.

**Rule REF-5:** Public interfaces (tool names, MCP methods, `PearlAgent` public
methods) MUST NOT be renamed without a deprecation cycle (see Rule EV-1).

**Rule REF-6:** Refactoring the approval flow (`PatchManager`, `AutonomousExecutor`)
requires full re-validation of the approval invariant test suite before merging.

---

## 15. Performance Considerations

### 15.1 The Performance Contract

Pearl is a single-user developer tool. Performance goals are:
- Tool execution: < 2 seconds for most operations (file I/O, small searches)
- Plan generation: bounded by LLM latency (not Pearl's responsibility to speed up)
- Repository indexing: < 5 seconds for a typical project (< 50,000 files)
- Approval cycle: < 100ms for patch staging and application

### 15.2 Measurement First

**Rule PERF-1:** Do NOT optimize without measurement. Before optimizing a code path,
profile it with `cProfile` or `py-spy` and confirm which lines are actually slow.
"I think this is slow" is not evidence.

**Rule PERF-2:** When a performance optimization makes code less readable, it MUST be
accompanied by a comment with the benchmark numbers that justify the complexity:

```python
# Using bytearray instead of str concatenation in the loop: 40% faster on 10k-line files
# (benchmarked 2024-12-01, MacBook M3, Python 3.12)
result = bytearray()
for line in lines:
    result.extend(line.encode())
```

### 15.3 File I/O Performance

**Rule PERF-3:** Never read a large file into memory in one call without checking
`MAX_FILE_SIZE_BYTES`. For files larger than this threshold, read lazily (line by line
or in chunks) or raise a `FileTooLargeError`.

**Rule PERF-4:** Prefer `Path.read_text()` over `open(path).read()` for file reads.
Prefer `Path.write_text()` over `open(path, 'w').write()`. They are shorter, more
Pythonic, and handle encoding consistently.

**Rule PERF-5:** When walking a directory tree, prune `IGNORED_DIRS` early (before
recursing into subdirectories), not after. Walking into `.git/objects/` for every
repository in a large monorepo is unnecessary I/O.

### 15.4 LLM Call Performance

**Rule PERF-6:** Minimize the number of LLM calls per task. Every extra call adds
latency, cost, and a new failure point. Restructure plans to do more per call rather
than chaining many small calls.

**Rule PERF-7:** The workspace context string passed to the planner MUST be bounded.
An unbounded context string increases tokens-per-call, which increases both latency
and cost. Cap context at `Settings.MAX_CONTEXT_TOKENS` before passing to the planner.

**Rule PERF-8:** Stream LLM responses where the provider supports it. Streaming
reduces perceived latency for the user even when total generation time is unchanged.

### 15.5 Memory Performance

**Rule PERF-9:** The `RepositoryIndex` is an in-memory structure. For very large
repositories (> 100,000 files), the index MUST use lazy loading — load metadata on
demand rather than all at once.

**Rule PERF-10:** `Memory.recent_messages()` with a large limit can produce a very
large history string. Always pass the minimum `limit` needed for the current call.

---

## 16. Security Considerations

### 16.1 Input Validation

**Rule SEC-1:** All tool inputs that represent file paths MUST be validated with
`_ensure_within_workspace(path)` before any I/O. This is the primary defense against
path traversal attacks.

```python
# CORRECT — validate before any I/O
def write_file(path: str, content: str) -> str:
    resolved = _ensure_within_workspace(path)  # raises WorkspaceBoundaryError if outside
    resolved.write_text(content, encoding="utf-8")
```

**Rule SEC-2:** Shell command tool (`execute_shell`) MUST check against
`_DANGEROUS_PATTERNS` before execution. New patterns that should be blocked MUST be
added to this list with a comment explaining the attack vector they prevent.

**Rule SEC-3:** Do NOT construct shell commands by string interpolation with user
input. Use `subprocess.run(list_of_args)` with a list, never `shell=True` with
an f-string.

```python
# VIOLATION — shell injection risk
subprocess.run(f"git log --oneline {branch}", shell=True)

# CORRECT — no shell injection; arguments are not interpreted by a shell
subprocess.run(["git", "log", "--oneline", branch], capture_output=True)
```

### 16.2 Credential Handling

**Rule SEC-4:** API keys, tokens, and passwords MUST live in `.env` and be read
through `Settings`. They MUST NOT be hard-coded anywhere in the codebase.

**Rule SEC-5:** `.env` files MUST be listed in `.gitignore`. Pearl MUST NOT add a
`.env` with any real credentials to version control under any circumstances.

**Rule SEC-6:** When displaying configuration in logs or UI, redact credential values:

```python
# CORRECT
logger.info("OpenAI API key: %s", "***" if Settings.OPENAI_API_KEY else "(not set)")
```

### 16.3 Dependency Security

**Rule SEC-7:** Run `pip-audit` (Python) or `npm audit` (TypeScript) in CI. Any
critical or high severity vulnerability in a direct dependency MUST be resolved
within two sprints of discovery.

**Rule SEC-8:** Do not install packages from unverified sources. All Python packages
MUST be installed from PyPI. All TypeScript packages MUST be installed from the
official npm registry.

### 16.4 MCP Security

**Rule SEC-9:** The MCP server processes JSON-RPC messages from the VS Code extension.
Even though the extension is a trusted client (same machine, same user), the server
MUST validate:
- Required fields are present
- Field types match expected types
- String lengths are within bounds

This prevents malformed tool calls from crashing the server or producing silent
incorrect behavior.

**Rule SEC-10:** Pearl MUST NOT execute shell commands from MCP `tools/call` requests
without routing them through `CommandApprovalManager` when in autonomous mode.

### 16.5 File Safety

**Rule SEC-11:** When writing files, Pearl MUST use atomic writes (write to a temp
file, then rename) for any file where a partial write would leave a corrupt state.
`PatchManager` handles this for patch application.

**Rule SEC-12:** Pearl MUST NOT follow symlinks that escape the workspace boundary.
`_ensure_within_workspace()` MUST resolve symlinks before comparing to the workspace
root.

---

## 17. Common Mistakes

### Mistake 1: Returning `None` when an error should be raised

**Symptom:** A `find_*` or `get_*` function returns `None` and callers crash with
`AttributeError: 'NoneType' object has no attribute '...'` somewhere else entirely.  
**Fix:** Raise a specific exception with an actionable message. `None` return is
reserved for optional values that callers explicitly check.

### Mistake 2: Missing `from __future__ import annotations`

**Symptom:** `NameError: name 'AutonomousExecutor' is not defined` when using a
forward reference in a type annotation.  
**Fix:** Add `from __future__ import annotations` to the top of the file.

### Mistake 3: Using an f-string in a log call

**Symptom:** Log messages that are identical in content but different in msg template,
making log aggregation useless. Also, string interpolation happens even if the log
level is disabled.  
**Fix:** Use `%s`-style formatting: `logger.info("Tool: %s", name)`.

### Mistake 4: Mutable default argument

**Symptom:** Behavior changes across calls because the default list/dict is shared.
Hard to reproduce; depends on call order.  
**Fix:** Use `None` as the default and initialize inside the function.

### Mistake 5: Catching `Exception` in a tool

**Symptom:** A tool returns an empty result or a fallback value when it should have
raised, causing the planner to receive incorrect information and generate a wrong plan.  
**Fix:** Catch only the specific exceptions you handle. Let unknown exceptions
propagate.

### Mistake 6: Hard-coding a model name

**Symptom:** Provider error after a model is retired; the error message references
a model name that appears in 7 files.  
**Fix:** All model names in `Settings.DEFAULT_MODEL`. One place to update.

### Mistake 7: Not calling `refresh_indexed_file` after a write

**Symptom:** `find_symbol` returns stale results for a symbol that was just written.
The model's next step uses the wrong line numbers.  
**Fix:** Every code path that commits a file write (real or via `PatchManager.apply_all()`)
MUST call `refresh_indexed_file(path)` for each written file.

### Mistake 8: Long function with multiple `return` paths and early exits

**Symptom:** Adding a new condition requires understanding all existing `return` paths
to avoid missing one. Tests are hard to write because the function does too much.  
**Fix:** Extract sub-functions. Each function should have one or two return paths.

### Mistake 9: Fixing a test by weakening it

**Symptom:** A test starts failing; the fix is to broaden the assertion or add a
`try/except` inside the test rather than fixing the production code.  
**Fix:** A failing test is information. Understand why it fails, then fix the
production code.

### Mistake 10: Adding a class field in `__init__` without a class-level annotation

**Symptom:** `pyright` reports "Attribute is not declared in class body". The field
is invisible to static analysis.  
**Fix:** Always declare class fields at the class body level, even for dataclasses:

```python
# VIOLATION — field declared only in __init__
class Executor:
    def __init__(self):
        self.cancel_event = threading.Event()  # not visible to type checker at class level

# CORRECT
class Executor:
    cancel_event: threading.Event

    def __init__(self):
        self.cancel_event = threading.Event()
```

---

## 18. Coding Standards Review Checklist

Use this checklist before approving any pull request.

### Python Formatting

- [ ] `ruff format` applied — no formatting diffs
- [ ] `ruff check` — zero warnings (no `# noqa` without justification)
- [ ] `from __future__ import annotations` at the top of every Python file
- [ ] Imports in three groups, sorted alphabetically within each group

### Type Safety

- [ ] All public functions/methods fully annotated (parameters + return type)
- [ ] No `Optional[X]` — uses `X | None` instead
- [ ] No `Dict[K,V]` / `List[T]` — uses `dict[k,v]` / `list[t]` instead
- [ ] Every use of `Any` has a justifying comment
- [ ] `pyright` / `mypy` passes on public interfaces

### Naming

- [ ] Functions and variables in `snake_case`
- [ ] Classes in `PascalCase`
- [ ] Module-level constants in `SCREAMING_SNAKE_CASE`
- [ ] Boolean variables use `is_`, `has_`, `can_`, `should_` prefix
- [ ] No abbreviations except universally understood shorthands
- [ ] Test functions named `test_<what>_<condition>_<expected>`

### Error Handling

- [ ] Tools raise specific exceptions with actionable messages
- [ ] No bare `except Exception:` in tool code without a log
- [ ] No `return None` to signal an error
- [ ] No `sys.exit()` outside `main()`

### Logging

- [ ] Logger created with `logging.getLogger(__name__)`
- [ ] Log calls use `%s`-formatting, not f-strings
- [ ] No file contents in log output
- [ ] No credentials or API keys in log output
- [ ] Every `except` that swallows logs at `WARNING` or `ERROR`

### Functions and Classes

- [ ] Functions under 60 lines
- [ ] Classes under 300 lines
- [ ] No mutable default arguments
- [ ] No more than 4 positional parameters (or dataclass used)
- [ ] `pathlib.Path` used for all path operations

### Testing

- [ ] New behavior has at least one test
- [ ] Bug fixes have a regression test
- [ ] Tests use `pytest.raises()` for exception testing
- [ ] Mocks applied where the name is USED, not where it is DEFINED
- [ ] Integration tests use the `scripted` provider

### Documentation

- [ ] Public modules have a module docstring
- [ ] Public classes have a class docstring
- [ ] Comments explain WHY, not WHAT
- [ ] CHANGELOG updated for every observable behavior change
- [ ] No `TODO` without a sprint or issue reference

### Security

- [ ] File-writing tools call `_ensure_within_workspace()`
- [ ] Shell commands use list form, not `shell=True` with f-strings
- [ ] No hard-coded credentials
- [ ] API keys read from `Settings`, not `os.environ`

### TypeScript (extension only)

- [ ] `strict: true` in effect — no `any`, no non-null assertions
- [ ] `import type` used for type-only imports
- [ ] `async/await` used throughout — no `.then()` chains
- [ ] All disposables registered to `context.subscriptions`
- [ ] All VS Code `Thenable` values awaited or explicitly voided

### AI-Assisted Code

- [ ] AI-generated code reviewed line by line — not accepted wholesale
- [ ] Security-critical paths authored by a human, not accepted from an AI suggestion
- [ ] PR description notes that AI assistance was used if > 20% of the diff is AI-generated
- [ ] No AI-generated placeholder comments or stub docstrings left in merged code

### Technical Debt

- [ ] New `DEBT:` comment includes sprint target and debt category
- [ ] No new `DEBT:` comment added for debt older than two sprints (escalate instead)
- [ ] Debt introduced by this PR declared in the PR description

### Feature Flags

- [ ] New feature flag follows `ENABLE_<FEATURE>` naming convention in `Settings`
- [ ] Flagged feature tested in both the enabled and disabled state
- [ ] Removal sprint noted in the flag's declaration comment

### Git Commits

- [ ] Commit messages follow Conventional Commits format (`type(scope): description`)
- [ ] Breaking changes annotated with `BREAKING CHANGE:` in the commit footer
- [ ] No "WIP", "fix", "asdf", or message-free commits in the final branch

---

## 19. AI-Assisted Development Rules

Pearl is itself an AI coding agent. Using AI tools to help develop Pearl creates a
layered situation: an AI helps build the tool that will help users write code. These
rules ensure that AI assistance improves productivity without undermining code quality,
correctness, or security.

### 19.1 Permitted Uses

**Rule AI-1:** AI assistance is permitted for: generating boilerplate, writing initial
test skeletons, explaining unfamiliar APIs, drafting docstrings, and proposing
refactoring strategies.

**Rule AI-2:** The contributor is the author of record for any AI-assisted code.
"The AI wrote it" is not a defence for a standards violation, a bug, or a security
issue. Review AI suggestions with the same rigour as your own code.

### 19.2 Required Verification for AI-Generated Code

**Rule AI-3:** Every AI-suggested code block MUST be read line by line before
acceptance. Accepting a suggestion without reading it is not code authorship.

**Rule AI-4:** AI-generated code MUST pass the full review checklist (Section 18)
before merging. There is no shortened review path for AI-generated code.

**Rule AI-5:** AI tools often produce plausible-looking code that is subtly wrong —
incorrect API signatures, missing edge cases, hallucinated function names. Verify:
- Function names and parameters exist in the actual library (check the docs)
- The code handles `None`, empty collections, and error states
- The code respects the architecture rules (correct layer, correct imports)

### 19.3 Restricted Areas

**Rule AI-6:** The following components MUST have human-authored implementation. AI
suggestions may be used as a reference or starting point, but the final implementation
must be reviewed and rewritten line-by-line by the contributor:

- `PatchManager` and `CommandApprovalManager` (approval invariant)
- `AutonomousExecutor._checkpoint_before_writing()` (data safety)
- `_ensure_within_workspace()` (security boundary)
- `_DANGEROUS_PATTERNS` (shell injection prevention)
- Any new MCP method handler that executes write tools

Rationale: these are Pearl's security-critical paths. A subtly wrong AI suggestion in
any of these can bypass the safety guarantees that Pearl's entire design is built on.

### 19.4 Transparency in PRs

**Rule AI-7:** If more than 20% of a PR's diff was AI-generated (not just
AI-suggested and then heavily modified), the PR description MUST note this. Example:
`AI-assisted: initial implementation of X was generated with [tool name] and then
reviewed and modified.`

This is not a penalty — it is a signal for reviewers to apply extra scrutiny.

### 19.5 Dogfooding Pearl Itself

**Rule AI-8:** Whenever practical, Pearl's own contributors SHOULD use Pearl to
assist with Pearl development. This is the primary dogfooding mechanism. Issues found
during dogfooding MUST be filed immediately, before continuing the development task
that surfaced them. See `03_TESTING_STANDARD.md`, Section 13 for the full dogfooding
protocol.

---

## 20. Technical Debt Policy

Technical debt is the accumulated cost of decisions made under time pressure or
incomplete knowledge. Pearl's policy is not "never incur debt" — that is unrealistic.
The policy is: know what debt exists, pay it intentionally, and never let it grow
silently.

### 20.1 Debt Classification

| Category | Definition | Examples |
|---|---|---|
| **Architectural debt** | A shortcut that violates a layer rule or ownership boundary | Tool importing from the agent layer to avoid refactoring |
| **Code quality debt** | Code that works but is hard to read, test, or change | A 200-line function, a class doing three things |
| **Test debt** | Missing tests for existing behavior | A path through the executor with no test |
| **Documentation debt** | Missing, stale, or misleading docs | A CHANGELOG entry that hasn't been updated |
| **Dependency debt** | Outdated or vulnerable dependencies | A library 3 major versions behind |

### 20.2 Recording Debt

**Rule DEBT-1:** Known debt MUST be recorded with a `DEBT:` comment at the specific
line where the debt is incurred:

```python
# DEBT(code-quality, Sprint 28): split _execute_plan into separate planning and
# dispatching phases. Currently does both in 90 lines.
def _execute_plan(self, ...): ...
```

The format is: `DEBT(<category>, <target-sprint>): <description>`.

**Rule DEBT-2:** Debt MUST also be logged in the PR that introduces it. The PR
description MUST include a "Debt introduced" section listing each `DEBT:` comment
added.

**Rule DEBT-3:** A `DEBT:` comment older than two sprints past its target sprint MUST
be escalated — either paid down in the current sprint or formally re-scheduled with
architect sign-off. Debt that is never paid is not debt — it is a permanent standard
violation.

### 20.3 Debt Budget

**Rule DEBT-4:** Each sprint MUST allocate at least 10% of its capacity to debt
reduction. "We'll pay it down later" with no concrete allocation means it never
happens.

**Rule DEBT-5:** Architectural debt (violations of `01_ARCHITECTURE_RULES.md`) MUST
be paid within one sprint. It cannot be deferred further than that.

### 20.4 Debt Inventory

The current debt inventory is maintained in `docs/engineering/DEBT_INVENTORY.md`.
Every `DEBT:` comment in the codebase MUST appear in this inventory. The inventory
is reviewed at each sprint planning session.

---

## 21. Feature Flag Guidelines

Feature flags allow high-risk changes to be merged to `master` in a disabled state,
validated in isolation, and enabled once confidence is high. They are not a permanent
feature — every flag has a planned removal sprint.

### 21.1 When to Use a Feature Flag

Use a feature flag when ANY of the following is true:
- The change touches the approval invariant, the executor loop, or the MCP protocol
- The change is a new autonomous execution mode that needs validation before exposure
- The change is a new LLM provider or routing logic
- The change is a significant UX change to the extension that needs A/B validation

Do NOT use a flag for: routine tool additions, bug fixes, documentation, test
additions, or refactoring within existing behavior.

### 21.2 Flag Implementation

**Rule FF-1:** Feature flags MUST be declared in `Settings` with the `ENABLE_` prefix
and a `bool` type, defaulting to `False`:

```python
class Settings:
    # Feature flags (remove by Sprint N)
    ENABLE_STREAMING_PATCHES: bool = False   # ADR-007; remove Sprint 30
    ENABLE_MULTI_FILE_PLAN: bool = False     # ADR-009; remove Sprint 31
```

**Rule FF-2:** The flag declaration comment MUST include the ADR reference and the
removal target sprint.

**Rule FF-3:** Flag checks MUST be as close to the entry point as possible. A flag
buried deep inside a tool function is hard to find and harder to remove.

```python
# CORRECT — checked at the entry point
def run_autonomous(self, prompt: str, ...) -> ExecutionReport:
    if Settings.ENABLE_MULTI_FILE_PLAN:
        return self._run_multi_file(prompt, ...)
    return self._run_single_file(prompt, ...)
```

### 21.3 Flag Lifecycle

```
Sprint N:   Flag introduced (default: False)
            Feature implemented behind the flag
            Both paths (enabled/disabled) covered by tests

Sprint N+1: Flag enabled by default (default: True)
            Disabled path still tested

Sprint N+2: Flag and both code paths removed
            CHANGELOG entry: "Removed feature flag ENABLE_X (now always on)"
```

A flag that does not advance through this lifecycle on schedule MUST be reviewed at
the next sprint planning. Flags that stay at `False` indefinitely become dead code.

### 21.4 Testing Flagged Features

**Rule FF-4:** A flagged feature MUST have tests for BOTH the enabled and disabled
states. A PR that adds a flag without testing both states will be rejected.

```python
@pytest.mark.parametrize("flag_value", [True, False])
def test_run_autonomous_respects_multi_file_flag(flag_value, monkeypatch):
    monkeypatch.setattr(Settings, "ENABLE_MULTI_FILE_PLAN", flag_value)
    ...
```

---

## 22. Engineering Metrics

Metrics tell us whether standards are being upheld and whether Pearl is improving
sprint over sprint. These metrics are tracked, not aspirational.

### 22.1 Code Quality Metrics

| Metric | Target | Gate |
|---|---|---|
| Test coverage (`src/`) | ≥ 90% | Hard — blocks merge |
| Ruff violations | 0 | Hard — blocks merge |
| Pyright errors on public interfaces | 0 | Hard — blocks merge |
| Functions > 60 lines | 0 | Hard — blocks merge |
| Classes > 300 lines | 0 | Soft — reviewer flag |
| Open `DEBT:` comments past target sprint | 0 | Soft — sprint review |
| Average cyclomatic complexity | < 8 per function | Soft — reported |

**Rule METRIC-1:** Hard gates MUST be enforced by CI. A PR that breaks a hard gate
MUST NOT be merged even with architect approval.

**Rule METRIC-2:** Soft gates are reviewed in sprint retrospective. Three consecutive
sprints of a soft gate being violated without resolution escalates it to a hard gate.

### 22.2 Test Metrics

| Metric | Target |
|---|---|
| Unit test suite duration | < 60 seconds |
| Integration test suite duration | < 5 minutes |
| Flaky test rate | 0% — any flaky test is P1 |
| Regression tests per bug fix | 1:1 (one regression test per bug) |
| E2E tests per user story | ≥ 1 |

**Rule METRIC-3:** Test duration MUST be tracked sprint over sprint. A suite that
doubles in duration in one sprint requires investigation.

### 22.3 Release Metrics

| Metric | Target |
|---|---|
| Open P0/P1 bugs at release | 0 |
| CHANGELOG completeness | 100% (all observable changes documented) |
| ADR coverage | 100% (every architectural change has an ADR) |
| Documentation coverage | All new public APIs have docstrings |

### 22.4 Reporting

**Rule METRIC-4:** Engineering metrics are reported in the sprint retrospective using
the standard metrics table format. The report covers: current sprint values, prior
sprint values, and trend (↑ better / ↓ worse / → stable).

The metrics report template lives in `docs/engineering/SPRINT_METRICS_TEMPLATE.md`.

---

## 23. Public API Stability

Pearl exposes three distinct API surfaces. Each has its own stability contract.

### 23.1 API Surfaces

| Surface | Definition | Consumers |
|---|---|---|
| **MCP Protocol** | All `pearl/*` and standard MCP methods; their request/response shapes | VS Code extension, any future MCP client |
| **`PearlAgent` facade** | `run_autonomous()`, `approve()`, `reject()`, `chat()`, `execute_tool()`, `available_tools()` | CLI, tests, future embedding contexts |
| **Tool names and schemas** | All `@tool`-decorated function names and their `parameters` declarations | Planner (via description), MCP `tools/call` clients |

### 23.2 Stability Levels

**Rule API-1:** Every public API element MUST be classified at one of three stability
levels, documented in a `# Stability: <level>` comment at its declaration:

| Level | Meaning | Backward compatibility guarantee |
|---|---|---|
| `alpha` | Experimental; may change without notice | None |
| `beta` | Stabilizing; breaking changes require one sprint of deprecation | One sprint notice |
| `stable` | Production; breaking changes require ADR + one sprint deprecation + version bump | Full deprecation cycle |

**Rule API-2:** Newly added MCP methods start at `alpha`. They advance to `beta`
after one sprint of use without breaking changes. They advance to `stable` after
two sprints at `beta`.

**Rule API-3:** `PearlAgent`'s five public methods are classified `stable`. Any
change to their signatures is a breaking change and requires a full deprecation cycle
(see Rule EV-1 in `01_ARCHITECTURE_RULES.md`).

### 23.3 Versioning

**Rule API-4:** Pearl follows Semantic Versioning for `SERVER_VERSION` in `src/mcp/server.py`:

| Change type | Version bump |
|---|---|
| New `pearl/*` method (additive) | Minor (`1.2.0` → `1.3.0`) |
| New `stable` tool added | Minor |
| Breaking change to any `stable` interface | Major (`1.x.x` → `2.0.0`) |
| Bug fix with no API change | Patch (`1.2.0` → `1.2.1`) |

**Rule API-5:** The VS Code extension's `package.json` version MUST be kept in sync
with `SERVER_VERSION` for major and minor bumps.

### 23.4 Consumer Impact Analysis

Before any breaking change to a `stable` API, the author MUST:
1. Search the codebase for all call sites of the changed interface
2. Update all call sites in the same PR as the interface change
3. Document the migration path in the CHANGELOG
4. If external consumers exist (other tools using the MCP server), publish a migration
   guide before the breaking version is released

---

## 24. Git Commit Message Standards

Commit messages are a permanent record. They are the first place a developer looks
when `git blame` or `git bisect` points to a change. Write them for the reader who
has no context beyond the diff.

### 24.1 Format

Pearl uses **Conventional Commits** (https://www.conventionalcommits.org/):

```
<type>(<scope>): <short description>

[optional body — wrap at 72 characters]

[optional footer(s)]
```

**Rule GIT-1:** The subject line (first line) MUST be ≤ 72 characters, written in
the imperative mood ("add", "fix", "remove" — not "added", "fixes", "removing").

**Rule GIT-2:** The subject line MUST NOT end with a period.

**Rule GIT-3:** The body (when present) MUST be separated from the subject by a blank
line and wrap at 72 characters.

### 24.2 Type Prefixes

| Type | When to use |
|---|---|
| `feat` | A new feature or capability |
| `fix` | A bug fix |
| `refactor` | Code restructuring with no behavior change |
| `test` | Adding or correcting tests |
| `docs` | Documentation only |
| `chore` | Build scripts, CI, dependency updates |
| `perf` | Performance improvement |
| `security` | Security fix or hardening |
| `revert` | Reverting a prior commit |

**Rule GIT-4:** The type MUST match the primary nature of the change. A commit that
both adds a feature and fixes a bug it discovered should be split into two commits.

### 24.3 Scope

The scope (in parentheses) identifies the component affected. Use the module or layer
name: `executor`, `planner`, `mcp`, `tools`, `checkpoints`, `extension`, `deps`,
`ci`.

```
feat(checkpoints): add :rename command to CLI
fix(executor): clear context vars on cancellation
refactor(planner): extract _build_tool_descriptions helper
test(approval): add regression for bypass via plan_and_run
docs(architecture): add sections 17-20 to ARCHITECTURE_RULES
```

### 24.4 Breaking Changes

**Rule GIT-5:** A commit that introduces a breaking change to a `stable` API MUST
include a `BREAKING CHANGE:` footer:

```
feat(mcp)!: rename pearl/approvePatches to pearl/approve

The `!` after the scope signals breaking. The footer explains the change:

BREAKING CHANGE: pearl/approvePatches is now pearl/approve. Update
all clients. The old name is removed with no deprecation period
because the method was classified `alpha`.
```

### 24.5 What Makes a Bad Commit Message

| Message | Problem |
|---|---|
| `fix stuff` | No scope, no description, imperative is missing |
| `WIP` | Not a complete commit; squash before merging |
| `addressing review comments` | No information about what changed |
| `updated executor.py` | Describes the file changed, not what changed in it |
| `fix: Fix the bug where the thing doesn't work` | "the thing" is meaningless to anyone reading in 6 months |

### 24.6 Branch Naming

| Pattern | When to use |
|---|---|
| `feat/<short-name>` | New feature |
| `fix/<short-name>` | Bug fix |
| `refactor/<short-name>` | Refactoring |
| `chore/<short-name>` | Maintenance |
| `sprint/<number>` | Sprint work branches |

Branch names MUST use `kebab-case`. Branches named `my-branch`, `test`, or `fix2`
are not permitted on shared branches.

---

## 25. Benchmark Policy

Benchmarks are evidence, not marketing. They are only useful when they are
reproducible, correctly attributed, and honestly interpreted.

### 25.1 What to Benchmark

Pearl tracks benchmarks in three categories:

| Category | What is measured | Unit |
|---|---|---|
| **Repository indexing** | Time to build `RepositoryIndex` for a standard corpus | seconds |
| **Tool execution** | Latency of each tool call type at p50/p95 | milliseconds |
| **End-to-end task** | Time from `run_autonomous()` call to `stop_reason` for standard tasks | seconds |

### 25.2 When to Run Benchmarks

**Rule BM-1:** Benchmarks MUST be run at the start of a sprint (baseline) and at the
end (comparison). Sprint-over-sprint regressions in tool execution latency > 10%
or indexing time > 20% MUST be investigated before the sprint closes.

**Rule BM-2:** Benchmarks MUST be run on dedicated hardware with no other significant
processes running. A benchmark run on a loaded developer laptop is not comparable to
a previous run on the same machine under load.

### 25.3 Environment Requirements

Every benchmark result MUST record:
- CPU model and core count
- RAM total and available at run start
- Operating system and version
- Python version
- All relevant dependency versions (`pip freeze`)
- Whether the run was on a cold index or a warm cache

**Rule BM-3:** A benchmark result without hardware/environment provenance MUST be
marked `[UNPROVENENANCED — DO NOT COMPARE]` and MUST NOT be referenced in any
performance claim.

### 25.4 Documenting Results

```markdown
## Benchmark: Repository Indexing
Date: 2026-07-26
Hardware: AMD Ryzen 9 5900X, 64 GB RAM, NVMe SSD
OS: Ubuntu 22.04
Python: 3.12.4
Dependencies: (see requirements-frozen.txt)

| Corpus | Files | Symbols | Cold index time | Warm re-index time |
|---|---|---|---|---|
| pearl-agent (self) | 87 | 412 | 0.31s | 0.04s |
| linux kernel (drivers/) | 12,000 | 95,000 | 18.4s | 2.1s |

Prior sprint (2026-07-12): 0.29s (self), 17.8s (kernel)
Change: +6.9% (self), +3.4% (kernel) — within acceptable bounds.
```

### 25.5 Regression Thresholds

| Category | Acceptable regression | Investigate | Block sprint close |
|---|---|---|---|
| Indexing time | < 10% | 10–20% | > 20% |
| Tool latency (p50) | < 5% | 5–15% | > 15% |
| Tool latency (p95) | < 10% | 10–25% | > 25% |
| E2E task time | < 15% | 15–30% | > 30% |

**Rule BM-4:** A benchmark regression that exceeds the "block sprint close" threshold
MUST be resolved or formally accepted (with an architectural explanation) before the
sprint is considered done.

### 25.6 The Superseded Mark

**Rule BM-5:** Any benchmark result that is no longer comparable to current conditions
(different hardware, different corpus, different major version) MUST be marked
`[SUPERSEDED: reason]` in the benchmark file. A superseded result MUST NOT be
referenced in any performance claim or PR description.

---

*This document is part of the Pearl Engineering Standards Series.*  
*Previous: [01_ARCHITECTURE_RULES.md](01_ARCHITECTURE_RULES.md)*  
*Next: [03_TESTING_STANDARD.md](03_TESTING_STANDARD.md)*
