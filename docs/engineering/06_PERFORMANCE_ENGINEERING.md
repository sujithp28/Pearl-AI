# 06 — Performance Engineering

**Pearl AI Coding Agent — Engineering Standards Series**  
Document owner: Lead Software Architect  
Applies to: Every sprint, every contributor, every release  
Status: Authoritative — changes require architect sign-off

---

## Purpose

This document is Pearl's authoritative performance engineering standard. It defines
how Pearl achieves, measures, and maintains the performance characteristics that
make it trustworthy for production use on real developer workstations with real
repositories.

Performance in an autonomous coding agent has stakes that generic application
performance tuning does not. When Pearl is slow, the developer is blocked. When
Pearl wastes tokens, API bills grow. When Pearl fills its context window carelessly,
it loses earlier, critical information and produces wrong results. When Pearl
consumes unbounded memory, it competes with the IDE itself. Each of these is not
merely a nuisance — it is a correctness failure or a trust failure.

Rules in this document are **binding**. A pull request that introduces a measurable
performance regression in any category covered here is rejected, regardless of how
otherwise correct the implementation is. Every rule states a rationale; challenge
the rule rather than the enforcement.

Performance budgets in this document are defined for a **reference environment**:
a modern developer workstation (8-core CPU, 16 GB RAM, NVMe SSD, 100 Mbps internet),
running a repository of up to **10,000 files**. Stricter targets are called out
where they apply.

---

## Scope

This document applies to:

- The Python backend (`src/`) — all async execution paths, tool dispatch, LLM calls,
  file I/O, and the repository indexing pipeline
- The VS Code Extension (`vscode-extension/`) — webview rendering, status bar
  updates, diff rendering, and the MCP request/response pipeline
- The `RepositoryIndexer`, `ContextBuilder`, `TokenBudgetManager`, and all caching
  layers
- Every tool implementation: file operations, shell execution, search, git operations
- The LLM client layer — prompt construction, streaming, token accounting
- Background processes — incremental indexing, checkpoint management
- Any future interface or integration Pearl exposes

It cross-references:

- `01_ARCHITECTURE_RULES.md` — for the two-process boundary that constrains IPC
  latency budgets
- `02_CODING_STANDARDS.md` — for async programming rules that underpin non-blocking
  execution
- `03_TESTING_STANDARD.md` — for performance regression testing gates and benchmark
  suites
- `04_SECURITY_GUIDELINES.md` — for the performance/security trade-offs at
  validation boundaries
- `05_UI_UX_GUIDELINES.md` — for the perceived-performance rules that govern
  progress events and streaming

---

## Table of Contents

1. [Performance Philosophy](#1-performance-philosophy)
2. [Performance Budgets](#2-performance-budgets)
3. [Startup Performance](#3-startup-performance)
4. [Repository Index Performance](#4-repository-index-performance)
5. [File System Performance](#5-file-system-performance)
6. [Tool Execution Performance](#6-tool-execution-performance)
7. [LLM Performance Guidelines](#7-llm-performance-guidelines)
8. [Prompt Construction Efficiency](#8-prompt-construction-efficiency)
9. [Context Window Optimization](#9-context-window-optimization)
10. [Token Budget Management](#10-token-budget-management)
11. [Caching Strategy](#11-caching-strategy)
12. [Memory Management](#12-memory-management)
13. [Parallel Execution Rules](#13-parallel-execution-rules)
14. [Async Execution Standards](#14-async-execution-standards)
15. [Large Repository Handling](#15-large-repository-handling)
16. [Incremental Indexing](#16-incremental-indexing)
17. [Lazy Loading Standards](#17-lazy-loading-standards)
18. [Background Processing](#18-background-processing)
19. [Resource Utilization](#19-resource-utilization)
20. [CPU Optimization](#20-cpu-optimization)
21. [Memory Optimization](#21-memory-optimization)
22. [Disk I/O Optimization](#22-disk-io-optimization)
23. [Network Optimization](#23-network-optimization)
24. [Benchmarking Standards](#24-benchmarking-standards)
25. [Performance Regression Testing](#25-performance-regression-testing)
26. [Profiling Guidelines](#26-profiling-guidelines)
27. [Monitoring and Metrics](#27-monitoring-and-metrics)
28. [Performance Logging](#28-performance-logging)
29. [Performance Anti-Patterns](#29-performance-anti-patterns)
30. [Common Performance Mistakes](#30-common-performance-mistakes)
31. [Performance Review Checklist](#31-performance-review-checklist)
32. [Release Performance Checklist](#32-release-performance-checklist)
33. [Canonical Vocabulary](#33-canonical-vocabulary)
34. [References](#34-references)

---

## 1. Performance Philosophy

### 1.1 Core Principles

Pearl's performance philosophy is grounded in the observation that a developer
working with an AI agent has fundamentally different latency tolerances than a
user waiting for a webpage. The developer is waiting to review work. Every
unnecessary second costs attention, flow state, and trust.

**Principle PERF-1 — Perceived performance is the real performance metric.**
A task that completes in 10 seconds with streaming progress events feels faster
than a task that completes in 7 seconds with no feedback. Both are measured; only
the streaming version is acceptable. See `05_UI_UX_GUIDELINES.md`, Section 13.

**Principle PERF-2 — Token efficiency is correctness, not optimization.**
The LLM context window is not infinite. Every token wasted on boilerplate, repeated
content, or unnecessary context is a token that cannot carry code the LLM needs to
reason correctly. Token waste directly degrades response quality and inflates cost.

**Principle PERF-3 — Pearl must not compete with the IDE for resources.**
Pearl runs inside a developer's VS Code session alongside their entire workspace.
Pearl's peak memory usage, peak CPU usage, and disk I/O MUST NOT cause the IDE to
stall, the editor to lag, or the system to swap. Pearl is a guest on the developer's
machine.

**Principle PERF-4 — Performance budgets are hard limits, not aspirational targets.**
A budget that is treated as aspirational is not a budget — it is a guideline that
will be exceeded in every sprint under pressure. Budgets are enforced in CI as
hard gates. A build that exceeds a budget fails, same as a build that fails a test.

**Principle PERF-5 — Measure before optimizing. Optimize the bottleneck.**
No performance change is accepted without a before/after measurement. Intuition
about performance is unreliable in async, I/O-bound systems. Profile first;
change second; measure again.

**Principle PERF-6 — Avoid speculative optimization.**
Code that is written speculatively for performance without a measured bottleneck
is technical debt with extra steps. Optimize exactly what profiling identifies,
nothing more.

**Principle PERF-7 — Correctness is never sacrificed for performance.**
A faster algorithm that returns wrong results is not faster — it is broken. Every
performance optimization MUST preserve the full correctness of the operation it
accelerates, including all security validations (`04_SECURITY_GUIDELINES.md`,
Section 2).

### 1.2 The Performance Contract

Pearl makes the following performance contract with the user. These are not
aspirational; they are the standard that code review enforces.

```
┌─────────────────────────────────────────────────────────────────┐
│  Pearl Performance Contract                                     │
│                                                                 │
│  Cold start to first token (CLI)          < 3 s                │
│  Cold start to first token (extension)    < 5 s                │
│  First LLM token visible to user          < 3 s after dispatch │
│  Repository index (10k files)             < 30 s               │
│  Incremental index (< 50 changed files)   < 2 s                │
│  Tool round-trip (non-shell)              < 500 ms             │
│  Tool round-trip (shell, fast)            < 2 s               │
│  Context window utilization               < 80% at start       │
│  Peak memory per session                  < 512 MB             │
│  Peak CPU (idle monitoring)               < 5%                 │
│  Cancellation acknowledged                < 2 s                │
└─────────────────────────────────────────────────────────────────┘
```

### 1.3 Decision Tree: Should I Optimize This?

```
Is there a measured, profiled bottleneck?
│
├── NO ──► Do not optimize. Ship the clear, correct implementation.
│
└── YES
    │
    Is the bottleneck within a defined performance budget?
    │
    ├── YES ──► Still no action required. Monitor; revisit if budget
    │           is approached.
    │
    └── NO (budget exceeded)
        │
        Is it a hot path (called every turn / every request)?
        │
        ├── NO ──► Optimize if the fix is low-risk. If high-risk,
        │          log a tech-debt ticket.
        │
        └── YES ──► Optimize. Block the sprint if budget overrun is
                    present in main.
```

---

## 2. Performance Budgets

### 2.1 Latency Budgets

Latency is measured end-to-end from the user's perspective: from the moment the
user submits a command to the moment they receive meaningful output.

| Operation | Budget | Measurement point | Failure action |
|---|---|---|---|
| CLI startup to REPL prompt | 3 s | Time from `python -m pearl` invocation | Block release |
| Extension activation to status "Ready" | 5 s | VS Code `onActivate` to status bar update | Block release |
| First LLM streaming token | 3 s | Tool dispatch to first `pearl/progress` event | Block release |
| Single-file read tool | 100 ms | MCP request received to response sent | P1 bug |
| `search_text` (10k-file repo) | 2 s | MCP request to complete response | P1 bug |
| `find_references` (10k-file repo) | 3 s | MCP request to complete response | P1 bug |
| `execute_shell` (fast command) | 2 s | MCP request to process exit captured | P2 bug |
| Approval prompt render | 300 ms | `approve` trigger to diff displayed | P1 bug |
| Checkpoint creation | 2 s | `git commit` in shadow repo | P2 bug |
| Incremental index (< 50 files) | 2 s | Change detected to index updated | P2 bug |
| Full index (10k-file repo, warm FS cache) | 30 s | Index start to index complete | P1 bug |
| MCP IPC round-trip (protocol overhead only) | 10 ms | Request encoded to response decoded | P1 bug |
| Cancellation acknowledged | 2 s | Ctrl-C to "Cancelled" status | Block release |

### 2.2 Token Budgets

Token budgets apply per-turn (one user message → one LLM response cycle).

| Context slot | Budget (tokens) | Budget (% of 128k window) | Notes |
|---|---|---|---|
| System prompt | ≤ 8,000 | ≤ 6.3% | Cached after first turn |
| Tool definitions | ≤ 4,000 | ≤ 3.1% | Cached after first turn |
| Conversation history | ≤ 40,000 | ≤ 31.3% | Pruned per TOK-* rules |
| Current task context | ≤ 20,000 | ≤ 15.6% | Files, search results |
| Working files (open) | ≤ 16,000 | ≤ 12.5% | Top-k most relevant |
| Response headroom | ≥ 8,000 | ≥ 6.3% | Must always be reserved |
| **Total used at dispatch** | **≤ 88,000** | **≤ 68.8%** | Hard ceiling |

**Rule TOK-1:** The context window MUST NOT be filled beyond 80% at the point of
LLM dispatch. The remaining 20% is response headroom and defensive margin. A
`TokenBudgetManager` MUST enforce this ceiling and raise a recoverable error
rather than dispatching an over-budget context.

> **Rationale:** LLMs begin to degrade before hitting the hard context limit.
> The last 20% of the window is the most expensive and least reliable. Keeping
> headroom is both a correctness and a cost measure.

**Rule TOK-2:** Token budget state MUST be logged at DEBUG level at every turn.
This makes it possible to diagnose "the LLM forgot what I told it earlier" as a
context overflow problem, not a model failure.

```python
# CORRECT
logger.debug(
    "token_budget",
    system=system_tokens,
    tools=tool_tokens,
    history=history_tokens,
    context=context_tokens,
    total=total_tokens,
    headroom=max_tokens - total_tokens,
)
```

### 2.3 Memory Budgets

| Component | Budget | Measurement | Enforcement |
|---|---|---|---|
| Full Python process (peak, per session) | 512 MB RSS | `psutil.Process().memory_info().rss` | CI memory gate |
| Repository index (in-memory, 10k files) | 128 MB | Index object `__sizeof__` sum | Assert in index constructor |
| Context builder working set | 64 MB | Peak allocation during build | Profiling gate |
| LLM response buffer (streaming) | 8 MB | Buffer size before flushing | Hard-coded buffer limit |
| Checkpoint shadow repo | ≤ disk delta | `git gc` after each checkpoint | Background gc task |
| VS Code extension webview | 128 MB | Chrome DevTools heap snapshot | Manual review gate |

**Rule MEM-1:** The Python process MUST NOT exceed 512 MB RSS during any normal
operation on a 10,000-file repository. Operations that require temporary
allocations beyond this limit MUST stream or page their data rather than loading
it fully in memory.

**Rule MEM-2:** Every component that caches data in memory MUST have a defined
eviction policy and a maximum size. An unbounded in-memory cache is a memory leak
with extra steps.

### 2.4 Repository Scaling Limits

Pearl defines three repository scale tiers. Performance budgets are specified per tier.

| Tier | File count | Lines of code | Budget multiplier |
|---|---|---|---|
| **Small** | ≤ 1,000 files | ≤ 100k LOC | 1× (base budgets apply) |
| **Medium** | 1,001–10,000 files | 100k–1M LOC | 1× (base budgets apply) |
| **Large** | 10,001–50,000 files | 1M–5M LOC | See Section 15 |
| **Monorepo** | > 50,000 files | > 5M LOC | Requires explicit scoping |

**Rule IDX-1:** For Large repositories, Pearl MUST NOT attempt a full in-memory
index. It MUST activate partial indexing (Section 16) and scope operations to the
current working subtree.

**Rule IDX-2:** Monorepo support MUST be configured with an explicit scope path.
Pearl will refuse to index a repository with > 50,000 files without a scope
configuration that reduces the effective file count into the Large tier or below.

---

## 3. Startup Performance

### 3.1 Cold Start Requirements

Cold start is defined as launching Pearl with no warm Python interpreter cache,
no warm OS filesystem cache, and no existing index on disk.

**Rule PERF-8:** The CLI cold start (from `python -m pearl` invocation to the
appearance of the REPL prompt) MUST complete within **3 seconds** on the reference
environment. This includes all module imports, configuration loading, and the initial
MCP server handshake.

**Rule PERF-9:** The VS Code extension cold start (from VS Code activation to
status bar showing "Pearl: Ready") MUST complete within **5 seconds**. The Python
subprocess MUST be spawned asynchronously; the extension MUST NOT block the VS Code
UI thread during startup.

```typescript
// CORRECT — asynchronous subprocess spawn, non-blocking
async function activate(context: vscode.ExtensionContext): Promise<void> {
    // Show "Pearl: Starting..." immediately
    statusBar.text = "$(loading~spin) Pearl: Starting...";
    statusBar.show();
    // Spawn Python backend without awaiting — result arrives via MCP handshake
    spawnPearlBackend(context).catch(handleSpawnError);
}

// VIOLATION — blocking the activation path
async function activate(context: vscode.ExtensionContext): Promise<void> {
    await spawnPearlBackend(context);  // blocks VS Code activation
    statusBar.text = "$(check) Pearl: Ready";
}
```

### 3.2 Warm Start Requirements

Warm start is defined as restarting Pearl within a session where the Python
interpreter is warm, the filesystem cache is warm, and an index on disk is current.

**Rule PERF-10:** Warm start to REPL prompt MUST complete within **1 second**.
The index MUST be loaded from disk cache, not rebuilt, unless `--no-cache` is
explicitly passed.

**Rule PERF-11:** Index cache validity MUST be checked against the repository's
`git rev-parse HEAD` result and the modification times of `.gitignore` and
`.pearlignore`. If the HEAD SHA matches and those files are unchanged, the index
is valid without re-reading file contents.

```python
# CORRECT — fast validity check
def _is_index_valid(self, cache_path: Path) -> bool:
    meta = _load_cache_meta(cache_path)
    return (
        meta.head_sha == _git_head_sha(self.workspace)
        and meta.gitignore_mtime == _mtime(self.workspace / ".gitignore")
    )

# VIOLATION — re-reading all files to verify cache
def _is_index_valid(self, cache_path: Path) -> bool:
    for f in self.workspace.rglob("*"):   # reads entire tree
        if f.stat().st_mtime > cache_meta.built_at:
            return False
    return True
```

### 3.3 Import Optimization

**Rule PERF-12:** All heavy imports (tree-sitter, tiktoken, numpy, and any ML
library) MUST be deferred to first use. They MUST NOT appear as module-level
imports in any file that is loaded during startup. Use the lazy import pattern:

```python
# CORRECT — lazy import pattern
def _get_tokenizer() -> "tiktoken.Encoding":
    import tiktoken  # imported on first call only
    return tiktoken.get_encoding("cl100k_base")

# VIOLATION — imported at module level, paid on every startup
import tiktoken  # at the top of context_builder.py
```

**Rule PERF-13:** Startup import time MUST be measured in CI using
`python -X importtime -m pearl --dry-run 2>&1 | tail -20`. Any module whose
cumulative import time exceeds **200 ms** is flagged for lazy import refactoring.

---

## 4. Repository Index Performance

### 4.1 Index Construction Budget

**Rule IDX-3:** Full index construction for a 10,000-file repository MUST complete
within **30 seconds** on the reference environment. This is measured from the moment
the user triggers indexing to the moment the `index_complete` progress event fires.

**Rule IDX-4:** Index construction MUST run in a background thread/process so that
the REPL or extension remains responsive during indexing. The user MUST see
incremental progress events at least every **5 seconds**.

```python
# CORRECT — background indexing with progress events
async def _build_index(self) -> None:
    total = len(self._files_to_index)
    for i, batch in enumerate(_batches(self._files_to_index, size=100)):
        await self._index_batch(batch)
        await self._emit_progress(
            current=i * 100,
            total=total,
            message=f"Indexed {i * 100}/{total} files",
        )

# VIOLATION — blocking, no progress
def _build_index(self) -> None:
    for f in self._files_to_index:
        self._index_file(f)  # blocking, no progress events
```

### 4.2 Index Storage Format

**Rule IDX-5:** The on-disk index MUST use a binary serialization format (msgpack
or a custom binary format) rather than JSON. JSON parsing of a 10,000-file index
is measurably slower than binary deserialization and produces unnecessary memory
pressure during parsing.

**Rule IDX-6:** The index file MUST be written atomically: write to a `.tmp` file,
then rename. A partial index write that leaves a corrupt file on disk corrupts
future warm starts.

```python
# CORRECT — atomic write
def _save_index(self, index: Index, path: Path) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(msgpack.packb(index.to_dict()))
    tmp.rename(path)  # atomic on POSIX

# VIOLATION — non-atomic write leaves corrupt state on interrupt
def _save_index(self, index: Index, path: Path) -> None:
    path.write_bytes(msgpack.packb(index.to_dict()))
```

### 4.3 Index Query Performance

**Rule IDX-7:** Index queries for symbol lookup MUST complete in **< 10 ms** for
a 10,000-file index already loaded in memory. This requires an inverted index
structure (symbol name → file list), not a linear scan.

**Rule IDX-8:** File-content search MUST use the pre-built inverted index where
possible. Falling back to a full `rg` (ripgrep) scan is allowed only when the
query cannot be answered by the index (regex patterns that require full-text
matching). The fallback MUST log a warning at DEBUG level.

```
Benchmark: Symbol lookup latency (10,000-file repo, warm memory)

  Inverted index lookup      3 ms   ✓ PASS
  Linear scan (Python)     420 ms   ✗ FAIL
  ripgrep fallback (cold)  180 ms   ✓ acceptable for text-only queries
```

---

## 5. File System Performance

### 5.1 Read Performance

**Rule IO-1:** All file reads MUST be performed asynchronously using
`asyncio.to_thread()` (Python) or non-blocking I/O (TypeScript). Synchronous file
reads that block the event loop are prohibited in any path that is called during
an active agent turn.

```python
# CORRECT — async file read
async def read_file(path: Path) -> str:
    return await asyncio.to_thread(path.read_text, encoding="utf-8")

# VIOLATION — blocks the event loop
def read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")  # synchronous, blocks event loop
```

**Rule IO-2:** When reading multiple files as part of a single tool call (e.g.,
reading a batch of search results), the reads MUST be performed concurrently using
`asyncio.gather()`, not sequentially.

```python
# CORRECT — concurrent reads
async def read_files(paths: list[Path]) -> list[str]:
    return await asyncio.gather(*[read_file(p) for p in paths])

# VIOLATION — sequential reads, N × latency
async def read_files(paths: list[Path]) -> list[str]:
    results = []
    for p in paths:
        results.append(await read_file(p))  # sequential
    return results
```

### 5.2 Write Performance

**Rule IO-3:** All file writes that are part of an approved patch application MUST
be atomic at the individual-file level: write to a temporary file in the same
directory, then `rename()`. This prevents partial writes from corrupting files if
the process is interrupted mid-write.

**Rule IO-4:** File writes MUST use explicit `encoding="utf-8"` on all platforms.
Relying on the platform's default encoding produces behavior that differs between
developer machines and is undefined on Windows.

**Rule IO-5:** Never use `os.path` functions in new code. Use `pathlib.Path`
throughout. `pathlib` avoids string concatenation bugs in path construction and
provides platform-safe operations.

### 5.3 Directory Traversal

**Rule IO-6:** Repository traversal MUST respect `.gitignore` and `.pearlignore`
rules. Traversing into `node_modules`, `.git`, `__pycache__`, or other ignored
directories wastes I/O, pollutes search results, and inflates the index. Use
`gitignore_spec` or `pathspec` to evaluate ignore rules during traversal.

```python
# CORRECT — applies ignore rules during traversal
def _iter_workspace_files(root: Path, spec: PathSpec) -> Iterator[Path]:
    for f in root.rglob("*"):
        rel = f.relative_to(root)
        if not spec.match_file(str(rel)) and f.is_file():
            yield f

# VIOLATION — traverses everything, filters afterward (reads ignored dirs)
def _iter_workspace_files(root: Path) -> Iterator[Path]:
    return (f for f in root.rglob("*") if f.is_file())
```

**Rule IO-7:** Directory traversal MUST NOT recurse into symlinked directories
unless the symlink target is within the workspace. Unbounded symlink traversal can
reach the entire filesystem.

### 5.4 File Watching

**Rule IO-8:** The file watcher for incremental indexing MUST use OS-native
mechanisms (`watchdog` with `InotifyObserver` on Linux, `FSEventsObserver` on
macOS, `WindowsApiObserver` on Windows). Polling-based watchers that stat every
file on a timer are prohibited; they cause continuous high CPU usage and disk I/O.

**Rule IO-9:** The file watcher MUST debounce events with a **500 ms** window.
Rapid successive changes to the same file (e.g., auto-save during rapid typing)
MUST collapse into a single index update, not N updates.

---

## 6. Tool Execution Performance

### 6.1 Tool Dispatch Latency

**Rule PERF-14:** The overhead of the MCP tool dispatch layer (from MCP request
received to tool handler called) MUST be less than **10 ms**. This is protocol
overhead, not tool execution time, and must not grow with the number of registered
tools.

**Rule PERF-15:** Tool handlers MUST be registered in a dictionary keyed by tool
name, not in a chain of `if`/`elif` statements. Dictionary lookup is O(1); a
50-tool `elif` chain is O(50) on the worst case.

```python
# CORRECT — O(1) dispatch
_TOOL_REGISTRY: dict[str, ToolHandler] = {
    "read_file": ReadFileTool(),
    "write_file": WriteFileTool(),
    ...
}

async def dispatch(name: str, params: dict) -> ToolResult:
    handler = _TOOL_REGISTRY.get(name)
    if handler is None:
        raise UnknownToolError(name)
    return await handler.run(params)

# VIOLATION — O(N) dispatch
async def dispatch(name: str, params: dict) -> ToolResult:
    if name == "read_file":
        return await ReadFileTool().run(params)
    elif name == "write_file":
        return await WriteFileTool().run(params)
    elif name == "search_text":
        ...
```

### 6.2 Shell Command Performance

**Rule PERF-16:** Shell commands invoked via `execute_shell` MUST have a timeout.
No shell command is permitted to run without a timeout. The default timeout is
**30 seconds**; long-running commands (builds, test suites) MUST use an explicit,
user-approved timeout up to **300 seconds**.

**Rule PERF-17:** Shell commands that produce large stdout (> 1 MB) MUST be
truncated with a clear notice rather than loading the full output into the tool
result. Full output loading inflates context tokens and risks OOM.

```python
# CORRECT — truncation with notice
MAX_SHELL_OUTPUT_BYTES = 1_048_576  # 1 MB

async def _run_shell(cmd: list[str], timeout: float) -> str:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    output, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    if len(output) > MAX_SHELL_OUTPUT_BYTES:
        output = output[:MAX_SHELL_OUTPUT_BYTES]
        return output.decode("utf-8", errors="replace") + "\n[OUTPUT TRUNCATED]"
    return output.decode("utf-8", errors="replace")
```

### 6.3 Search Performance

**Rule PERF-18:** `search_text` MUST use `ripgrep` (`rg`) as its backend for
full-text search, not Python's `re` module iterating over file contents. `rg`
is typically 5–10× faster on cold filesystems due to SIMD-optimized search and
parallelism.

**Rule PERF-19:** Search results MUST be capped at `MAX_SEARCH_RESULTS = 200`
lines. Returning more than 200 results inflates the context beyond useful
information density. The cap MUST be enforced in the tool, not relied on at the
LLM level. See `01_ARCHITECTURE_RULES.md`, Section 9.

**Rule PERF-20:** `find_references` MUST use the in-memory index for symbol-level
lookups and fall back to `rg` only for text patterns. The index-backed path MUST
be at least **10× faster** than the `rg` fallback on a warm index.

---

## 7. LLM Performance Guidelines

### 7.1 LLM Dispatch Latency

**Rule LLM-PERF-1:** The time from user command submission to the first token of
LLM response visible in the UI MUST be less than **3 seconds** (excluding time
spent in tool calls). This includes prompt construction, context assembly, and the
network round-trip to the LLM provider.

**Rule LLM-PERF-2:** Prompt construction MUST complete in under **500 ms** for any
context size within budget. Prompt construction that takes longer than 500 ms
indicates algorithmic inefficiency in the `ContextBuilder` — profile and fix before
shipping.

**Rule LLM-PERF-3:** LLM API calls MUST be made asynchronously and MUST stream
the response. A non-streaming call that waits for the full completion before
returning anything to the user violates both this rule and `05_UI_UX_GUIDELINES.md`
UX-3.

```python
# CORRECT — streaming, asynchronous
async def dispatch_llm(messages: list[Message]) -> AsyncIterator[str]:
    async with llm_client.stream(messages=messages) as stream:
        async for chunk in stream:
            yield chunk.text
            await _emit_progress(chunk.text)

# VIOLATION — blocks until full response
async def dispatch_llm(messages: list[Message]) -> str:
    response = await llm_client.complete(messages=messages)  # no streaming
    return response.text
```

### 7.2 Retry and Backoff

**Rule LLM-PERF-4:** All LLM API calls MUST implement exponential backoff with
jitter for retryable errors (rate limits, transient 5xx). The retry schedule is:

```
Attempt 1:  immediate
Attempt 2:  2 s ± 0.5 s jitter
Attempt 3:  4 s ± 1 s jitter
Attempt 4:  8 s ± 2 s jitter
Attempt 5:  fail with PearlLLMError
```

Non-retryable errors (4xx client errors, invalid API key, context-too-long) MUST
fail immediately without retry.

**Rule LLM-PERF-5:** Context-too-long errors (HTTP 400 from the provider) MUST
trigger the token pruning pipeline (Section 10), not a user-visible error. The
`TokenBudgetManager` SHOULD prevent this state from being reached, but the error
MUST be handled if it occurs.

---

## 8. Prompt Construction Efficiency

### 8.1 System Prompt Caching

**Rule CTX-1:** The system prompt MUST be constructed once at session start and
cached for the duration of the session. It MUST NOT be reconstructed on every turn.
The system prompt is static by design — it does not incorporate per-turn context.

**Rule CTX-2:** For LLM providers that support prompt caching (Anthropic Claude's
cache-control blocks), the system prompt and tool definitions MUST be marked as
cacheable. Failing to mark them cacheable causes the provider to charge input token
costs on every turn for content that does not change.

```python
# CORRECT — system prompt marked cacheable (Anthropic)
system_block = {
    "type": "text",
    "text": SYSTEM_PROMPT,
    "cache_control": {"type": "ephemeral"},
}

# VIOLATION — system prompt not cacheable; paid per turn
system_block = {
    "type": "text",
    "text": SYSTEM_PROMPT,
    # no cache_control — charged every turn
}
```

### 8.2 Prompt Template Efficiency

**Rule CTX-3:** Prompt templates MUST be pre-compiled at module load time, not
re-parsed on each call. String formatting with f-strings is acceptable; Jinja2
templates MUST be compiled once with `jinja2.Environment` and cached.

**Rule CTX-4:** Prompt construction MUST NOT involve reading files from disk.
All file content included in a prompt MUST already be in memory from a previous
tool call result or from the index. If a file must be read for a prompt, the read
MUST be an explicit, logged operation — not a hidden prompt-construction side effect.

**Rule CTX-5:** Tool definitions MUST be serialized to JSON once and cached as a
string. Re-serializing the tool definitions on every LLM call is unnecessary work.

### 8.3 Message History Formatting

**Rule CTX-6:** Message history MUST be formatted as a list of role/content dicts,
not reconstructed from a raw transcript string each turn. Parsing a raw string to
reconstruct structured history is O(n²) in conversation length.

---

## 9. Context Window Optimization

### 9.1 Context Assembly Strategy

Context assembly is the process of selecting, ordering, and fitting content into
the LLM's context window. It is one of the highest-impact performance operations
in Pearl.

**Rule CTX-7:** Context assembly MUST follow a priority ordering. When the context
is full, lower-priority items are dropped before higher-priority items:

```
Priority 1 (never dropped): System prompt, tool definitions
Priority 2 (never dropped): Current user message
Priority 3 (drop last): The file(s) most relevant to the current task
Priority 4 (drop second-to-last): Recent conversation turns (last 5)
Priority 5 (drop first): Older conversation turns
Priority 6 (drop first): Search results from prior turns
Priority 7 (drop first): Index summaries from prior turns
```

**Rule CTX-8:** Context assembly MUST complete in **under 200 ms** for any context
within budget. Expensive relevance ranking algorithms are not permitted in the hot
path. Use the pre-built index for relevance scoring.

### 9.2 File Inclusion Strategy

**Rule CTX-9:** Do not include entire files in the context unless the file is small
(≤ 200 lines) and entirely relevant. For larger files, include:

1. The file header (imports, class declarations) — first 20 lines
2. The specific functions or methods identified by the index as relevant
3. The surrounding context (±10 lines) of each relevant section

**Rule CTX-10:** When including file content, ALWAYS include the file path and line
numbers as context. This enables the LLM to generate correct patch hunks and
reference correct locations.

```python
# CORRECT — path and line numbers included
def _format_file_excerpt(path: str, start: int, end: int, content: str) -> str:
    return f"# File: {path} (lines {start}–{end})\n{content}"

# VIOLATION — path lost, line numbers lost
def _format_file_excerpt(content: str) -> str:
    return content
```

### 9.3 Context Deduplication

**Rule CTX-11:** Before assembling the context, the `ContextBuilder` MUST deduplicate
content. The same file excerpt MUST NOT appear twice in the context, even if two
different tool calls returned it. Deduplication key: (file path, line range).

**Rule CTX-12:** Tool results from the conversation history that are superseded by
a more recent result for the same file MUST be excluded from the context. Stale
file content confuses the LLM and wastes tokens.

### 9.4 Context Window Monitoring

**Rule CTX-13:** The `ContextBuilder` MUST emit a `context_budget` metric at DEBUG
level after every context assembly:

```python
logger.debug(
    "context_assembled",
    priority_1=p1_tokens,
    priority_3=p3_tokens,
    priority_4=p4_tokens,
    total=total_tokens,
    dropped_turns=dropped_count,
    headroom=headroom_tokens,
)
```

---

## 10. Token Budget Management

### 10.1 Token Counting

**Rule TOK-3:** Token counting MUST use the same tokenizer as the target LLM.
For Claude models, use Anthropic's token-counting API endpoint or the
`anthropic.count_tokens()` method. For other providers, use their respective
tokenizer. Approximating token counts with character-count heuristics produces
unreliable budget enforcement.

```python
# CORRECT — uses provider tokenizer
async def count_tokens(text: str) -> int:
    return await anthropic_client.count_tokens(text)

# VIOLATION — heuristic approximation
def count_tokens(text: str) -> int:
    return len(text) // 4  # characters / 4 is wrong for code
```

**Rule TOK-4:** Token counting MUST be cached per content string. Counting the same
system prompt's tokens on every turn is pure waste. Cache key: SHA-256 of the
content string. Cache TTL: session lifetime.

### 10.2 Budget Enforcement

**Rule TOK-5:** The `TokenBudgetManager` MUST raise a `TokenBudgetExceededError`
(recoverable) before dispatching an over-budget context. The error handler MUST
trigger context pruning (Section 9.3), not a user-visible failure.

**Rule TOK-6:** When context pruning is triggered, the `ContextBuilder` MUST log
which items were dropped, at INFO level, so the developer can diagnose reasoning
gaps.

```python
# CORRECT — log dropped items
def _prune_to_budget(self, items: list[ContextItem]) -> list[ContextItem]:
    kept, dropped = [], []
    budget = self._remaining_budget
    for item in sorted(items, key=lambda i: -i.priority):
        if item.tokens <= budget:
            kept.append(item)
            budget -= item.tokens
        else:
            dropped.append(item)
    if dropped:
        logger.info("context_pruned", dropped=[i.label for i in dropped])
    return kept
```

### 10.3 Conversation History Pruning

**Rule TOK-7:** Conversation history MUST be pruned using a sliding window strategy.
The most recent turns are always retained. Older turns are summarized or dropped
when the budget is under pressure. The pruning strategy is:

```
1. Always keep: last 5 turns (Priority 4)
2. Under pressure (< 20% headroom): summarize turns 6–20 into a "prior context" block
3. Severe pressure (< 10% headroom): drop prior context entirely, keep last 5 turns only
```

**Rule TOK-8:** Summarization of pruned turns MUST be done by the LLM in a
separate, lightweight call using a reduced system prompt. The summary MUST be
cached for the session. The LLM call for summarization MUST NOT itself use more
than 8,000 tokens.

---

## 11. Caching Strategy

### 11.1 Cache Taxonomy

Pearl uses five cache types. Each has defined scope, invalidation, and size limits.

| Cache | Scope | Invalidation | Size limit | Storage |
|---|---|---|---|---|
| **Index cache** | Session + disk | HEAD SHA change, `.gitignore` mtime | 128 MB | `.pearl/cache/index.bin` |
| **Token count cache** | Session | Never (content hash key) | 10,000 entries LRU | In-memory |
| **System prompt cache** | Session | Never | 1 entry | In-memory |
| **Tool definition cache** | Session | Never | 1 entry | In-memory |
| **Search result cache** | Turn | End of turn | 50 entries LRU | In-memory |
| **File stat cache** | Turn | End of turn | 1,000 entries | In-memory |

### 11.2 Cache Implementation Rules

**Rule CACHE-1:** Every cache MUST have a defined maximum size. Unbounded caches
are memory leaks. Use `functools.lru_cache` for simple function-level caches or
a purpose-built LRU structure for complex caches.

```python
# CORRECT — bounded LRU cache
@functools.lru_cache(maxsize=10_000)
def _count_tokens_cached(content_hash: str, content: str) -> int:
    return _expensive_token_count(content)

# VIOLATION — no size limit
_token_cache: dict[str, int] = {}  # unbounded; grows forever
```

**Rule CACHE-2:** Cache keys MUST be stable across Python interpreter restarts.
Do not use `id()`, object identity, or memory addresses as cache keys. Use
deterministic keys: file paths, content hashes, parameter tuples.

**Rule CACHE-3:** Disk caches MUST include a format version field. When Pearl
is updated and the cache format changes, the old cache MUST be invalidated by
comparing the format version, not by deleting the cache directory on install.

```python
# CORRECT — version-aware cache loading
def _load_cache(path: Path) -> Index | None:
    data = msgpack.unpackb(path.read_bytes())
    if data.get("format_version") != CACHE_FORMAT_VERSION:
        logger.info("cache_format_version_mismatch: rebuilding index")
        return None
    return Index.from_dict(data)
```

**Rule CACHE-4:** Cache reads MUST be instrumented. Log cache hits and misses at
DEBUG level. A cache hit rate below 80% on a warm session is a sign that the cache
key is wrong or the cache is being evicted too aggressively.

### 11.3 Cache Invalidation Rules

**Rule CACHE-5:** The index cache MUST be invalidated when any of these conditions
are true:

1. `git rev-parse HEAD` returns a different SHA than when the cache was built
2. `.gitignore` has been modified (mtime change)
3. `.pearlignore` has been modified (mtime change)
4. The cache format version field does not match the current Pearl version

**Rule CACHE-6:** Turn-scoped caches (search results, file stats) MUST be cleared
at the start of every new agent turn. Retaining them across turns produces stale
data in multi-turn sessions where files change between turns.

**Rule CACHE-7:** Never cache security-sensitive data. API keys, secrets,
authentication tokens, and file contents from `.env` files MUST NOT be stored in
any cache. See `04_SECURITY_GUIDELINES.md`, Section 4.

---

## 12. Memory Management

### 12.1 Memory Lifecycle

**Rule MEM-3:** Objects that hold large amounts of data (file contents, search
results, context buffers) MUST be explicitly released after use. Do not rely on
Python's garbage collector alone to release large allocations promptly. Use `del`
to dereference large objects at the end of their scope.

```python
# CORRECT — explicit release of large buffer
async def _process_large_result(result: bytes) -> str:
    processed = _transform(result)
    del result  # release large buffer before next operation
    return processed

# VIOLATION — large buffer retained until GC runs
async def _process_large_result(result: bytes) -> str:
    return _transform(result)  # result stays alive until function returns
```

**Rule MEM-4:** Streaming responses from the LLM MUST be processed chunk-by-chunk
and MUST NOT be buffered fully in memory before display. The streaming buffer MUST
flush at least every **4 KB** or every **100 ms**, whichever comes first.

**Rule MEM-5:** The `ContextBuilder` working set MUST be explicitly freed after
the LLM call completes. Context objects, file excerpts, and history items MUST NOT
persist on the heap past the turn boundary.

### 12.2 Memory Leak Prevention

**Rule MEM-6:** Any object that registers event listeners, callbacks, or watchers
MUST deregister them on destruction or session end. Failure to deregister
long-lived callbacks against short-lived objects is the most common source of
memory leaks in Python async code.

```python
# CORRECT — cleanup on destruction
class FileWatcher:
    def __init__(self) -> None:
        self._observer = Observer()
        self._observer.schedule(self._handler, str(self._root))
        self._observer.start()

    def close(self) -> None:
        self._observer.stop()
        self._observer.join()

# VIOLATION — observer never stopped; thread leaks on session end
class FileWatcher:
    def __init__(self) -> None:
        self._observer = Observer()
        self._observer.schedule(self._handler, str(self._root))
        self._observer.start()
    # no close() method
```

**Rule MEM-7:** Do not use class-level mutable default arguments. They create
shared state between instances that causes incorrect cross-session behavior and
can accumulate data indefinitely.

```python
# CORRECT — instance-level state
class ContextBuilder:
    def __init__(self) -> None:
        self._items: list[ContextItem] = []  # new list per instance

# VIOLATION — shared mutable class-level default
class ContextBuilder:
    _items: list[ContextItem] = []  # shared across ALL instances
```

### 12.3 Memory Profiling Gates

**Rule MEM-8:** The peak RSS of the Python process MUST be measured in CI for the
standard benchmark suite (Section 24). The measurement MUST appear in the CI
performance report. Any run that exceeds **512 MB** is a blocking failure.

---

## 13. Parallel Execution Rules

### 13.1 When to Parallelize

**Rule PAR-1:** When a tool call produces a list of items that each require
independent I/O (file reads, stat calls, network requests), those operations
MUST be performed in parallel using `asyncio.gather()`, subject to the concurrency
limits in Rule PAR-2.

**Rule PAR-2:** Parallel I/O operations MUST be rate-limited using a semaphore.
The default semaphore limits are:

| Resource | Max concurrent operations |
|---|---|
| File reads | 32 |
| File writes (patch application) | 8 |
| Shell subprocesses | 4 |
| LLM API calls | 1 (no parallel LLM calls by default) |
| Index batch processing | CPU count |

```python
# CORRECT — semaphore-limited parallel reads
_READ_SEM = asyncio.Semaphore(32)

async def read_file_limited(path: Path) -> str:
    async with _READ_SEM:
        return await asyncio.to_thread(path.read_text, encoding="utf-8")

async def read_files(paths: list[Path]) -> list[str]:
    return await asyncio.gather(*[read_file_limited(p) for p in paths])
```

**Rule PAR-3:** Parallel LLM calls are permitted only for explicitly parallelized
multi-agent tasks (future feature). In all current sprint work, LLM calls MUST be
sequential within a single agent turn. Parallel LLM calls that are not explicitly
coordinated produce non-deterministic tool call ordering and break the approval flow.

### 13.2 Parallelism and Safety

**Rule PAR-4:** Parallel operations MUST NOT write to the same file. The file write
semaphore (limit 8) prevents I/O contention, but it does not prevent multiple
co-routine paths from targeting the same file. The `ChangeManager` MUST validate
that no two concurrent patches target the same file path before acquiring the write
semaphore.

**Rule PAR-5:** Parallel operations MUST propagate cancellation. If the user cancels
an operation while parallel tasks are running, all pending tasks in the
`asyncio.gather()` call MUST be cancelled and awaited for cleanup.

```python
# CORRECT — propagates cancellation
async def _parallel_tool_calls(calls: list[ToolCall]) -> list[ToolResult]:
    tasks = [asyncio.create_task(_run_call(c)) for c in calls]
    try:
        return await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
```

---

## 14. Async Execution Standards

### 14.1 Event Loop Discipline

**Rule ASYNC-1:** The Python backend MUST run a single event loop for the lifetime
of the session. Creating and destroying event loops within a session is prohibited.
Use `asyncio.get_event_loop()` at the module level for libraries that require it;
do not call `asyncio.new_event_loop()` in tool handlers.

**Rule ASYNC-2:** Synchronous blocking calls MUST NOT be made from within a
coroutine. Any operation that may block for more than **1 ms** MUST be offloaded
to a thread pool using `asyncio.to_thread()`. Examples of blocking calls that MUST
be offloaded: file I/O, `subprocess.run()`, regex on large strings, JSON parsing
of large payloads.

```python
# CORRECT — blocking JSON parse offloaded
async def _parse_large_json(raw: str) -> dict:
    return await asyncio.to_thread(json.loads, raw)

# VIOLATION — blocks the event loop for large inputs
async def _parse_large_json(raw: str) -> dict:
    return json.loads(raw)  # synchronous, blocks event loop
```

**Rule ASYNC-3:** `asyncio.sleep(0)` MUST be inserted in long-running synchronous
loops that run within a coroutine and cannot be fully offloaded to `to_thread()`.
This yields control back to the event loop to allow other tasks to run.

### 14.2 Task Management

**Rule ASYNC-4:** Background tasks MUST be tracked in a set and awaited on session
shutdown. A task that is created with `asyncio.create_task()` and then forgotten
will suppress its exceptions and may continue running after the session ends.

```python
# CORRECT — tracked background task
class PearlAgent:
    def __init__(self) -> None:
        self._background_tasks: set[asyncio.Task] = set()

    def _start_background_task(self, coro: Coroutine) -> None:
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def close(self) -> None:
        for task in list(self._background_tasks):
            task.cancel()
        await asyncio.gather(*self._background_tasks, return_exceptions=True)
```

**Rule ASYNC-5:** `asyncio.gather()` MUST be called with `return_exceptions=True`
when the caller intends to handle individual task failures. Without it, the first
exception cancels all remaining tasks and raises immediately.

### 14.3 Timeout Discipline

**Rule ASYNC-6:** Every external call (LLM API, shell command, file watcher event)
MUST have an explicit `asyncio.wait_for()` timeout. The default timeouts are:

| Operation | Default timeout | Override |
|---|---|---|
| LLM API call | 120 s | User-configurable up to 300 s |
| Shell command | 30 s | User-configurable up to 300 s |
| File read (single file) | 5 s | Not configurable |
| MCP handshake | 10 s | Not configurable |
| Index batch (100 files) | 15 s | Not configurable |

**Rule ASYNC-7:** `asyncio.TimeoutError` MUST be caught and wrapped in a
`PearlToolError` with a user-readable message. Never let `TimeoutError` propagate
to the MCP layer as an unhandled exception.

---

## 15. Large Repository Handling

### 15.1 Scope Limiting

**Rule IDX-9:** When the repository exceeds 10,000 files, Pearl MUST activate
scope-limited indexing. The scope is determined by:

1. The user's configured `pearl.scope` setting (explicit path prefix)
2. The most recently modified 10,000 files (fallback heuristic)

**Rule IDX-10:** Pearl MUST inform the user when scope limiting is active, including
the effective scope and the number of files excluded. This is a UX requirement
(`05_UI_UX_GUIDELINES.md`, Section 8) as well as a performance requirement.

### 15.2 Streaming and Pagination

**Rule PERF-21:** For Large repositories, file listings, search results, and
symbol lookup results MUST be paginated or streamed. No tool call in a Large
repository context MUST return more than **500** items in a single result. This
limit applies even when the underlying data source has more matches.

**Rule PERF-22:** When a search result is truncated at the large-repository limit,
the truncation MUST be noted in the tool result with the exact count of additional
matches not returned. The LLM can then request a more specific query.

```python
# CORRECT — truncation with count
def _format_truncated_results(results: list[SearchResult], limit: int) -> str:
    shown = results[:limit]
    remaining = len(results) - limit
    output = "\n".join(r.format() for r in shown)
    if remaining > 0:
        output += f"\n\n[{remaining} additional matches not shown — refine your query]"
    return output
```

### 15.3 Monorepo Support

**Rule IDX-11:** Monorepo support REQUIRES a user-configured scope path before
indexing proceeds. Pearl MUST NOT attempt to auto-detect the correct scope for a
monorepo; the developer knows the codebase; Pearl does not.

**Rule IDX-12:** In a scoped monorepo session, cross-scope symbol references (a
call to a symbol in an unindexed subtree) MUST be noted in the search result as
"symbol found outside current scope — expand scope to resolve." This prevents the
LLM from silently generating incorrect cross-package references.

---

## 16. Incremental Indexing

### 16.1 Change Detection

**Rule IDX-13:** Incremental indexing MUST be triggered by the file watcher
(Section 5.4), not by polling. The watcher event provides the exact set of changed
files; the incremental indexer updates only those files in the index.

**Rule IDX-14:** Incremental index updates for ≤ 50 changed files MUST complete
within **2 seconds** on the reference environment. The implementation MUST NOT
re-read unchanged files; it MUST use the existing index for all unchanged entries.

```
Incremental indexing flow:

  File watcher event → debounce (500 ms) → changed_files list
  │
  ├── For each changed file:
  │   ├── Re-parse and extract symbols
  │   ├── Update index entry
  │   └── Invalidate token count cache for that file
  │
  └── Emit `index_updated` progress event
```

### 16.2 Index Consistency

**Rule IDX-15:** During an incremental index update, queries against the index
MUST be deferred or served from the previous index snapshot until the update
completes. A partial index state (some files updated, others not) produces
incorrect search results.

**Rule IDX-16:** The incremental indexer MUST handle file deletions: when a file
is deleted, all its symbols MUST be removed from the inverted index. Orphaned
symbol entries that point to deleted files cause false-positive search results.

### 16.3 Index Persistence Frequency

**Rule IDX-17:** The updated index MUST be persisted to disk within **30 seconds**
of the last incremental update. Do not persist after every single file change —
batch persistence reduces disk write amplification.

**Rule IDX-18:** Index persistence MUST be atomic (see Rule IDX-6). The
persistence operation MUST be performed in a background task, not in the file
watcher callback, to avoid blocking the watcher event loop.

---

## 17. Lazy Loading Standards

### 17.1 Module-Level Lazy Loading

**Rule PERF-23:** Any module that is not required for CLI startup or the initial
MCP handshake MUST be lazy-loaded on first use. The lazy import pattern is
defined in Rule PERF-12.

The following modules are classified as lazy-load required:

| Module | Trigger for load |
|---|---|
| `tiktoken` | First token count operation |
| `tree_sitter` | First AST parse operation |
| `git` (gitpython) | First git operation |
| `watchdog` | First file watcher start |
| `msgpack` | First index serialization |
| Any LLM provider SDK | First LLM call |

### 17.2 Data Lazy Loading

**Rule PERF-24:** Large data structures (the full symbol table, the file content
cache) MUST be loaded on demand, not eagerly at startup. The startup path MUST
load only the metadata required to answer "is the index current?" — the index
itself is loaded only when the first query arrives.

**Rule PERF-25:** Lazy-loaded data MUST have a clear ownership model:
the first caller that triggers the load owns the load operation; subsequent callers
that arrive while loading MUST await the in-progress load, not trigger a second
concurrent load.

```python
# CORRECT — single-flight lazy load
class IndexManager:
    def __init__(self) -> None:
        self._index: Index | None = None
        self._loading: asyncio.Task | None = None

    async def get_index(self) -> Index:
        if self._index is not None:
            return self._index
        if self._loading is None:
            self._loading = asyncio.create_task(self._load())
        return await self._loading

    async def _load(self) -> Index:
        self._index = await _read_index_from_disk()
        return self._index
```

---

## 18. Background Processing

### 18.1 Background Task Rules

**Rule ASYNC-8:** Background tasks MUST NOT block the agent turn. Any process that
takes longer than **100 ms** and is not required for the immediate tool result MUST
be offloaded to a background task.

**Rule ASYNC-9:** Background tasks MUST NOT silently swallow exceptions. All
background task exceptions MUST be logged at ERROR level with the full traceback.
A background indexing failure is not fatal, but it MUST be visible.

```python
# CORRECT — exception-safe background task wrapper
async def _run_background(coro: Coroutine, label: str) -> None:
    try:
        await coro
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("background_task_failed", label=label)
```

**Rule ASYNC-10:** Background tasks MUST be cancellable. Every background task
MUST respond to `asyncio.CancelledError` by cleaning up any partially-written state
and re-raising. A background task that ignores cancellation blocks session shutdown.

### 18.2 Background Task Priority

Background tasks in Pearl are prioritized in two tiers:

| Tier | Examples | CPU priority | Cancellable mid-operation |
|---|---|---|---|
| **Critical** | Checkpoint creation, patch write | Normal | No (must complete atomically) |
| **Opportunistic** | Incremental indexing, index gc | Below normal | Yes (safe to interrupt) |

**Rule ASYNC-11:** Opportunistic background tasks MUST call `asyncio.sleep(0)` at
regular intervals to yield to the event loop. An opportunistic task that saturates
the event loop for > 10 ms at a time degrades the agent's responsiveness.

---

## 19. Resource Utilization

### 19.1 CPU Budget

| State | CPU budget | Enforcement |
|---|---|---|
| Idle (REPL waiting, extension connected) | < 1% | CI idle test |
| Active agent turn (tool calls) | < 80% (soft) | Monitoring |
| Repository indexing (background) | < 50% | `nice`/`ionice` on Linux |
| File watching (idle monitoring) | < 2% | CI idle test |

**Rule CPU-1:** When Pearl is idle (no active agent turn), CPU usage MUST fall
below **1%** within 5 seconds of the last operation completing. CPU above 1% at
idle is a sign of a runaway background task or a missing `await` in a tight loop.

**Rule CPU-2:** Incremental indexing and full indexing MUST be run at reduced
process priority on platforms that support it (`os.nice(5)` on Linux/macOS).
Indexing MUST NOT compete with the developer's IDE or build tools for CPU.

### 19.2 Disk I/O Budget

| Operation | Disk I/O budget | Notes |
|---|---|---|
| Checkpoint creation | < 10 MB written | Shadow git repo delta |
| Incremental index persist | < 20 MB written | Compressed binary format |
| Full index rebuild | < 50 MB written | One-time per HEAD SHA |
| Log rotation | < 5 MB/session | Log files are size-limited |

**Rule IO-10:** Pearl MUST NOT write to the user's workspace except through the
`ChangeManager` after explicit approval. Background tasks (indexing, checkpointing)
write only to `.pearl/` inside the workspace or to the configured cache directory.

**Rule IO-11:** Log files MUST be capped at **10 MB** with rotation. Pearl MUST
NOT fill the disk with logs during a long session.

---

## 20. CPU Optimization

### 20.1 Hot Path Identification

The following operations are classified as **hot paths** — they execute on every
agent turn or on every tool dispatch:

1. MCP request parsing and routing
2. Token count retrieval (cached)
3. Context priority sorting
4. Progress event serialization and emission
5. LLM response chunk processing

**Rule CPU-3:** Hot paths MUST NOT perform dict or list comprehensions that
allocate unnecessarily. Pre-allocate fixed-size structures where the size is known.

**Rule CPU-4:** Hot paths MUST NOT perform string concatenation in a loop. Use
`"".join(parts)` or `io.StringIO` for multi-part string assembly.

```python
# CORRECT — join for string assembly
def _format_context(items: list[ContextItem]) -> str:
    return "\n\n".join(item.text for item in items)

# VIOLATION — quadratic string concatenation
def _format_context(items: list[ContextItem]) -> str:
    result = ""
    for item in items:
        result += item.text + "\n\n"  # O(N²) allocations
    return result
```

### 20.2 Algorithm Complexity

**Rule CPU-5:** Any algorithm that operates on the full file list or the full index
MUST be O(n) or better. Quadratic algorithms on the index are prohibited and will
be caught by the benchmark suite at N = 10,000.

**Rule CPU-6:** Sorting operations MUST NOT be performed inside loops. Extract
the sorted list once; iterate the sorted result.

### 20.3 Regex Performance

**Rule CPU-7:** Regular expressions that are used more than once MUST be compiled
at module load time with `re.compile()`. Recompiling the same pattern on every call
is measurable overhead in high-frequency search paths.

```python
# CORRECT — compiled at module level
_TODO_PATTERN = re.compile(r"\bTODO\b|\bFIXME\b|\bHACK\b", re.IGNORECASE)

# VIOLATION — compiled on every call
def find_todos(text: str) -> list[str]:
    return re.findall(r"\bTODO\b|\bFIXME\b|\bHACK\b", text, re.IGNORECASE)
```

---

## 21. Memory Optimization

### 21.1 Data Structure Selection

**Rule MEM-9:** Use `__slots__` on classes that are instantiated in large numbers
(e.g., index entries, context items). `__slots__` reduces per-instance memory by
40–60% by replacing the instance `__dict__` with a fixed struct.

```python
# CORRECT — slots for high-count objects
@dataclass
class IndexEntry:
    __slots__ = ("path", "symbols", "mtime", "size")
    path: str
    symbols: list[str]
    mtime: float
    size: int

# VIOLATION — __dict__ overhead for every entry
@dataclass
class IndexEntry:
    path: str
    symbols: list[str]
    mtime: float
    size: int
```

**Rule MEM-10:** Use generators instead of list comprehensions wherever the full
list is not required simultaneously. Generators process items one at a time and
have O(1) memory cost regardless of input size.

```python
# CORRECT — generator (O(1) memory)
def iter_relevant_files(index: Index, query: str) -> Iterator[IndexEntry]:
    return (entry for entry in index.entries if query in entry.symbols)

# VIOLATION — materializes entire list (O(N) memory)
def iter_relevant_files(index: Index, query: str) -> list[IndexEntry]:
    return [entry for entry in index.entries if query in entry.symbols]
```

### 21.2 String Interning

**Rule MEM-11:** File path strings that appear in many index entries MUST be
interned with `sys.intern()`. File paths are repeated across many index entries;
interning them reduces memory by deduplicating the string objects.

```python
# CORRECT — intern repeated path strings
entry = IndexEntry(path=sys.intern(str(file_path)), ...)
```

### 21.3 Buffer Management

**Rule MEM-12:** LLM response streaming buffers MUST be pre-allocated at a fixed
size (4 KB) and flushed when full, rather than accumulating the full response
before flushing. This bounds peak memory usage for large LLM responses to O(1)
rather than O(response length).

---

## 22. Disk I/O Optimization

### 22.1 Read Amplification

**Rule IO-12:** Index reads MUST NOT re-read the index from disk more than once
per session per HEAD SHA. The in-memory index is authoritative; disk reads are only
for cold starts and after a HEAD SHA change.

**Rule IO-13:** When multiple tool calls in a single turn read the same file,
the file content MUST be served from the turn-scoped cache (Section 11.1) after
the first read. File stat calls and content reads MUST NOT be duplicated within
a turn.

### 22.2 Write Amplification

**Rule IO-14:** Do not write intermediate state to disk during a tool call.
Tool calls are transient; their intermediate state belongs in memory. Write to
disk only for:
1. Approved patches (via `ChangeManager`)
2. Index persistence (periodic, atomic)
3. Checkpoints (via shadow git)
4. Logs (append-only, size-limited)

**Rule IO-15:** Log writes MUST be buffered with a write buffer of at least
**4 KB**. Synchronous unbuffered log writes on every debug statement saturate
disk I/O during high-frequency operations.

### 22.3 Compression

**Rule IO-16:** The on-disk index MUST be compressed (zstd or lz4) to reduce
disk space usage and I/O bandwidth during reads and writes. Use `lz4` for hot
data (index rebuilt frequently); use `zstd` for cold data (archived session logs).

---

## 23. Network Optimization

### 23.1 Connection Management

**Rule NET-1:** The LLM API client MUST maintain a persistent HTTP/2 or HTTP/1.1
keep-alive connection to the provider. Creating a new TCP connection for each LLM
call adds 50–200 ms of round-trip latency on every turn.

**Rule NET-2:** HTTP client instances MUST be created once at session start and
reused. Never create a new `httpx.AsyncClient` or equivalent inside a tool handler
or per-request function.

```python
# CORRECT — shared client at session scope
class LLMClient:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0),
            http2=True,
        )

    async def close(self) -> None:
        await self._http.aclose()

# VIOLATION — new client per call
async def call_llm(messages: list) -> str:
    async with httpx.AsyncClient() as client:  # new TCP connection every turn
        ...
```

### 23.2 Request Optimization

**Rule NET-3:** LLM API requests MUST include only the fields the provider
requires. Sending large, empty, or null optional fields wastes request bandwidth
and may trigger provider-side parsing overhead.

**Rule NET-4:** Provider-side prompt caching MUST be enabled for all requests
where the system prompt and tool definitions are unchanged from the previous turn.
Cache hits eliminate the input token cost of the static portions of the prompt.

---

## 24. Benchmarking Standards

### 24.1 Benchmark Suite Structure

Pearl maintains a canonical benchmark suite in `tests/benchmarks/`. Every rule
that specifies a numeric budget MUST have a corresponding benchmark that enforces
it.

**Rule BENCH-1:** The benchmark suite MUST be run on every PR that touches any
file in `src/`, `vscode-extension/src/`, or `tests/`. It MUST NOT be optional
or skipped in CI.

**Rule BENCH-2:** Benchmark results MUST be reported in a structured format:

```json
{
  "benchmark": "full_index_10k_files",
  "duration_ms": 18420,
  "budget_ms": 30000,
  "status": "PASS",
  "environment": {
    "cpu": "8-core",
    "ram_gb": 16,
    "python_version": "3.11.4",
    "platform": "linux"
  }
}
```

**Rule BENCH-3:** Benchmark fixtures (reference repositories, synthetic file
trees) MUST be checked into `tests/fixtures/`. Benchmarks MUST be reproducible
from the fixture alone, without network access or external dependencies.

### 24.2 Benchmark Reference Fixtures

| Fixture | File count | Description | Used by |
|---|---|---|---|
| `small_repo` | 500 files | Synthetic Python project | Startup, warm-index tests |
| `medium_repo` | 5,000 files | Mixed Python/TS project | Most benchmarks |
| `large_repo` | 10,000 files | Realistic full-stack repo | Budget boundary tests |
| `monorepo` | 50,000 files | Synthetic monorepo | Scoping and limits |
| `binary_heavy_repo` | 2,000 files (30% binary) | Repo with images, PDFs | Ignore rule tests |

### 24.3 Benchmark Table

The following table defines the full benchmark suite as of this document version.
CI uses this table to determine pass/fail.

| Benchmark ID | Operation | Fixture | Budget | Metric |
|---|---|---|---|---|
| `B-001` | CLI cold start | `small_repo` | 3 s | Wall clock to REPL |
| `B-002` | CLI warm start | `small_repo` | 1 s | Wall clock to REPL |
| `B-003` | Full index build | `large_repo` | 30 s | Index start to complete |
| `B-004` | Incremental index (50 files) | `large_repo` | 2 s | Change to index updated |
| `B-005` | Index cache load | `large_repo` | 2 s | Disk to in-memory |
| `B-006` | Symbol lookup | `large_repo` | 10 ms | Query to result |
| `B-007` | `search_text` (10k files) | `large_repo` | 2 s | MCP request to response |
| `B-008` | `find_references` (10k files) | `large_repo` | 3 s | MCP request to response |
| `B-009` | `read_file` (1 MB file) | `medium_repo` | 100 ms | MCP request to response |
| `B-010` | Context assembly (80k context) | `medium_repo` | 200 ms | Build start to complete |
| `B-011` | Token count (8k tokens) | `medium_repo` | 20 ms | Cached count retrieval |
| `B-012` | Approval diff render | `medium_repo` | 300 ms | Trigger to display |
| `B-013` | Checkpoint creation | `medium_repo` | 2 s | Approved to committed |
| `B-014` | Peak RSS (full session) | `large_repo` | 512 MB | `psutil` peak RSS |
| `B-015` | Idle CPU (30s idle) | `large_repo` | < 1% | `psutil` cpu_percent |
| `B-016` | Cancellation | `medium_repo` | 2 s | Ctrl-C to cancelled |
| `B-017` | MCP dispatch overhead | `small_repo` | 10 ms | Protocol overhead only |
| `B-018` | Monorepo scope limit | `monorepo` | 5 s | Scope detect to ready |

---

## 25. Performance Regression Testing

### 25.1 Regression Gates

**Rule REG-1:** No PR that introduces a benchmark regression of more than **10%**
on any metric in Section 24.3 is accepted without an explicit performance budget
exception signed off by the Lead Architect. A 10% regression is defined as:

```
regression_pct = (new_value - baseline_value) / baseline_value * 100
```

**Rule REG-2:** Benchmark baselines MUST be stored in `tests/benchmarks/baseline.json`
and committed to the repository. The baseline is updated only when a deliberate,
documented performance improvement is made. Accidental updates to baseline are
rejected in code review.

```json
{
  "B-003": { "budget_ms": 30000, "baseline_ms": 18500, "updated": "2026-07-01" },
  "B-007": { "budget_ms": 2000, "baseline_ms": 980, "updated": "2026-07-01" }
}
```

**Rule REG-3:** The CI performance report MUST compare the current run against
the baseline and highlight any delta > 5% (warning) or > 10% (failure). Both
regressions and improvements MUST be flagged — an unexpected improvement may
indicate a test fixture change, not a real performance gain.

### 25.2 Regression Investigation Protocol

When a regression is detected:

```
Step 1: Reproduce locally
  └── Run the failing benchmark in isolation on the development machine
  └── Confirm the regression is consistent (not a flaky measurement)

Step 2: Bisect
  └── git bisect against the last known-good commit
  └── Identify the commit that introduced the regression

Step 3: Profile
  └── Run the profiler (Section 26) on the regressed benchmark
  └── Identify the function responsible for the regression

Step 4: Fix or file
  └── If fixable within the sprint: fix in the same PR
  └── If not fixable: file a P1 performance bug; do not merge the regression
```

**Rule REG-4:** A performance regression that is not fixable within the current
sprint MUST be reverted before the sprint closes. Regressions are not allowed
to accumulate in main. "We'll fix it next sprint" is not an accepted resolution.

---

## 26. Profiling Guidelines

### 26.1 Profiler Selection

| Profiler | Use case | Command |
|---|---|---|
| `cProfile` | Synchronous CPU profiling | `python -m cProfile -o prof.out -m pearl ...` |
| `asyncio` `loop.set_debug(True)` | Async event loop slow-callback detection | Set in dev config |
| `py-spy` | Live CPU profiling of running process | `py-spy record -o profile.svg --pid <PID>` |
| `memray` | Memory allocation profiling | `memray run -o output.bin -m pearl ...` |
| `scalene` | Combined CPU + memory + GPU | `scalene --cpu --memory src/` |
| `yappi` | Coroutine-aware profiling | `yappi.start(builtins=True)` |

**Rule PROF-1:** Before filing a performance bug or submitting a performance-fix
PR, the contributor MUST attach profiling output that identifies the bottleneck.
"It feels slow" is not a sufficient description. Attach the `py-spy` SVG or
`cProfile` stats.

**Rule PROF-2:** Profile in release mode, not debug mode. Debug logging, `assert`
statements, and `asyncio.loop.set_debug(True)` all add overhead that distorts
profiling results.

**Rule PROF-3:** Profile against the canonical benchmark fixtures (Section 24.2),
not against the contributor's personal repository. Personal repositories vary in
size and content; fixture-based profiling is reproducible.

### 26.2 Profiling Workflow

```
Performance investigation workflow:

1. Establish baseline
   └── Run benchmark suite; record median of 5 runs

2. Reproduce the scenario
   └── Run the specific operation that is slow
   └── Time it with `time.perf_counter()` in dev mode

3. CPU profile
   └── py-spy record --pid <pid> --output cpu.svg
   └── Identify hot functions (> 5% of total time)

4. Memory profile (if RSS is the concern)
   └── memray run --output mem.bin python -m pearl <scenario>
   └── memray flamegraph mem.bin

5. Correlate with benchmarks
   └── Which benchmark does this scenario correspond to?
   └── Is the budget exceeded?

6. Optimize and re-measure
   └── Apply optimization
   └── Re-run benchmark suite
   └── Compare before/after (must show ≥ 10% improvement to justify change)
```

### 26.3 Before/After Optimization Example

```
Before optimization — context_assembly benchmark (B-010):

  cProfile output:
    ncalls  tottime  percall  cumtime  percall filename:lineno(function)
     1      0.000    0.000    0.842    0.842   context_builder.py:45(_assemble)
   400      0.412    0.001    0.820    0.002   context_builder.py:112(_count_tokens)
   400      0.408    0.001    0.408    0.001   tokenizer.py:34(_count)

  Root cause: token count called 400× without caching; each call invokes
              the tokenizer synchronously.

After optimization — add LRU cache on content hash:

  cProfile output:
    ncalls  tottime  percall  cumtime  percall filename:lineno(function)
     1      0.000    0.000    0.031    0.031   context_builder.py:45(_assemble)
   400      0.001    0.000    0.001    0.000   context_builder.py:112(_count_tokens)
     1      0.030    0.030    0.030    0.030   tokenizer.py:34(_count)

  Improvement: 0.842 s → 0.031 s (96.3% reduction)
  Budget: 200 ms — now PASS
```

---

## 27. Monitoring and Metrics

### 27.1 Metrics Taxonomy

Pearl defines three tiers of metrics:

| Tier | Description | Emission frequency | Retention |
|---|---|---|---|
| **Operational** | Real-time per-turn metrics | Every turn | Session |
| **Benchmark** | CI gate metrics | Every PR | 90 days |
| **Trend** | Aggregated across releases | Every release | Permanent |

### 27.2 Required Operational Metrics

**Rule MON-1:** The following metrics MUST be emitted at DEBUG level on every
agent turn:

| Metric | Field name | Type | Description |
|---|---|---|---|
| Turn duration | `turn_duration_ms` | int | Wall clock from user input to response complete |
| LLM call duration | `llm_duration_ms` | int | From dispatch to last streaming token |
| Tool call count | `tool_call_count` | int | Number of tool calls in this turn |
| Prompt tokens | `prompt_tokens` | int | Total input tokens (from provider usage) |
| Completion tokens | `completion_tokens` | int | Total output tokens |
| Context headroom | `context_headroom` | int | Tokens remaining after context assembly |
| Cache hit rate | `token_cache_hit_rate` | float | Fraction of token counts served from cache |
| Peak RSS | `peak_rss_mb` | int | Peak memory for this turn |

```python
# CORRECT — structured turn metrics
logger.debug(
    "turn_complete",
    turn_duration_ms=int((t_end - t_start) * 1000),
    llm_duration_ms=int(llm_duration * 1000),
    tool_call_count=len(tool_calls),
    prompt_tokens=usage.input_tokens,
    completion_tokens=usage.output_tokens,
    context_headroom=headroom,
    token_cache_hit_rate=cache.hit_rate(),
    peak_rss_mb=_peak_rss_mb(),
)
```

### 27.3 Performance Health Dashboard

For extended sessions, Pearl SHOULD emit a performance health summary every
**10 turns** at INFO level:

```
[PERF HEALTH] Turns: 10 | Avg turn: 4.2 s | Avg LLM: 2.1 s | Avg tools: 2.1 s
              Context: 62% used | Cache hit rate: 94% | RSS: 284 MB
              Index: current (last update: 14 s ago)
```

**Rule MON-2:** When any metric crosses a warning threshold, Pearl MUST emit a
log at WARNING level. Warning thresholds:

| Metric | Warning threshold |
|---|---|
| Turn duration | > 20 s |
| Context utilization | > 70% |
| Peak RSS | > 400 MB |
| Token cache hit rate | < 80% |
| Index staleness | > 60 s since last update |

---

## 28. Performance Logging

### 28.1 Logging Levels for Performance

| Level | Content | Audience |
|---|---|---|
| `DEBUG` | Per-turn metrics, per-operation timings, cache events | Developer debugging |
| `INFO` | Turn completion summary, index events, health reports | Standard operation |
| `WARNING` | Budget threshold crossings, cache miss spikes | Operator attention |
| `ERROR` | Budget violations, profiling failures | Immediate attention |

**Rule PERF-26:** Performance log messages MUST use structured logging (key=value
pairs via `structlog` or `logging.extra`). Plain-string performance logs that cannot
be machine-parsed are not acceptable.

```python
# CORRECT — structured, parseable
logger.debug("index_query", symbol="MyClass", duration_ms=3, result_count=5)

# VIOLATION — unstructured, unparseable
logger.debug(f"Index query for 'MyClass' took 3ms, found 5 results")
```

**Rule PERF-27:** Timing measurements MUST use `time.perf_counter()`, not
`time.time()`. `time.time()` has platform-dependent resolution and can go backward
on some platforms during NTP adjustments. `perf_counter()` is monotonic and
high-resolution.

### 28.2 Log Sampling

**Rule PERF-28:** In production use (outside of development mode), DEBUG-level
performance logs MUST be sampled at **10%** to avoid log I/O becoming a
performance bottleneck in itself. Sampling MUST be deterministic (hash-based, not
random) so that specific slow turns are reproducible.

```python
# CORRECT — deterministic 10% sampling
def _should_log_debug(turn_id: str) -> bool:
    return int(hashlib.md5(turn_id.encode()).hexdigest(), 16) % 10 == 0
```

---

## 29. Performance Anti-Patterns

### 29.1 Anti-Pattern Catalog

**Anti-Pattern AP-PERF-1: The Eager Index**

> **Description:** Loading the entire repository index into memory at startup,
> before the user has made any request.
>
> **Symptom:** Slow cold start (> 5 s), high startup RSS.
>
> **Root cause:** The developer treats the index as a prerequisite for any
> operation, without recognizing that the warm path serves 90% of operations.
>
> **Correct approach:** Load index metadata at startup (< 100 ms); load the full
> index on first query (lazy, single-flight).

---

**Anti-Pattern AP-PERF-2: The Context Carpet-Bomb**

> **Description:** Including all recently read files in the context on every turn,
> regardless of relevance.
>
> **Symptom:** Context utilization > 90% on turn 2; LLM responses degrade in quality;
> pruning drops critical instructions.
>
> **Root cause:** "More context = better reasoning" — true up to the budget, false
> beyond it. Irrelevant context displaces relevant context.
>
> **Correct approach:** Use the index to rank file relevance; include only the top-k
> most relevant excerpts. Follow the priority ordering in Rule CTX-7.

---

**Anti-Pattern AP-PERF-3: The Synchronous Bottleneck**

> **Description:** A synchronous file read, subprocess call, or regex match placed
> directly in a coroutine, blocking the event loop.
>
> **Symptom:** All tool calls queue behind the blocking operation; perceived latency
> spikes for all concurrent tool calls.
>
> **Root cause:** Misunderstanding that `async def` does not make a function
> non-blocking; only `await asyncio.to_thread(...)` offloads blocking work.
>
> **Correct approach:** Rule IO-1, Rule ASYNC-2.

---

**Anti-Pattern AP-PERF-4: The Token Count Tax**

> **Description:** Calling the tokenizer on every piece of content in the context
> on every turn, without caching.
>
> **Symptom:** Context assembly takes 500 ms — 1 s; the profiler shows the tokenizer
> as the dominant cost.
>
> **Root cause:** Tokenizer is accurate but expensive; calling it on unchanged content
> is pure waste.
>
> **Correct approach:** Cache token counts by content hash (Rule TOK-4). For a warm
> session, 95% of token count calls should be cache hits.

---

**Anti-Pattern AP-PERF-5: The Infinite Background Task**

> **Description:** A background indexing or file-watching task that runs
> continuously at full CPU, even when idle.
>
> **Symptom:** CPU > 10% at idle; battery drain on laptops; IDE sluggishness.
>
> **Root cause:** A polling loop without a sleep, or a file watcher that fires on
> every OS event including `.DS_Store` changes.
>
> **Correct approach:** Rule IO-8 (OS-native watcher), Rule IO-9 (debouncing),
> Rule CPU-1 (idle CPU gate).

---

**Anti-Pattern AP-PERF-6: The Accumulating History**

> **Description:** Growing the conversation history without pruning, until it fills
> the context window and forces all file content out.
>
> **Symptom:** Turn 20+ produces responses that ignore file content; context
> utilization is 95% with conversation history; tool results are not visible to the LLM.
>
> **Root cause:** History is treated as append-only without a pruning policy.
>
> **Correct approach:** Rule TOK-7 (sliding window pruning), Rule TOK-8 (summarization).

---

**Anti-Pattern AP-PERF-7: The Uncapped Search Result**

> **Description:** Returning thousands of `search_text` matches to the LLM context.
>
> **Symptom:** A search for a common variable name fills the entire context with
> 3,000 match lines, displacing all other content.
>
> **Root cause:** The search cap (Rule PERF-19) is not enforced, or is enforced
> after the result is already assembled into the context.
>
> **Correct approach:** Enforce `MAX_SEARCH_RESULTS = 200` at the ripgrep layer,
> before the result is assembled. See `01_ARCHITECTURE_RULES.md`, Section 9.

---

**Anti-Pattern AP-PERF-8: The Redundant Validation Loop**

> **Description:** Re-validating paths, checking file existence, or stat-ing files
> that were already validated or stat-ed in the same tool call.
>
> **Symptom:** File operations take 3–5× longer than necessary; profiler shows
> repeated `os.stat()` calls for the same paths.
>
> **Root cause:** Each validation function defensively re-checks, without consulting
> the turn-scoped stat cache.
>
> **Correct approach:** Rule IO-13 (turn-scoped stat cache).

---

**Anti-Pattern AP-PERF-9: The N+1 Tool Call**

> **Description:** The LLM generates N tool calls to read N files sequentially,
> when a single batched tool call could read them in parallel.
>
> **Symptom:** Reading 10 files takes 10 × (tool dispatch + I/O) = 10+ seconds
> instead of 1 × (tool dispatch + parallel I/O).
>
> **Root cause:** Tool APIs expose single-file operations; the LLM calls them N times.
>
> **Correct approach:** Expose batched variants of common tools (`read_files`,
> `search_multiple`). Document the batch tools so the LLM uses them when reading
> multiple files is the plan.

---

**Anti-Pattern AP-PERF-10: The Context Rebuild**

> **Description:** Rebuilding the entire context from scratch on every turn,
> including re-counting tokens for unchanged content.
>
> **Symptom:** Context assembly is > 500 ms even on simple follow-up turns.
>
> **Root cause:** No incremental context management; every turn throws away the
> previous turn's assembled context and starts fresh.
>
> **Correct approach:** Cache the static portions (system prompt, tool definitions,
> unchanged history) across turns. Only rebuild the dynamic portion (current task
> context, new turns).

---

## 30. Common Performance Mistakes

### 30.1 Mistakes Catalog

**Mistake M-PERF-1: Timing with `time.time()` instead of `time.perf_counter()`**

```python
# WRONG
start = time.time()
result = await do_work()
duration = time.time() - start

# CORRECT
start = time.perf_counter()
result = await do_work()
duration = time.perf_counter() - start
```

Resolution: Replace all `time.time()` in timing code with `time.perf_counter()`.
`time.time()` is for calendar time; `perf_counter()` is for durations.

---

**Mistake M-PERF-2: Blocking the event loop with `subprocess.run()`**

```python
# WRONG — blocks event loop for entire command duration
async def run_git(args: list[str]) -> str:
    result = subprocess.run(["git"] + args, capture_output=True, text=True)
    return result.stdout

# CORRECT
async def run_git(args: list[str]) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await proc.communicate()
    return stdout.decode("utf-8")
```

---

**Mistake M-PERF-3: Forgetting `return_exceptions=True` in `asyncio.gather()`**

```python
# WRONG — first failure cancels all other tasks
results = await asyncio.gather(*tasks)

# CORRECT — all tasks run to completion; inspect results individually
results = await asyncio.gather(*tasks, return_exceptions=True)
errors = [r for r in results if isinstance(r, Exception)]
```

---

**Mistake M-PERF-4: Creating new HTTP client sessions per request**

```python
# WRONG — new TCP connection on every LLM call
async def call_llm(messages):
    async with httpx.AsyncClient() as client:
        return await client.post(...)

# CORRECT — reuse session-scoped client
async def call_llm(messages):
    return await self._http_client.post(...)
```

---

**Mistake M-PERF-5: Using `os.walk()` instead of `pathlib.rglob()` with filters**

```python
# WRONG — traverses ignored directories, applies filter too late
files = [
    Path(root) / name
    for root, dirs, names in os.walk(workspace)
    for name in names
    if not any(name.endswith(ext) for ext in IGNORED_EXTS)
]

# CORRECT — prune ignored directories during traversal
def _iter_workspace_files(root: Path, spec: PathSpec) -> Iterator[Path]:
    for f in root.rglob("*"):
        if f.is_file() and not spec.match_file(str(f.relative_to(root))):
            yield f
```

---

**Mistake M-PERF-6: Logging inside a tight loop at INFO level**

```python
# WRONG — log I/O on every file in a 10k-file index
for f in files:
    logger.info(f"Indexing {f}")  # 10,000 INFO log writes
    _index_file(f)

# CORRECT — log at batch level
for i, batch in enumerate(_batches(files, 100)):
    logger.info(f"Indexing batch {i+1}/{total_batches}")
    for f in batch:
        _index_file(f)
```

---

**Mistake M-PERF-7: Running ripgrep without `--max-count` in unbounded search**

```bash
# WRONG — can return millions of lines for common patterns
rg -n "import" /workspace

# CORRECT — limit output at the tool level
rg -n --max-count=1 --max-filesize=1M "import" /workspace | head -200
```

---

**Mistake M-PERF-8: Serializing the full index to JSON**

```python
# WRONG — JSON serialization of 10k entries takes 2–5 seconds
with open(cache_path, "w") as f:
    json.dump(index.to_dict(), f)

# CORRECT — binary serialization is 10–20× faster
with open(cache_path, "wb") as f:
    f.write(lz4.frame.compress(msgpack.packb(index.to_dict())))
```

---

**Mistake M-PERF-9: Not deduplicating context before token counting**

```python
# WRONG — counts tokens for the same file twice if it appeared in two results
context = result_a.files + result_b.files  # may overlap
total_tokens = sum(count_tokens(f.content) for f in context)

# CORRECT — deduplicate by (path, line range) before counting
seen = set()
context = []
for f in result_a.files + result_b.files:
    key = (f.path, f.start_line, f.end_line)
    if key not in seen:
        seen.add(key)
        context.append(f)
total_tokens = sum(count_tokens(f.content) for f in context)
```

---

**Mistake M-PERF-10: Using `asyncio.sleep()` as a poor man's debounce**

```python
# WRONG — creates one sleep task per event; O(N) tasks for N rapid events
async def _on_file_change(path: str) -> None:
    await asyncio.sleep(0.5)  # not a debounce; just delays each event
    await _reindex(path)

# CORRECT — real debounce via cancellation
_debounce_task: asyncio.Task | None = None

async def _on_file_change(path: str) -> None:
    global _debounce_task
    if _debounce_task is not None:
        _debounce_task.cancel()
    _debounce_task = asyncio.create_task(_debounced_reindex(path))

async def _debounced_reindex(path: str) -> None:
    await asyncio.sleep(0.5)
    await _reindex(path)
```

---

## 31. Performance Review Checklist

This checklist MUST be completed by the PR author for every PR that touches
execution paths listed in the Scope section of this document.

### 31.1 Pre-Submission

**Startup and Initialization**
- [ ] No new module-level imports that are not required at startup (Rule PERF-12)
- [ ] No new synchronous blocking calls in the startup path (Rule ASYNC-2)
- [ ] `B-001` (cold start) and `B-002` (warm start) benchmarks still PASS

**File System**
- [ ] All file reads use `asyncio.to_thread()` (Rule IO-1)
- [ ] Parallel reads use `asyncio.gather()` with semaphore (Rule PAR-2)
- [ ] Directory traversal respects `.gitignore` and `.pearlignore` (Rule IO-6)
- [ ] File writes are atomic (Rule IO-3)

**LLM and Context**
- [ ] System prompt is cached at session start (Rule CTX-1)
- [ ] System prompt is marked cacheable for providers that support it (Rule CTX-2)
- [ ] Context does not exceed 80% of the context window (Rule TOK-1)
- [ ] Token counts use the provider tokenizer, not a heuristic (Rule TOK-3)
- [ ] Token counts are cached by content hash (Rule TOK-4)
- [ ] Context includes file paths and line numbers (Rule CTX-10)
- [ ] Context is deduplicated before assembly (Rule CTX-11)

**Async and Concurrency**
- [ ] No `subprocess.run()` in coroutines (Rule ASYNC-2)
- [ ] All external calls have explicit timeouts (Rule ASYNC-6)
- [ ] Background tasks are tracked and awaited on shutdown (Rule ASYNC-4)
- [ ] Cancellation is propagated through `asyncio.gather()` (Rule PAR-5)

**Caching**
- [ ] All new caches have a defined maximum size (Rule CACHE-1)
- [ ] Cache keys are stable (content hash, file path) — not `id()` (Rule CACHE-2)
- [ ] No security-sensitive data in any cache (Rule CACHE-7)

**Memory**
- [ ] Large buffers are explicitly released after use (Rule MEM-3)
- [ ] New high-count objects use `__slots__` (Rule MEM-9)
- [ ] Generators used instead of list comprehensions where applicable (Rule MEM-10)

**Benchmarks**
- [ ] Full benchmark suite run locally before submission
- [ ] No regressions > 10% vs. baseline (Rule REG-1)
- [ ] Before/after timing data attached to the PR if this is a performance fix

### 31.2 Code Review

The reviewer MUST verify:

**Algorithmic complexity**
- [ ] No O(N²) or worse algorithms on paths that operate over the full index or
      file tree
- [ ] Sorting operations are outside loops, not inside
- [ ] Regex patterns are compiled at module level

**Event loop safety**
- [ ] No synchronous blocking calls visible in coroutines
- [ ] `asyncio.gather()` used for parallel I/O, not sequential await in a loop

**Resource limits**
- [ ] Search results capped at 200 (Rule PERF-19)
- [ ] Shell output capped at 1 MB (Rule PERF-17)
- [ ] Shell commands have timeouts (Rule PERF-16)

**Metrics and observability**
- [ ] Turn metrics emitted at DEBUG level (Rule MON-1)
- [ ] Performance log messages are structured (Rule PERF-26)
- [ ] Timing uses `time.perf_counter()` (Rule PERF-27)

---

## 32. Release Performance Checklist

This checklist MUST be completed and signed off before any production release tag.

### 32.1 Benchmark Gate

```
Release benchmark gate — all must PASS before tagging:

[ ] B-001 (CLI cold start)              ≤ 3,000 ms
[ ] B-002 (CLI warm start)              ≤ 1,000 ms
[ ] B-003 (full index, 10k files)       ≤ 30,000 ms
[ ] B-004 (incremental index, 50 files) ≤ 2,000 ms
[ ] B-005 (index cache load)            ≤ 2,000 ms
[ ] B-006 (symbol lookup)               ≤ 10 ms
[ ] B-007 (search_text)                 ≤ 2,000 ms
[ ] B-008 (find_references)             ≤ 3,000 ms
[ ] B-009 (read_file, 1 MB)             ≤ 100 ms
[ ] B-010 (context assembly, 80k)       ≤ 200 ms
[ ] B-011 (token count, cached)         ≤ 20 ms
[ ] B-012 (approval diff render)        ≤ 300 ms
[ ] B-013 (checkpoint creation)         ≤ 2,000 ms
[ ] B-014 (peak RSS)                    ≤ 512 MB
[ ] B-015 (idle CPU, 30s)               < 1%
[ ] B-016 (cancellation)                ≤ 2,000 ms
[ ] B-017 (MCP dispatch overhead)       ≤ 10 ms
[ ] B-018 (monorepo scope limit)        ≤ 5,000 ms
```

### 32.2 Regression Audit

- [ ] Baseline file `tests/benchmarks/baseline.json` is current and matches last
      deliberate performance update
- [ ] No benchmark shows > 10% regression vs. baseline
- [ ] Any regressions between 5% and 10% are documented with explanation

### 32.3 Memory Audit

- [ ] Peak RSS measured under the full benchmark suite: ≤ 512 MB
- [ ] No unclosed file handles detected (`lsof -p <PID>` before and after session)
- [ ] No leaked background tasks detected after session close

### 32.4 Profiling Validation

- [ ] `py-spy` profile of a full representative session reviewed by the architect
- [ ] No new function appearing in the top-10 hot functions that was not in the
      previous release profile
- [ ] Event loop slow-callback warnings (> 100 ms) are zero in the benchmark run
      with `asyncio.loop.set_debug(True)`

### 32.5 Large Repository Test

- [ ] Pearl successfully indexes the `large_repo` fixture (10,000 files) within 30 s
- [ ] Pearl activates scope limiting on the `monorepo` fixture without crashing
- [ ] Incremental index update (50 changed files in `large_repo`) completes in ≤ 2 s

### 32.6 Sign-off

```
Release Performance Checklist — complete before pushing release tag:

[ ] Benchmark gate: all 18 benchmarks PASS
[ ] Regression audit: no regression > 10%, documented if > 5%
[ ] Memory audit: RSS ≤ 512 MB, no leaks
[ ] Profiling: top-10 hot functions reviewed
[ ] Large repo test: PASS

Performance Reviewer: ______________ Date: __________
Architect Sign-off:   ______________ Date: __________
```

---

## 33. Canonical Vocabulary

The following terms are used throughout this document with precise, binding meaning.
All contributors MUST use these terms consistently in code, comments, commits, and
reviews. Using synonyms or informal substitutes creates ambiguity in performance
discussions.

| Term | Definition |
|---|---|
| **Latency budget** | The maximum permitted elapsed wall-clock time for a defined operation. Exceeding the budget is a defect, not a warning. |
| **Token budget** | The maximum number of LLM tokens allocated to a defined context slot. Exceeding it triggers pruning, not an error to the user. |
| **Memory budget** | The maximum permitted RSS for a defined process or component. Exceeding it is a blocking bug. |
| **Benchmark** | A repeatable, fixture-based measurement of a defined operation against a defined budget. |
| **Baseline** | The stored benchmark result from the last deliberate performance update. Regression is measured against this. |
| **Regression** | A benchmark result that exceeds the baseline by more than 10%. Regressions block merges. |
| **Hot path** | A code path that executes on every agent turn or every tool dispatch. Optimization effort is prioritized here. |
| **Cold start** | Launching Pearl with no warm OS filesystem cache, no warm Python import cache, and no existing on-disk index. |
| **Warm start** | Launching Pearl where the OS cache, Python cache, and on-disk index are all fresh from a recent run. |
| **Index cache** | The on-disk serialized index, keyed by HEAD SHA and `.gitignore` mtime. |
| **Turn** | One user message through to the agent's complete response, including all tool calls within that cycle. |
| **Context assembly** | The process of selecting, ordering, deduplicating, and token-counting content for the LLM context window. |
| **Context headroom** | Tokens remaining in the context window after assembly: `max_tokens − total_assembled_tokens`. |
| **Token count** | The provider-specific tokenizer count of a string. Not an approximation; the exact tokenizer for the target model. |
| **Incremental index** | An index update that re-processes only changed files, not the full file tree. |
| **Scope limiting** | Restricting indexing and search to a subtree of the repository to bound resource usage on large repositories. |
| **Semaphore** | An asyncio concurrency primitive used to cap the number of concurrent I/O operations. |
| **Debounce** | Collapsing rapid successive events into a single event by waiting for a quiet window before acting. |
| **Single-flight** | A pattern where a duplicate in-flight operation joins the existing in-progress operation instead of starting a second one. |
| **LRU cache** | Least-Recently-Used cache: a bounded cache that evicts the least-recently-used entry when full. |
| **Atomic write** | Writing to a temporary file and renaming it atomically to the final path, preventing partial writes. |
| **RSS** | Resident Set Size: the portion of a process's memory held in RAM. The primary memory budget metric. |

---

## 34. References

### Internal Cross-References

| Document | Relevant sections |
|---|---|
| `01_ARCHITECTURE_RULES.md` | Section 9 (Tool System — `MAX_SEARCH_RESULTS`); Section 18 (Scalability Rules); Section 19 (Observability Rules) |
| `02_CODING_STANDARDS.md` | Section 7 (Async Programming); Section 15 (Performance Considerations); Section 25 (Benchmark Policy) |
| `03_TESTING_STANDARD.md` | Section 6 (Performance Testing); Section 7 (Stress Testing); Section 15 (Competitive Benchmarking); Section 16 (Release Gates) |
| `04_SECURITY_GUIDELINES.md` | Section 2 (Secure Coding Practices — validation that must not be bypassed for performance); Section 5 (Filesystem Sandboxing) |
| `05_UI_UX_GUIDELINES.md` | Section 13 (Performance Perception — streaming and progress events as perceived performance) |

### Python Standard Library and Ecosystem

| Tool / Library | Purpose in Pearl | Key rules |
|---|---|---|
| `asyncio` | Event loop, coroutines, task management | Rules ASYNC-1 through ASYNC-11 |
| `asyncio.to_thread()` | Offload blocking I/O to thread pool | Rules IO-1, ASYNC-2 |
| `functools.lru_cache` | Bounded function-level caching | Rule CACHE-1 |
| `pathlib.Path` | File system operations | Rule IO-5 |
| `time.perf_counter()` | Monotonic high-resolution timing | Rule PERF-27 |
| `sys.intern()` | String deduplication for paths | Rule MEM-11 |
| `psutil` | RSS and CPU measurement | Rules MEM-8, CPU-1 |
| `re.compile()` | Pre-compiled regex patterns | Rule CPU-7 |
| `io.StringIO` | Efficient string accumulation | Rule CPU-4 |
| `msgpack` | Binary serialization for index | Rules IDX-5, IDX-6 |
| `lz4` / `zstd` | Index compression | Rule IO-16 |
| `watchdog` | OS-native file watching | Rule IO-8 |
| `pathspec` | `.gitignore` pattern matching | Rule IO-6 |

### Profiling Tools

| Tool | Purpose | Installation |
|---|---|---|
| `py-spy` | Live CPU profiling, flamegraphs | `pip install py-spy` |
| `memray` | Memory allocation profiling | `pip install memray` |
| `scalene` | Combined CPU + memory profiler | `pip install scalene` |
| `yappi` | Coroutine-aware profiling | `pip install yappi` |
| `cProfile` | Standard library CPU profiler | Built-in |
| Chrome DevTools | VS Code extension heap profiling | Built into VS Code |

### External Standards and Literature

| Reference | Relevance |
|---|---|
| Google SRE Book — Chapter 4 (Service Level Objectives) | Framing performance budgets as SLOs with consequences |
| Brendan Gregg, *Systems Performance* (2nd ed.) | Methodology for latency, CPU, memory, and I/O analysis |
| PEP 567 — Context Variables | Async context propagation without shared state |
| Python `asyncio` docs — "Developing with asyncio" | Debugging slow callbacks; `set_debug(True)` |
| ripgrep documentation — `--max-count`, `--max-filesize` | Enforcing search result caps at the tool boundary |
| Anthropic API Reference — Prompt Caching | `cache_control` for system prompt and tool definitions |
| HTTPx documentation — Connection Pooling | Persistent connection management for LLM API calls |

---

*This document is part of the Pearl Engineering Standards Series.*  
*Previous: [05_UI_UX_GUIDELINES.md](05_UI_UX_GUIDELINES.md)*  
*Next: [07_AI_ENGINEERING_GUIDELINES.md](07_AI_ENGINEERING_GUIDELINES.md)*
