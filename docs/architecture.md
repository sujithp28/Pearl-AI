# Pearl Architecture

This document explains how a request flows from a user through Pearl's
components, and what each module owns. See the [README](../README.md)
for installation and usage; this is the "how it fits together" reference.

## Component diagram

```
        User
          │
          ▼
   VS Code Extension                (vscode-extension/src/)
     chatController, chatPanel,
     planApproval, patchApproval
          │  MCP (JSON-RPC 2.0, newline-delimited, over stdio)
          ▼
      MCP Server                    (src/mcp/server.py)
          │
          ▼
       Planner  ───uses───▶  ContextBuilder ───uses───▶ RepositoryIndex
    (src/agent/planner.py)  (src/repository/context.py)  (src/tools/repo_tools.py)
          │                                                       ▲
          │ plan (list[ToolCall])                                 │ reads/records
          ▼                                                       │
  AutonomousExecutor ───records execution/edits───▶ WorkspaceMemory
  (src/agent/executor.py)                          (src/memory/workspace_memory.py)
          │
          │ dispatch(tool_name, args)
          ▼
     Tool Dispatcher                (src/agent/dispatcher.py)
          │
          ▼
     Tool Registry                  (src/tools/registry.py)
     file / edit / shell / git / repo tools
          │
          ▼ (file-writing tools only)
      PatchManager                  (src/tools/patch_manager.py)
          │
          ▼
   pause for approval ──approve()──▶ write to disk ──▶ Git
          │
       reject() ──▶ discard, resume without the change
```

## Request lifecycle

1. **VS Code extension** (or any MCP client) sends a JSON-RPC request —
   typically `pearl/runAutonomous` with a natural-language prompt — over
   the child process's stdin.
2. **`MCPServer`** (`src/mcp/server.py`) dispatches the method to the
   appropriate handler, ultimately calling into `AutonomousExecutor`.
3. **`Planner`** (`src/agent/planner.py`) builds a prompt from the user's
   request plus (optionally) a `ContextBuilder`-assembled workspace
   context string, sends it to the configured LLM via `LLMClient`
   (`src/llm/client.py`), and parses the response into an ordered list of
   `ToolCall`s.
4. **`AutonomousExecutor`** (`src/agent/executor.py`) executes each
   `ToolCall` in turn through the `ToolDispatcher`. If a step fails, the
   executor feeds the failure back into a **replanning** prompt (bounded
   by `DEFAULT_MAX_REPLANS`) rather than aborting the whole run. Progress
   is reported incrementally via `ProgressEvent`s, passed to whatever
   `on_progress` callback the caller supplied. Over MCP, `MCPServer`
   forwards each one as a `pearl/progress` JSON-RPC notification —
   written to the same stdio stream, ahead of the eventual response —
   via a `notify` callback threaded through `handle_request`
   (`src/mcp/server.py`); omitting it (as every direct
   `handle_request()` call in tests does) reproduces the exact prior,
   non-streaming behavior, so this is purely additive.
5. **`ToolDispatcher`** (`src/agent/dispatcher.py`) looks up the tool by
   name in the `ToolRegistry` and invokes it with validated arguments,
   raising `ToolNotFoundError`/`ToolExecutionError` with an actionable
   message on failure.
6. **File-writing tools** (`create_file`, `edit_lines`, `patch_file`,
   `replace_in_file`, and the symbol-aware editors in
   `src/tools/symbol_editor.py`) never touch disk directly — they stage a
   unified diff through `PatchManager` (`src/tools/patch_manager.py`).
   Staging a patch pauses the executor (`_PausedState`) and the run
   surfaces the diff for approval instead of continuing.
7. **Approval** — an MCP client calls `pearl/approvePatches` or
   `pearl/rejectPatches`. Approval writes the staged content to disk and
   resumes execution from where it paused; rejection discards it and
   resumes without the change. This is the one path by which Pearl ever
   modifies the working tree.
8. Throughout, **`WorkspaceMemory`** (`src/memory/workspace_memory.py`)
   records what happened this session (files read/edited, symbols
   created, freeform notes) — a session-scoped log, not a database, whose
   job is to keep a long single session coherent, not to persist across
   restarts.
9. **`RepositoryIndex`** (`src/tools/repo_tools.py`) is the sole source of
   "what files/symbols/imports exist" — built once per workspace root and
   cached in-process (`_INDEX_CACHE`), consulted by symbol lookup,
   reference search, and `ContextBuilder`'s file ranking. It never gets
   re-walked or re-parsed by other modules; they all read from it.

## Module reference

| Module | Owns |
|---|---|
| `src/mcp/server.py` | JSON-RPC method dispatch, protocol handshake, stdio framing. |
| `src/agent/planner.py` | Turning a prompt (+ optional context) into an ordered `list[ToolCall]` via the LLM. |
| `src/agent/executor.py` | Running a plan step-by-step, replanning on failure, pause/resume around patch approval, cancellation. |
| `src/agent/dispatcher.py` | Looking up and invoking a tool by name with validated arguments. |
| `src/agent/agent.py` | `PearlAgent` — the CLI-facing façade wiring dispatcher/executor/planner/memory together. |
| `src/tools/registry.py` | Registration and lookup of every `@tool`-decorated function. |
| `src/tools/metadata.py` | The `@tool` decorator — attaches name, description, and JSON-Schema parameter spec. |
| `src/tools/patch_manager.py` | Staging unified diffs, applying or discarding them — the only path to a disk write. |
| `src/tools/symbol_editor.py` | AST-based lookup of a function/class/method's exact source range, for surgical edits. |
| `src/tools/repo_tools.py` | `RepositoryIndex` — the cached file/symbol/import index; symbol search, reference search, project summary. |
| `src/repository/context.py` | `SemanticContextBuilder` — the single retrieval implementation: ranks files by relevance and renders them. `src/agent/context_engine.py` budgets and assembles what it returns. |
| `src/tools/git_tools.py` | Thin, validated wrappers over the `git` CLI (status, diff, log, branch, commit, restore). |
| `src/memory/workspace_memory.py` | Session-scoped record of files touched, symbols created, and freeform notes. |
| `src/llm/client.py` | Provider-agnostic LLM client (Ollama/OpenAI/OpenRouter/custom/Claude/Gemini) over the OpenAI-compatible chat API. |
| `src/config/settings.py` | All environment-driven configuration; loads `.env` relative to its own file location, not `cwd`. |
| `vscode-extension/src/mcp/` | Spawning and talking to the Python MCP server subprocess from the extension host. |
| `vscode-extension/src/chat/` | The chat webview, plan/patch approval UI, and execution-state rendering. |
| `vscode-extension/src/memory/` | The memory explorer tree view. |

## Design constraints

These boundaries are intentionally stable — changing them ripples through
every module above, so they're kept as-is barring a bug fix:

- **Planner never imports a tool module or `ContextBuilder`.** It accepts
  an already-assembled `workspace_context: str` and knows nothing about
  what produced it. This is what lets `ContextBuilder`'s ranking strategy
  change freely without touching the planner.
- **Only `PatchManager` writes to disk for tracked edits.** Tools compute
  *what* should change; `PatchManager` is the only thing that turns a
  staged edit into bytes on disk, and only after approval. `execute_shell`
  is the one deliberate exception — a shell command can write files
  directly and is not diffed, since shell output isn't structured enough
  to diff meaningfully.
- **`RepositoryIndex` is the only filesystem walker for "what exists."**
  `ContextBuilder`, symbol search, and reference search all read from it
  rather than re-scanning — this is both a correctness property (one
  consistent view of the repo per index build) and the reason indexing
  performance matters disproportionately (see the README's
  [Performance](../README.md#performance) section).
- **MCP methods are additive, not replacements.** `pearl/*` methods sit
  alongside the standard `tools/list`/`tools/call`/`initialize` MCP
  methods rather than replacing them, so any generic MCP client can still
  drive Pearl's tools directly without knowing about the `pearl/*`
  extensions.
