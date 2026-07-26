# 01 — Architecture Rules

**Pearl AI Coding Agent — Engineering Standards Series**  
Document owner: Lead Software Architect  
Applies to: Every sprint, every contributor, every code review  
Status: Authoritative — changes require architect sign-off

---

## Purpose

This document defines the non-negotiable architecture rules for the Pearl AI Coding Agent.
These rules exist to protect Pearl's core invariants — safety, correctness, and
maintainability — across every sprint, every contributor, and every future feature.

Rules in this document are **binding**. A pull request that violates them is rejected
regardless of how well the code otherwise works. Every rule has a stated rationale;
if you believe a rule is wrong, raise it with the architect rather than silently
violating it.

---

## Table of Contents

1. [System Boundaries](#1-system-boundaries)
2. [Layer Ownership](#2-layer-ownership)
3. [The Approval Invariant](#3-the-approval-invariant)
4. [Module Dependency Rules](#4-module-dependency-rules)
5. [The Single Index Rule](#5-the-single-index-rule)
6. [MCP Protocol Rules](#6-mcp-protocol-rules)
7. [State Ownership Rules](#7-state-ownership-rules)
8. [LLM Client Rules](#8-llm-client-rules)
9. [Tool System Rules](#9-tool-system-rules)
10. [Configuration Rules](#10-configuration-rules)
11. [Error Propagation Rules](#11-error-propagation-rules)
12. [Prohibited Patterns](#12-prohibited-patterns)
13. [Permitted Exceptions](#13-permitted-exceptions)
14. [Architecture Decision Records](#14-architecture-decision-records)
15. [Common Mistakes](#15-common-mistakes)
16. [Architecture Review Checklist](#16-architecture-review-checklist)
17. [Architecture Evolution Policy](#17-architecture-evolution-policy)
18. [Scalability Rules](#18-scalability-rules)
19. [Observability Rules](#19-observability-rules)
20. [Future Compatibility Rules](#20-future-compatibility-rules)

---

## 1. System Boundaries

### 1.1 Component Map

Pearl is a two-process system. The boundary between them is strictly JSON-RPC 2.0 over
stdio. No other inter-process communication is permitted.

```
┌─────────────────────────────────────────────┐
│  VS Code Extension (TypeScript)             │
│  vscode-extension/src/                      │
│                                             │
│  Responsibilities:                          │
│  • Spawn and manage the Python subprocess   │
│  • Send JSON-RPC requests over stdio        │
│  • Render UI (chat, plan, patch approval)   │
│  • Handle pearl/progress notifications      │
│  • Never execute Python or shell directly   │
└───────────────┬─────────────────────────────┘
                │  JSON-RPC 2.0, newline-delimited, over stdio
                │  (stdout of Python process ← MCP responses)
                │  (stdin  of Python process → MCP requests)
                ▼
┌─────────────────────────────────────────────┐
│  Python Backend (Python 3.10+)              │
│  src/                                       │
│                                             │
│  Responsibilities:                          │
│  • All tool execution                       │
│  • All LLM communication                   │
│  • All file I/O                             │
│  • All git operations                       │
│  • Approval gating (PatchManager)           │
│  • Repository indexing                      │
│  • Session memory                           │
└─────────────────────────────────────────────┘
```

### 1.2 Boundary Rules

**Rule B-1:** The VS Code extension MUST NOT execute Python code directly. It communicates
exclusively via MCP JSON-RPC.

**Rule B-2:** The Python backend MUST NOT read from the VS Code extension process's memory
or file handles. It knows nothing about the editor UI.

**Rule B-3:** No information crosses the boundary except JSON-serializable values. Binary
data, live objects, and Python exceptions MUST be serialized to their string forms before
appearing in any MCP response.

**Rule B-4:** The stdio stream is the only IPC mechanism. No shared files, environment
variable injection, or named pipes between the two processes.

---

## 2. Layer Ownership

### 2.1 The Six Layers

Pearl's Python backend is organized in six layers. Each layer has a single, clearly-scoped
responsibility. **A layer may only depend on layers below it** — never upward, never lateral
within the same layer.

```
Layer 6  ─── Entry Points ────────  src/main.py, src/mcp/__main__.py
                                     Wires everything together; no logic

Layer 5  ─── Protocol ────────────  src/mcp/server.py
                                     JSON-RPC dispatch, stdio framing

Layer 4  ─── Agent ──────────────  src/agent/ (agent.py, executor.py,
                                     planner.py, dispatcher.py)
                                     Orchestration, planning, execution loop

Layer 3  ─── Tools ─────────────  src/tools/ (all tool modules)
                                     Pure functions; tool logic only

Layer 2  ─── Infrastructure ─────  src/llm/, src/memory/
                                     LLM abstraction, session memory

Layer 1  ─── Foundation ─────────  src/config/, src/prompts/
                                     Settings, prompt templates
```

### 2.2 Layer Dependency Rules

**Rule L-1 (No upward dependencies):** A lower layer MUST NOT import from a higher layer.

```python
# VIOLATION — Layer 1 (config) importing Layer 3 (tools)
# src/config/settings.py
from src.tools.registry import ToolRegistry  # FORBIDDEN

# CORRECT — Layer 3 importing Layer 1
# src/tools/repo_tools.py
from src.config.settings import Settings     # PERMITTED
```

**Rule L-2 (No lateral dependencies within Agent layer):** Planner MUST NOT import
Executor; Executor MUST NOT import Planner except through the interface already established
(`Planner` type annotation in `AutonomousExecutor.__init__`).

**Rule L-3 (Tools are leaves):** Tool modules (`src/tools/`) MUST NOT import from
`src/agent/`, `src/mcp/`, or each other except for shared utilities in `src/tools/metadata.py`
and `src/tools/file_tools.py`'s workspace-boundary helper.

```python
# VIOLATION — tool importing agent
# src/tools/edit_tools.py
from src.agent.executor import AutonomousExecutor  # FORBIDDEN

# PERMITTED cross-tool imports
from src.tools.metadata import tool                # OK — shared decorator
from src.tools.file_tools import _ensure_within_workspace  # OK — shared guard
```

**Rule L-4 (Protocol is a boundary):** `src/mcp/server.py` MUST NOT contain business
logic. It translates between JSON-RPC and the agent layer. Any logic that isn't
"parse request → call agent method → serialize result" belongs in `src/agent/`.

### 2.3 Dependency Direction Diagram

```
       Permitted import directions
       ──────────────────────────
       Entry Points
           │
           ▼
        Protocol
           │
           ▼
          Agent ────────► Tools
           │                │
           ▼                ▼
      Infrastructure ◄─── Tools
           │
           ▼
       Foundation
```

---

## 3. The Approval Invariant

This is Pearl's most critical safety property. It is not negotiable under any circumstances.

### 3.1 Statement

> **Every write to the user's workspace that is initiated by an autonomous run MUST pass
> through `PatchManager` and be explicitly approved by the user before reaching disk.**

### 3.2 What This Means

1. File-writing tools (`create_file`, `edit_lines`, `patch_file`, `replace_in_file`,
   `replace_function`, `replace_class`, `insert_after_symbol`, `insert_before_symbol`)
   MUST call `PatchManager.stage()` when an active patch manager is present, not write
   to disk directly.

2. `AutonomousExecutor` is the **only** code path that wires file-writing tools to disk.
   Any caller that invokes a write tool outside an `AutonomousExecutor` context MUST
   understand it is bypassing the approval gate — and in autonomous agent code, that
   is a bug, not a feature.

3. `PatchManager` MUST be the **single chokepoint**. No write tool may have a "fast
   path" that skips staging.

### 3.3 The Three Execution Modes

| Mode | Path | Approval |
|---|---|---|
| Direct tool call (CLI tool, test) | `ToolDispatcher.execute()` directly | None — caller is already a human |
| `tools/call` MCP method | `MCPServer._tools_call()` → `ToolDispatcher` | None — client is making a deliberate call |
| Autonomous run | `MCPServer._run_autonomous()` → `AutonomousExecutor.run()` | **Required** — PatchManager active |

**Rule A-1:** When `set_active_patch_manager(pm)` is called, ALL subsequent file-writing
tool calls in that thread context MUST stage to `pm`. No tool may read and ignore the
active patch manager.

**Rule A-2:** A new execution path that calls write tools autonomously MUST activate a
`PatchManager` first and MUST NOT bypass it. New MCP methods, new agent modes, and new
CLI commands that execute write tools autonomously are all subject to this rule.

**Rule A-3:** `execute_shell` is the one deliberate exception. Shell commands are
not diffed because shell output is not structured enough for meaningful diffing.
However, shell commands that run inside an autonomous executor are subject to
`CommandApprovalManager` staging — they are not exempt from the approval principle,
only from the diff-based presentation.

**Rule A-4:** The approval invariant MUST be verified by at least one end-to-end test per
sprint that actually writes a file to disk and verifies the file was not present before
approval.

### 3.4 Approved Bypass Decision Tree

```
A new feature wants to write a file. Is it...
│
├─ ...invoked autonomously by the agent?
│   └─ YES → MUST use AutonomousExecutor + PatchManager. No exceptions.
│
├─ ...a direct user command (CLI built-in, explicit `tools/call`)?
│   └─ YES → May write directly. Document why in a comment.
│
└─ ...a test fixture or setup step?
    └─ YES → May write directly. Must not use production write-tool code paths.
```

---

## 4. Module Dependency Rules

### 4.1 Planner Isolation

**Rule P-1:** `Planner` MUST NOT import any tool module, `ContextBuilder`, or
`WorkspaceMemory`. It accepts:
- A `ToolRegistry` (for building the tool-description prompt)
- A `ToolDispatcher` (kept for historical reasons; the planner no longer calls it directly)
- A `LLMClient`
- A pre-assembled `workspace_context: str` string

This isolation is what allows `ContextBuilder`'s ranking strategy to change without
touching the planner.

```python
# VIOLATION
# src/agent/planner.py
from src.tools.context_manager import ContextBuilder  # FORBIDDEN
from src.memory.workspace_memory import WorkspaceMemory  # FORBIDDEN

# CORRECT — Planner receives context as an already-formatted string
def plan(self, user_prompt: str, workspace_context: str = "") -> list[ToolCall]: ...
```

**Rule P-2:** The `workspace_context` parameter MUST remain an opaque string. The Planner
MUST NOT introspect its structure, format, or origin.

### 4.2 Executor Responsibilities

**Rule E-1:** `AutonomousExecutor` owns the execution loop, patch-approval lifecycle,
cancellation, and progress streaming. It MUST NOT contain planning logic or LLM calls.

**Rule E-2:** `AutonomousExecutor` delegates ALL tool execution to `ToolDispatcher`. It
MUST NOT invoke tool functions directly.

**Rule E-3:** `AutonomousExecutor` MUST accept `PatchManager` and `CommandApprovalManager`
as constructor parameters (with sensible defaults) — not create them internally with
no way to substitute them. This is required for both testability and controlled sharing
(e.g., `MCPServer` passes its `CheckpointManager` in).

### 4.3 MCP Server Isolation

**Rule M-1:** `MCPServer` MUST NOT contain agent logic. `_run_autonomous()` constructs
an `AutonomousExecutor` and calls `.run()`; the loop itself lives in `executor.py`.

**Rule M-2:** `MCPServer` MUST NOT hold mutable agent state between requests except:
- The in-flight `_autonomous_executor` (needed to resolve `approvePatches`/`rejectPatches`)
- The shared `checkpoints` store
- The `memory` store

**Rule M-3:** `MCPServer` method handlers MUST be pure request-response functions from
the caller's perspective. Side effects (state mutation) are limited to the three
state-holders listed in M-2.

---

## 5. The Single Index Rule

**Rule I-1:** `RepositoryIndex` (`src/tools/repo_tools.py`) is the sole authoritative
source of "what files and symbols exist in the workspace." No other module may walk the
filesystem independently for the purpose of discovering Python symbols or file lists.

**Rule I-2:** `ContextBuilder`, symbol search, reference search, and project summary ALL
read from `RepositoryIndex`. They MUST NOT re-walk or re-parse the filesystem.

**Rule I-3:** When a write tool modifies a file, it MUST call `refresh_indexed_file(path)`
after the write succeeds. This keeps the index consistent within a session without a
full re-walk.

```python
# CORRECT — after writing, refresh the single index
self.patch_manager.apply_all()
for path in applied_paths:
    refresh_indexed_file(path)  # keeps _INDEX_CACHE consistent
```

**Rule I-4:** The index is process-scoped and in-memory. It MUST NOT be persisted to disk
or shared across processes. It is a session-level cache, not a database.

**Rule I-5:** `IGNORED_DIRS` is the authoritative list of directories excluded from
indexing. Any module that does walk the filesystem (e.g., for non-indexing purposes)
SHOULD respect the same set.

---

## 6. MCP Protocol Rules

### 6.1 Method Naming

**Rule MCP-1:** Standard MCP methods use the `method/verb` form (`tools/list`,
`tools/call`, `initialize`, `shutdown`, `exit`). Pearl-specific extensions use the
`pearl/camelCaseVerb` form (`pearl/runAutonomous`, `pearl/approvePatches`).

**Rule MCP-2:** Pearl extensions MUST be additive. Adding a new `pearl/*` method MUST NOT
break any existing method or change the behavior of any existing response shape.

**Rule MCP-3:** A new Pearl capability (e.g., `pearlCheckpoints`) MUST be advertised in
the `initialize` response's `capabilities.experimental` map before any client can rely on
it. A client that doesn't see the capability flag MUST degrade gracefully.

### 6.2 Response Shape

**Rule MCP-4:** Method responses MUST be JSON-serializable. If a tool produces a
non-serializable result (e.g., a `subprocess.CompletedProcess`), it MUST be converted
to its string representation before being embedded in a response.

**Rule MCP-5:** Errors in method handlers MUST surface as JSON-RPC `error` objects, not
as HTTP-style status codes or exceptions that escape to the stdio framing layer.

| Situation | Response shape |
|---|---|
| Tool not found | `result.isError=true`, `result.content[0].text="Unknown tool: ..."` |
| Protocol error (missing param) | `error.code=-32602`, `error.message="..."` |
| Internal error | `error.code=-32603`, `error.message="..."` |
| Server unknown method | `error.code=-32601`, `error.message="Unknown method: ..."` |

**Rule MCP-6:** Notifications (`pearl/progress`) MUST have no `id` field. A response
MUST always have an `id` matching the request's `id`. These MUST NOT be confused.

### 6.3 Backward Compatibility

**Rule MCP-7:** A new optional field in a response is backward compatible. A new required
field, a renamed field, or a removed field is a breaking change and requires a version
bump in `SERVER_VERSION`.

**Rule MCP-8:** The `pearl/runAutonomous` → `pearl/approvePatches` / `pearl/rejectPatches`
flow MUST remain the canonical path for all autonomous file writes. A new method that
executes file edits autonomously MUST also go through this same pause/approval cycle.

---

## 7. State Ownership Rules

### 7.1 Who Owns What

| State | Owner | Lifetime | Shared with |
|---|---|---|---|
| `PatchManager` | `AutonomousExecutor` | One run (or pause/resume cycle) | `edit_tools` via context var |
| `CommandApprovalManager` | `AutonomousExecutor` | One run | `shell_tools` via context var |
| `CheckpointManager` | `MCPServer` / `PearlAgent` | Process lifetime | `AutonomousExecutor` (passed in) |
| `Memory` | `MCPServer` / `PearlAgent` | Process lifetime | — |
| `RepositoryIndex` | `_INDEX_CACHE` (module global) | Process lifetime | All readers |
| `WorkspaceMemory` | `Memory` | Process lifetime | — |
| LLM client | `Planner`, `PearlAgent` | Process lifetime | — |

### 7.2 Context Variable Rules

**Rule S-1:** `set_active_patch_manager()` and `set_active_command_approver()` use
Python context variables (`contextvars.ContextVar`). They MUST be set to `None`
at the end of every run (whether completed, rejected, cancelled, or failed) to prevent
state leakage into the next run.

**Rule S-2:** Only `AutonomousExecutor` may call `set_active_patch_manager()` and
`set_active_command_approver()`. No tool, no agent facade, no MCP method handler may
call these directly.

**Rule S-3:** The module-global `_INDEX_CACHE` in `repo_tools.py` is the single
index store. Nothing may clear it during normal operation (only tests may substitute
or clear it via monkeypatching).

### 7.3 No Shared Mutable State Between Requests

**Rule S-4:** Each `pearl/runAutonomous` call MUST create a fresh `AutonomousExecutor`
instance. State from a prior run MUST NOT leak into a new one via a shared executor.

```python
# CORRECT — new executor per call
executor = AutonomousExecutor(self.planner, self.dispatcher, ...)
self._autonomous_executor = executor
report = executor.run(prompt)

# VIOLATION — reusing executor from previous run
self._autonomous_executor.run(prompt)  # FORBIDDEN if previous run completed
```

---

## 8. LLM Client Rules

### 8.1 Provider Abstraction

**Rule LLM-1:** All LLM calls MUST go through `LLMClient` (`src/llm/client.py`). Direct
imports of `openai`, `anthropic`, or `google.generativeai` are forbidden outside of
`src/llm/providers/`.

**Rule LLM-2:** Planning uses `LLMClient.generate_json()`. Chat uses
`LLMClient.generate()`. This distinction is intentional: planning output is parsed as
JSON and MUST NOT be pre-conditioned by a system prompt. Adding a system prompt to
`generate_json()` is a breaking change to all planning behavior.

**Rule LLM-3:** `LLMClient.generate()` exposes `cancel_check: Callable[[], bool] | None`.
Any code that calls `generate()` or `generate_json()` from within an executor loop MUST
forward its cancellation check. Blocking LLM calls that ignore cancellation degrade UX.

**Rule LLM-4:** Retry logic lives in `LLMClient`, not in callers. A caller that wraps
`generate()` in its own retry loop is introducing duplicate behavior that will fight
with the client's own backoff.

### 8.2 Provider Configuration

**Rule LLM-5:** Provider selection is controlled solely by `Settings.LLM_PROVIDER`.
No module may select a provider based on other heuristics (model name patterns,
environment variables other than those `Settings` reads, etc.).

**Rule LLM-6:** The `scripted` provider (`src/llm/providers/scripted.py`) MUST only be
selectable via explicit configuration (`PEARL_LLM_PROVIDER=scripted`). It MUST NOT be a
fallback for any misconfiguration path.

---

## 9. Tool System Rules

### 9.1 Tool Registration

**Rule T-1:** Every tool MUST be registered via the `@tool` decorator. A function that
is called by the dispatcher but not decorated is invisible to the Planner and will
cause plans to reference an unknown tool name.

```python
# CORRECT
@tool(
    description="Read the contents of a UTF-8 text file.",
    parameters={"path": "str"},
    returns="str",
)
def read_file(path: str) -> str: ...

# VIOLATION — dispatcher can call it, Planner can never plan it
def read_file(path: str) -> str: ...  # no @tool
```

**Rule T-2:** The `description` field in `@tool` is the only description the Planner
ever sees. It MUST be precise, action-oriented, and include any important constraints
(e.g., "Fails if the file already exists.").

**Rule T-3:** A tool's function signature is its contract. Parameter names and types
MUST match what `@tool(parameters=...)` declares. Drift between the decorator and the
function is a planning-time correctness bug.

### 9.2 Tool Safety

**Rule T-4:** Every tool that writes to the filesystem MUST call
`_ensure_within_workspace(path)` before any I/O. This is the workspace boundary
enforcement that prevents path traversal attacks.

**Rule T-5:** A tool MUST NOT read from the active `PatchManager` or
`CommandApprovalManager` state — only write tools may stage into them. Tools that
need to report "what's pending" expose that through the executor/server API, not the
tool itself.

**Rule T-6:** Tools MUST NOT raise `SystemExit` or call `sys.exit()`. They MUST raise
a Python exception with an actionable message. The dispatcher catches exceptions and
wraps them as `ToolExecutionError`.

### 9.3 Search Result Caps

**Rule T-7:** Any tool that returns a collection of matches from a filesystem walk
MUST cap results at `MAX_SEARCH_RESULTS` (currently 200) and append a truncation
notice when the cap is reached. An uncapped tool that goes into a planning prompt can
exceed the LLM's context window, producing incorrect plans silently.

```python
# CORRECT
matches = _search(root, pattern, extensions)
if len(matches) >= MAX_SEARCH_RESULTS:
    matches.append(_truncation_notice(matches, MAX_SEARCH_RESULTS))
return matches
```

---

## 10. Configuration Rules

**Rule C-1:** ALL configuration is read from `src/config/settings.py`. No module may
read environment variables directly with `os.environ.get(...)` or `os.getenv(...)`.

**Rule C-2:** `settings.py` MUST load `.env` relative to its own file location
(`Path(__file__).resolve().parent.parent / ".env"`), not relative to `cwd`. Pearl's
workspace is a different directory from Pearl's own installation, and `cwd` is the
workspace.

```python
# CORRECT — resolved from settings.py's own location
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

# VIOLATION — cwd-relative
load_dotenv(".env")  # breaks when cwd is not Pearl's repo root
```

**Rule C-3:** A new configuration key MUST have a documented default in `Settings`.
A key with no default is a required environment variable, which breaks the
"works with no configuration" contract for new users.

**Rule C-4:** No configuration values may be computed at import time if they involve
filesystem operations or subprocess calls. They may be computed lazily (as properties
or on first access), but never at module load — a failed import should not block
the whole application.

---

## 11. Error Propagation Rules

### 11.1 Error Taxonomy

| Layer | Error type | Handling |
|---|---|---|
| Tools | `ValueError`, `FileNotFoundError`, `PermissionError`, `subprocess.TimeoutExpired` | Raised; Dispatcher wraps as `ToolExecutionError` |
| Dispatcher | `ToolNotFoundError`, `ToolExecutionError` | Raised; Executor catches and records in `ExecutionStep.error` |
| Executor | `LLMCancelled` | Finalize as `stop_reason="cancelled"` |
| MCP server | `MCPProtocolError` | Return as JSON-RPC `error` object |
| MCP server | Any other exception | Log + return as `INTERNAL_ERROR` |
| CLI | Any exception | `print(f"\nError: {exc}\n")` — never a traceback to stdout |

### 11.2 Rules

**Rule ERR-1:** A tool MUST raise an exception with an actionable message on failure.
"Something went wrong" is not acceptable. "Path escapes workspace: ../etc/passwd" is.

**Rule ERR-2:** The MCP server MUST NEVER let an unhandled exception propagate to the
stdio framing layer and corrupt the JSON-RPC stream. Every handler is wrapped in a
`try/except` that returns an `INTERNAL_ERROR` response.

**Rule ERR-3:** Progress callback failures MUST be swallowed. A broken `on_progress`
listener MUST NOT abort execution. Log at WARNING level and continue.

**Rule ERR-4:** Checkpoint failures MUST be swallowed in the `approve()` path. The
user's write MUST proceed even if the checkpoint can't be taken. Log at WARNING level.

**Rule ERR-5:** `LLMCancelled` is the only exception the executor treats as a normal
cancellation signal. Any other exception from the LLM client is treated as a provider
error and triggers replanning (or `fatal_error` if the replan budget is exhausted).

---

## 12. Prohibited Patterns

These patterns are unconditionally forbidden. Any code that introduces one of them is
rejected in code review.

### 12.1 Approval Bypass

```python
# FORBIDDEN — write tool called outside executor context
dispatcher.execute("create_file", path="out.py", content="...")

# FORBIDDEN — writing directly from a tool when a patch manager is active
def my_tool(path: str, content: str) -> None:
    Path(path).write_text(content)  # ignores active PatchManager
```

### 12.2 Upward Dependency

```python
# FORBIDDEN — tool importing from agent layer
# src/tools/my_tool.py
from src.agent.executor import AutonomousExecutor

# FORBIDDEN — config importing from tools layer
# src/config/settings.py
from src.tools.registry import ToolRegistry
```

### 12.3 Direct Environment Variable Access

```python
# FORBIDDEN
import os
BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")

# CORRECT
from src.config.settings import Settings
BASE_URL = Settings.OLLAMA_BASE_URL
```

### 12.4 Planner Logic Contamination

```python
# FORBIDDEN — Planner building context itself
# src/agent/planner.py
from src.tools.context_manager import ContextBuilder
context = ContextBuilder().build(user_prompt)

# CORRECT — context is assembled by the caller and passed in
def plan(self, user_prompt: str, workspace_context: str = "") -> list[ToolCall]: ...
```

### 12.5 Shared Mutable Executor

```python
# FORBIDDEN — reusing a completed executor
class MCPServer:
    def __init__(self):
        self._executor = AutonomousExecutor(...)  # built once at init

    def _run_autonomous(self, params, notify):
        report = self._executor.run(params["prompt"])  # reused across calls
```

### 12.6 Direct LLM Provider Import

```python
# FORBIDDEN — importing provider SDKs outside src/llm/providers/
# src/agent/planner.py
import openai
client = openai.OpenAI()

# CORRECT
from src.llm.client import LLMClient
client = LLMClient()
```

### 12.7 Unbounded Search Returns

```python
# FORBIDDEN — no cap on search results
def my_search(pattern: str) -> list[dict]:
    return [m for m in _walk_and_match(pattern)]  # no limit

# CORRECT
def my_search(pattern: str) -> list[dict]:
    matches = []
    for m in _walk_and_match(pattern):
        if len(matches) >= MAX_SEARCH_RESULTS:
            matches.append(_truncation_notice(matches, MAX_SEARCH_RESULTS))
            break
        matches.append(m)
    return matches
```

---

## 13. Permitted Exceptions

A "permitted exception" is a case where the general rule is relaxed under specific,
documented conditions. Invoking a permitted exception requires a comment citing this
document and the exception code.

| Code | Exception | Condition |
|---|---|---|
| PE-001 | `execute_shell` bypasses `PatchManager` | Shell output is not structured enough to diff meaningfully; guarded by `CommandApprovalManager` instead |
| PE-002 | Test helpers write files directly | Test setup/teardown code; MUST NOT use production write-tool code paths |
| PE-003 | `src/tools/metadata.py` is imported by tool modules | Shared decorator — not a true lateral dependency |
| PE-004 | `src/tools/file_tools._ensure_within_workspace` is imported by tool modules | Shared security guard — not a true lateral dependency |
| PE-005 | `MCPServer` holds in-flight `_autonomous_executor` across requests | Required to resolve `approvePatches`/`rejectPatches` on a paused run |

```python
# Example of citing a permitted exception in code
# This tool writes directly because it's test infrastructure (PE-002).
def _create_test_fixture(path: str, content: str) -> None:
    Path(path).write_text(content)
```

---

## 14. Architecture Decision Records

All significant architecture decisions are recorded as ADRs in this project. When a rule
in this document was established as the result of a specific incident or design debate,
the ADR is referenced here.

| Rule | ADR Reference | Summary |
|---|---|---|
| Rule A-2 (Approval invariant) | ADR-001 | `Planner.run()`, `PearlAgent.plan_and_run()`, and `pearl/plan` were confirmed to bypass PatchManager and write files with zero approval. Removed. |
| Rule I-2 (Single index) | ADR-002 | Multiple walkers diverged from the index and produced stale context; unified on single cache. |
| Rule C-2 (Settings path resolution) | ADR-003 | `.env` loaded via `cwd` silently failed when Pearl was launched with `cwd` set to a target project; broke all provider settings. |
| Rule S-1 (Context var cleanup) | ADR-004 | Active PatchManager not cleared after a run leaked into the next run, staging writes that should have executed. |
| Rule B-1 (Checkpoints outside workspace) | ADR-005 | Storing checkpoints inside the workspace required editing the user's `.gitignore` and allowed checkpoint git state to be captured in its own checkpoints. |

When writing a new ADR:
1. Create `docs/adr/NNNN-short-title.md`
2. State the context, decision, and consequences
3. Add the cross-reference in this table

---

## 15. Common Mistakes

These are mistakes that have been made before or that reviewers frequently catch.
Know them before you submit a PR.

### Mistake 1: New execution path that skips PatchManager

**Symptom:** A new MCP method or CLI command that calls tool functions in a loop
without creating an `AutonomousExecutor`.  
**Risk:** Files are written without user review — Pearl's core safety guarantee is
violated.  
**Detection:** Any code path that iterates over `ToolCall` objects and calls
`dispatcher.execute()` without an active `PatchManager`.

### Mistake 2: Planning with context retrieved in the planner

**Symptom:** `Planner.plan()` calls `ContextBuilder` or reads from `WorkspaceMemory`
before building the prompt.  
**Risk:** The planner is no longer independently testable; changing context strategy
requires planner changes.  
**Detection:** Import of `ContextBuilder` or `WorkspaceMemory` in `planner.py`.

### Mistake 3: cwd-relative path in settings or prompts

**Symptom:** A new setting, prompt file, or `.env` load uses a relative path or a
bare `Path(filename)`.  
**Risk:** Works when Pearl is launched from its own repo root; silently fails when
launched from a target project directory.  
**Detection:** Any `Path("something")` or `open("path")` call in `src/config/` or
`src/prompts/` that isn't prefixed with `Path(__file__).resolve().parent...`.

### Mistake 4: Forgetting to refresh the index after a write

**Symptom:** After approving a patch, `find_symbol` or `find_references` returns
stale results for the modified file.  
**Risk:** The model is told symbols don't exist when they do, or vice versa.  
**Detection:** Any call to `patch_manager.apply_all()` or `write_file()` not followed
by `refresh_indexed_file(path)`.

### Mistake 5: Swallowing a cancel check in a long LLM call

**Symptom:** `executor.cancel()` is called from the UI thread; the current planning
or replanning call doesn't check `cancel_check` and runs to completion anyway.  
**Risk:** Cancellation latency is equal to the LLM call duration (potentially
minutes for a large model).  
**Detection:** A call to `LLMClient.generate()` or `generate_json()` without a
`cancel_check` argument when called from within an executor loop.

### Mistake 6: Stale benchmark results presented as current

**Symptom:** A REPORT.md or benchmark JSON in `benchmarks/` is updated with numbers
from a development environment without noting the environment's hardware characteristics.  
**Risk:** A new contributor benchmarks on a different machine and either dismisses
correct improvements or accepts false regressions.  
**Detection:** Benchmark files with no hardware/environment provenance note.

### Mistake 7: Tool that raises a generic exception

**Symptom:** `raise Exception("Error")` or `raise RuntimeError("Failed")` in a tool.  
**Risk:** The user sees "Error" and has no actionable information. The model receives
"Error" and may misinterpret or mishandle the failure.  
**Detection:** Generic exception messages without context in tool code.

---

## 16. Architecture Review Checklist

Use this checklist before approving any pull request that touches core architecture
components (Planner, Executor, Dispatcher, MCP server, PatchManager, RepositoryIndex,
tools).

### Layer and Dependency

- [ ] No lower layer imports from a higher layer
- [ ] No tool module imports from `src/agent/` or `src/mcp/`
- [ ] No module imports `openai`, `anthropic`, or `google.generativeai` outside `src/llm/providers/`
- [ ] No environment variable reads outside `src/config/settings.py`
- [ ] `Planner` imports no tool module and no `ContextBuilder`

### Approval Invariant

- [ ] Any new autonomous execution path activates `PatchManager` before dispatching
- [ ] `set_active_patch_manager(None)` is called when the run ends (all branches)
- [ ] `set_active_command_approver(None)` is called when the run ends (all branches)
- [ ] At least one test verifies the file was absent before approval and present after

### MCP Protocol

- [ ] New `pearl/*` method does not change any existing method's response shape
- [ ] New capability is advertised in `initialize`'s `experimental` map
- [ ] All response values are JSON-serializable
- [ ] Error responses use the correct JSON-RPC error codes

### Tools

- [ ] Every new tool function has the `@tool` decorator
- [ ] Every tool that writes files calls `_ensure_within_workspace(path)`
- [ ] Every search/walk tool caps results at `MAX_SEARCH_RESULTS`
- [ ] Every tool raises a specific, actionable exception on failure

### Configuration

- [ ] New configuration keys have defaults in `Settings`
- [ ] No new bare `Path("filename")` or relative path in `src/config/` or `src/prompts/`
- [ ] `.env` loading (if touched) uses file-relative path resolution

### State Management

- [ ] New `AutonomousExecutor` is created per `runAutonomous` call, not reused
- [ ] No new module-global mutable state without explicit ownership documentation
- [ ] Context variable cleanup is symmetric (set before run, cleared after)

### Repository Index

- [ ] After any write, `refresh_indexed_file(path)` is called
- [ ] No new filesystem walk for "what exists" — readers use `_get_index()`
- [ ] `IGNORED_DIRS` is respected by any new walk code

### Architecture Evolution

- [ ] Non-trivial architecture change has a corresponding ADR in `docs/adr/`
- [ ] Deprecated paths are marked with `# DEPRECATED(NNNN):` and a removal target sprint
- [ ] Deprecation is announced in CHANGELOG under `[Unreleased]`
- [ ] Backward-incompatible changes increment `SERVER_VERSION`

### Scalability

- [ ] No new in-memory collection is unbounded (cap or stream)
- [ ] No new operation blocks the MCP stdio reader thread
- [ ] New LLM prompts have been reviewed for context budget impact

### Observability

- [ ] New component logs a `logger.info` at initialization
- [ ] Every new failure path logs at `WARNING` or `ERROR` with structured context
- [ ] No file content, LLM response text, or credentials appear in log output
- [ ] New progress-emitting code uses the existing `ProgressEvent` type

### Future Compatibility

- [ ] No use of Python features unavailable in Python 3.10
- [ ] No hard-coded model name strings outside `src/config/settings.py`
- [ ] New tool parameters are optional-with-default or are added in a new tool rather than modifying an existing one's signature

---

## 17. Architecture Evolution Policy

Pearl's architecture is not frozen — it must evolve as capabilities grow and production
experience accumulates. This section defines the process for changing it safely.

### 17.1 Change Classification

Not all changes to `src/` are architectural. Use this table to determine whether a
change requires an ADR and architect sign-off:

| Change type | ADR required | Sign-off required |
|---|---|---|
| New tool function | No | No |
| New MCP method | Yes | Yes |
| New layer or reclassification of existing module | Yes | Yes |
| New context variable | Yes | Yes |
| New shared mutable global | Yes | Yes |
| Adding a parameter to an existing public interface | No (if backward-compatible) | No |
| Removing a public interface | Yes | Yes |
| Changing the approval invariant or `PatchManager` contract | Yes | Yes — architect + security review |
| New LLM provider | No | No |
| Changing `Settings` key names | Yes | Yes |

### 17.2 ADR Process

1. Create `docs/adr/NNNN-short-title.md` using the standard template:
   ```
   # NNNN — Title
   Date: YYYY-MM-DD
   Status: Proposed | Accepted | Superseded by MMMM

   ## Context
   What situation forced this decision?

   ## Decision
   What was decided, stated precisely.

   ## Consequences
   What gets better, what gets harder, what other rules change.

   ## Alternatives considered
   Why the alternatives were rejected.
   ```
2. Reference the ADR in the relevant rule in this document's Section 14 table.
3. If the ADR supersedes an existing one, mark the old ADR `Status: Superseded by NNNN`.

### 17.3 Deprecation Policy

**Rule EV-1:** A public interface (MCP method, agent public method, tool function name)
MUST be deprecated for at least one sprint before removal. During that sprint the old
name MUST still work, and every call site MUST emit a `DeprecationWarning`.

```python
# CORRECT — deprecated but still functional for one sprint
def plan_and_run(self, prompt: str) -> ExecutionReport:
    # DEPRECATED(ADR-006): removed in Sprint N+1. Use run_autonomous().
    import warnings
    warnings.warn("plan_and_run is deprecated; use run_autonomous()", DeprecationWarning, stacklevel=2)
    return self.run_autonomous(prompt)
```

**Rule EV-2:** Deprecations MUST be announced in `CHANGELOG.md` under `[Unreleased]`
on the same sprint they are introduced.

**Rule EV-3:** An internal private function (prefixed `_`) may be removed without a
deprecation cycle, but MUST still be referenced in the removal commit message.

### 17.4 Graduated Rollout

For high-risk changes (anything touching the approval invariant, the MCP protocol,
or the execution loop):

1. **Stage the change behind a feature flag in `Settings`** — default off.
2. **Ship in one sprint** with the flag off, collecting integration test coverage.
3. **Enable by default in the next sprint** after one sprint of test-only validation.
4. **Remove the flag** in the sprint after that.

This three-sprint window ensures no single sprint both introduces and activates a
high-risk architectural change.

---

## 18. Scalability Rules

Pearl is a single-process, single-user tool. "Scalability" in Pearl's context means:
(a) handling large repositories without OOM or timeouts, (b) handling long plans without
exceeding LLM context windows, (c) handling concurrent MCP requests without data races.
It does not mean horizontal scaling or distributed state.

### 18.1 Repository Scale

**Rule SC-1:** `RepositoryIndex` MUST stream or lazily walk the filesystem. It MUST NOT
load all file contents into memory. Only metadata (path, symbol names, line counts) is
held in memory.

**Rule SC-2:** `MAX_SEARCH_RESULTS = 200` is the hard cap for any single tool call
that returns filesystem matches. This applies to text search, symbol search, and
reference search. The cap MUST be enforced before the results are serialized into any
LLM prompt.

**Rule SC-3:** Any tool that reads a file's content MUST truncate at a reasonable
maximum (currently `MAX_FILE_SIZE_BYTES` in `Settings`) and MUST signal the truncation
in its return value. Returning a 10 MB file as a single string to the planning prompt
is a fatal context budget error.

### 18.2 LLM Context Budget

**Rule SC-4:** Before assembling a planning prompt, the assembled context MUST be
estimated in tokens using `len(text) // 4` as a conservative estimate. If the estimate
exceeds `Settings.MAX_CONTEXT_TOKENS`, the context MUST be reduced (fewer history turns,
fewer file contents) before the call is made.

**Rule SC-5:** `Memory.recent_messages()` MUST accept and respect the `limit` parameter.
The default limit MUST be small enough that the full history of the oldest long-running
session still fits in a planning prompt.

**Rule SC-6:** Plans MUST be bounded. `DEFAULT_MAX_ITERATIONS` (currently 20) caps the
number of tool calls per run. `DEFAULT_MAX_REPLANS` (currently 3) caps the number of
replanning cycles. These are not arbitrary — they bound worst-case LLM cost per request.
Raising them requires architect sign-off and a cost analysis.

### 18.3 Concurrency

**Rule SC-7:** The MCP server MUST process one `pearl/runAutonomous` call at a time.
If a second call arrives while one is in flight, it MUST be rejected with a clear error
(`"An autonomous run is already in progress"`), not queued silently.

**Rule SC-8:** Context variables (`_ACTIVE_PATCH_MANAGER`, `_ACTIVE_COMMAND_APPROVER`)
are per-thread. If Pearl ever supports true multi-threaded execution, these MUST be
migrated to a per-run explicit parameter — not left as thread-locals. Document this
constraint with a `# SCALABILITY-NOTE:` comment at each declaration site.

**Rule SC-9:** The `RepositoryIndex` module global `_INDEX_CACHE` is not thread-safe
for concurrent writes. Any new code path that calls `index_repository()` from a
background thread MUST acquire an explicit lock documented at the call site.

### 18.4 Memory Footprint

**Rule SC-10:** Do not accumulate unlimited history in `Memory`. The in-memory message
log MUST be either capped or summarized when it exceeds `Settings.MAX_MEMORY_MESSAGES`.
A session that runs for hours in a large codebase MUST NOT exhaust RAM.

---

## 19. Observability Rules

Observability is not optional. Pearl runs autonomously; when something goes wrong, the
log is often the only evidence. These rules define what must be logged, what must never
appear in logs, and how progress events are structured.

### 19.1 Log Levels

| Level | When to use | Example |
|---|---|---|
| `DEBUG` | Fine-grained trace for active debugging; disabled by default | `"Planner received %d tool descriptions"` |
| `INFO` | Lifecycle events, state transitions, significant actions | `"Pearl Agent initialized"`, `"Checkpoint saved: abc123"` |
| `WARNING` | Recoverable failures; execution continues | `"Progress callback raised: %s — ignored"`, `"Checkpoint skipped: %s"` |
| `ERROR` | Non-recoverable failures that end or corrupt a run | `"LLM provider returned unexpected format: %s"` |
| `CRITICAL` | Use is forbidden in Pearl | Reserved for the logging framework itself |

**Rule OBS-1:** Every component MUST log at `INFO` level when it initializes. This
creates a deterministic startup sequence in the log that makes "the agent never started"
bugs trivial to diagnose.

```python
# CORRECT
class AutonomousExecutor:
    def __init__(self, ...):
        ...
        logger.info("AutonomousExecutor initialized (max_iterations=%d)", max_iterations)
```

**Rule OBS-2:** Every `except` block in the execution path MUST log the exception
before swallowing it. A swallowed exception with no log line is a silent failure.

```python
# CORRECT
except Exception as exc:
    logger.warning("Progress callback raised: %s — ignored", exc)

# VIOLATION
except Exception:
    pass  # silent
```

### 19.2 What MUST NOT Appear in Logs

**Rule OBS-3:** File contents MUST NOT be logged at any level. Log the path and byte
count instead.

```python
# VIOLATION
logger.debug("File content: %s", content)

# CORRECT
logger.debug("Read file: %s (%d bytes)", path, len(content))
```

**Rule OBS-4:** LLM response text MUST NOT be logged at `INFO` or above. It may be
logged at `DEBUG` level only, with a clear label, for active development.

**Rule OBS-5:** Credentials, API keys, and environment variable values MUST NEVER be
logged at any level. `Settings` values that contain secrets MUST be redacted:

```python
# CORRECT — log presence, not value
logger.info("LLM provider: %s, API key: %s", Settings.LLM_PROVIDER,
            "***" if Settings.OPENAI_API_KEY else "(not set)")
```

**Rule OBS-6:** User prompts MUST be logged at `INFO` level only by the agent facade
(once, on receipt). They MUST NOT be re-logged by the planner, executor, or any tool.
Logging the same prompt at multiple layers produces confusing duplicate entries.

### 19.3 Progress Events

**Rule OBS-7:** `ProgressEvent` is the structured vocabulary for communicating execution
state to callers. Every significant state transition in `AutonomousExecutor` MUST emit
a `ProgressEvent` via the `on_progress` callback. "Significant" means: plan received,
step started, step completed, step failed, replanning triggered, approval paused,
checkpoint saved, run finished.

**Rule OBS-8:** A `ProgressEvent`'s `message` field MUST be human-readable and
actionable. It appears directly in the UI. "step 3" is not actionable; "Writing
`src/agent/executor.py` (patch staged for approval)" is.

**Rule OBS-9:** `on_progress` callback failures MUST be swallowed (Rule ERR-3). The
log line on swallow MUST identify the failed callback, not just the exception type.

### 19.4 Structured Logging Context

**Rule OBS-10:** Log calls in the execution path SHOULD include task-scoped context
(task ID, tool name, iteration number) as `%s`-formatted arguments, not f-strings.
This keeps the log record's `msg` constant across invocations, enabling log aggregation
tools to group by message template.

```python
# CORRECT — msg is constant, values are arguments
logger.info("Tool %s completed in %dms", tool_name, elapsed_ms)

# AVOID — every unique tool name produces a different msg template
logger.info(f"Tool {tool_name} completed in {elapsed_ms}ms")
```

---

## 20. Future Compatibility Rules

Pearl's architecture must remain coherent as Python, the MCP spec, LLM providers, and
VS Code evolve. These rules reduce the cost of future migrations.

### 20.1 Python Version Compatibility

**Rule FC-1:** Pearl targets Python 3.10+. Use of features introduced after 3.10
(`match`/`case` structural pattern matching in 3.10, `Self` type in 3.11, `tomllib`
in 3.11, etc.) MUST be guarded by a `sys.version_info` check or conditional import
until the minimum version is raised.

**Rule FC-2:** Do NOT use deprecated Python APIs. The canonical check is:
`python -W error::DeprecationWarning -m pytest` — all tests must pass under this flag.

**Rule FC-3:** Type annotations MUST use `from __future__ import annotations` at the
top of every module. This enables forward references and keeps annotations compatible
with Python 3.10's evaluation semantics.

### 20.2 MCP Spec Compatibility

**Rule FC-4:** Pearl implements MCP as it existed when the `initialize` exchange was
designed. When the upstream MCP spec advances, Pearl MUST advertise its protocol version
in the `initialize` response and MUST maintain backward compatibility with the previous
version for at least one sprint.

**Rule FC-5:** Pearl-specific extensions (`pearl/*` methods) MUST NOT conflict with
any method names or capability keys defined in the upstream MCP specification. Before
naming a new extension, check the MCP spec changelog.

**Rule FC-6:** The `capabilities.experimental` map in `initialize` is the forward-
compatibility surface for unfinished features. A feature that has been stable for two
sprints MUST be promoted from `experimental` to a top-level capability.

### 20.3 Provider API Compatibility

**Rule FC-7:** LLM provider client libraries (`openai`, `anthropic`, `google-generativeai`)
MUST be pinned to a minimum version in `pyproject.toml`. Pearl MUST NOT use provider
APIs marked as deprecated in the pinned version or above.

**Rule FC-8:** Model names (e.g., `"gpt-4o"`, `"claude-3-5-sonnet-20241022"`) MUST
reside exclusively in `src/config/settings.py`. Hard-coding a model name in any other
module creates a change-24-files problem when the model is retired.

```python
# CORRECT — one place to update
# src/config/settings.py
DEFAULT_MODEL: str = "claude-sonnet-4-5"

# VIOLATION — scattered across multiple files
# src/agent/planner.py
response = client.generate(prompt, model="claude-3-opus-20240229")
```

**Rule FC-9:** Every provider module in `src/llm/providers/` MUST implement the same
`BaseProvider` interface. A new provider MUST NOT require callers to special-case it.

### 20.4 Tooling and Dependency Compatibility

**Rule FC-10:** Pearl's test suite MUST pass with the two most-recent minor releases
of all direct dependencies. Before pinning to a specific patch version, confirm the
fix is not available in the prior minor.

**Rule FC-11:** Type-checker compatibility: `pyright --verifytypes src` MUST produce
zero errors for all public interfaces. This ensures that downstream tools and IDE
integrations built against Pearl's public API receive correct type information.

**Rule FC-12:** Every new public function signature MUST include complete type
annotations for parameters and return type. Untyped public interfaces are a maintenance
debt that compounds with every downstream consumer.

### 20.5 Cross-Platform Compatibility

**Rule FC-13:** Path construction MUST use `pathlib.Path`, never string concatenation
with `/` or `os.sep`. `Path` handles platform separator differences transparently.

```python
# CORRECT
config_path = Path(__file__).resolve().parent / "config.toml"

# VIOLATION — Windows-incompatible
config_path = os.path.dirname(__file__) + "/config.toml"
```

**Rule FC-14:** Shell commands in tests MUST assume POSIX semantics unless the test
is explicitly marked `@pytest.mark.posix_only`. Commands that use `bash`-isms (`[[`,
process substitution) MUST not appear in platform-agnostic test fixtures.

---

*This document is part of the Pearl Engineering Standards Series.*  
*Next: [02_CODING_STANDARDS.md](02_CODING_STANDARDS.md)*
