# 🦪 Pearl

> A self-hostable AI coding agent — plan, execute, and review multi-step
> engineering tasks against your own codebase, from your own VS Code.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Status](https://img.shields.io/badge/Status-Public%20Beta-orange)
![License](https://img.shields.io/badge/License-MIT-yellow)
![Version](https://img.shields.io/badge/Version-1.2.0--beta-informational)

---

## Overview

Pearl is an open-source AI coding agent. It plans multi-step engineering
tasks against a real repository, executes them through a sandboxed tool
system (file edits, shell commands, git, repository search), pauses for
human approval before any file on disk actually changes, and remembers
what it did during the session. It talks to any OpenAI-compatible LLM
endpoint — including a fully local [Ollama](https://ollama.com) model, so
it can run entirely offline with no API keys and no data leaving your
machine.

Pearl ships as two halves that speak [MCP](https://modelcontextprotocol.io)
(JSON-RPC 2.0) to each other:

- **A Python backend** (`src/`) — the planner, tool system, patch manager,
  repository index, and MCP server. Usable standalone from a terminal, or
  embedded in any MCP-speaking client.
- **A VS Code extension** (`vscode-extension/`) — a chat webview that
  drives the backend, renders plans and diffs, and gates every write
  behind an in-editor Approve/Reject UI.

---

## Features

- **Autonomous multi-step execution** with reflection and replanning when
  a step fails, plus mid-run cancellation.
- **Patch-preview approval** — every file write is staged as a diff first;
  nothing touches disk until a human (or MCP client) approves it.
- **Repository intelligence** — a cached, incremental index of every
  Python file, symbol, and import in the workspace, used for symbol
  lookup, reference search, and project summaries.
- **Symbol-aware editing** — replace or insert a function/class/method by
  name via AST, preserving surrounding formatting exactly (no full-file
  rewrites for a one-function change).
- **Context management** — ranks and compresses the most relevant files
  for a query into a token-budgeted context string, instead of dumping
  the whole repo into the prompt.
- **Workspace memory** — a session-scoped record of what Pearl has read,
  edited, and learned, fed back into future planning in the same session.
- **Git tools** — status, diff, log, branch, commit, and restore, exposed
  as regular tools the planner can call.
- **Any OpenAI-compatible provider** — Ollama (local, default), OpenAI,
  OpenRouter, or any custom OpenAI-compatible endpoint; optional Anthropic
  and Gemini providers.
- **MCP server** — the same tool registry, planner, and memory the CLI
  uses, exposed over stdio JSON-RPC for any MCP client (the VS Code
  extension is one such client, not a special case).

---

## Architecture

```
        User
          │
          ▼
   VS Code Extension  (chat webview, plan/patch approval UI)
          │  MCP (JSON-RPC 2.0 over stdio)
          ▼
      MCP Server  (src/mcp)
          │
          ▼
       Planner  ──uses──▶  ContextBuilder ──uses──▶ RepositoryIndex
          │                                              ▲
          ▼                                              │
  AutonomousExecutor ──records──▶ WorkspaceMemory ────────┘
          │
          ▼
    Tool Dispatcher  (src/agent/dispatcher.py)
          │
          ▼
     Tool Registry  (file / shell / git / repo / edit tools)
          │
          ▼
      PatchManager  ──▶ pause for approval ──▶ apply to disk ──▶ Git
```

See [`docs/architecture.md`](docs/architecture.md) for a full walkthrough
of every module in this diagram.

---

## Installation

Requires **Python 3.10+**. Works on Linux, macOS, Windows, and WSL — the
backend is pure Python with no OS-specific code paths, and paths are
resolved with `pathlib` throughout.

```bash
git clone https://github.com/sujithp28/Pearl-AI.git
cd Pearl-AI
python -m venv .venv
```

Activate the virtualenv:

```bash
source .venv/bin/activate        # Linux / macOS / WSL
```
```powershell
.venv\Scripts\Activate.ps1       # Windows PowerShell
```

Install Pearl in editable mode (this registers the `pearl` command and
installs `openai`, Pearl's only runtime dependency):

```bash
pip install -e .
```

Optional extras:

```bash
pip install -e ".[claude]"    # Anthropic provider
pip install -e ".[gemini]"    # Gemini provider
pip install -e ".[dev]"       # pytest, ruff, pyflakes — for contributing
pip install -e ".[all]"       # everything above
```

If you'd rather not install the package, `pip install -r requirements.txt`
and run modules directly with `python -m src.main` / `python -m src.mcp`.

---

## Quick Start

Pearl defaults to a local Ollama model, so the fastest path to a working
setup is:

```bash
# 1. Install and start Ollama: https://ollama.com/download
ollama pull qwen2.5-coder:3b
ollama serve   # usually already running as a background service

# 2. From the root of the project you want Pearl to work on:
cd /path/to/your/project
pearl
```

`pearl` starts an interactive REPL in the current directory — that
directory is Pearl's **workspace**, and every tool call (file read/write,
git, repo search) is scoped to it. Type a request and Pearl plans and
executes it, pausing for approval before any file write.

To use Pearl from VS Code instead of the terminal, see
[VS Code Extension](#vs-code-extension) below.

---

## Configuration

Pearl reads a `.env` file from its own repository root (not your target
project's directory — Pearl's workspace and Pearl's own install location
are different things). Copy an example and edit it:

```bash
cp .env.example .env   # if present, otherwise create one — see below
```

| Variable | Default | Purpose |
|---|---|---|
| `PEARL_LLM_PROVIDER` | `ollama` | One of `ollama`, `openai`, `openrouter`, `custom`, `claude`, `gemini`. |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama's OpenAI-compatible endpoint. |
| `OLLAMA_MODEL` | `llama3` | Model tag to use — set to `qwen2.5-coder:3b` or similar for coding tasks. |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` | — / `https://api.openai.com/v1` / `gpt-4o-mini` | Used when `PEARL_LLM_PROVIDER=openai`. |
| `OPENROUTER_API_KEY` / `OPENROUTER_BASE_URL` / `OPENROUTER_MODEL` | — / `https://openrouter.ai/api/v1` / `openrouter/auto` | Used when `PEARL_LLM_PROVIDER=openrouter`. |
| `CUSTOM_API_KEY` / `CUSTOM_BASE_URL` / `CUSTOM_MODEL` | — | Any other OpenAI-compatible endpoint. |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | — / `claude-sonnet-4-5` | Used when `PEARL_LLM_PROVIDER=claude` (requires the `claude` extra). |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | — / `gemini-2.5-flash` | Used when `PEARL_LLM_PROVIDER=gemini` (requires the `gemini` extra). |

All settings live in [`src/config/settings.py`](src/config/settings.py) —
that file is the single source of truth if you need to check a default.

---

## Models & Ollama Setup

Pearl is model-agnostic (anything behind an OpenAI-compatible
`/v1/chat/completions` endpoint works), but is developed and benchmarked
primarily against **Ollama running `qwen2.5-coder:3b`** — small enough to
run on a laptop CPU, capable enough for the planning/tool-calling loop
Pearl relies on.

```bash
ollama pull qwen2.5-coder:3b
echo "OLLAMA_MODEL=qwen2.5-coder:3b" >> .env
```

Larger local models (`qwen2.5-coder:7b`/`14b`) or hosted models (GPT-4o,
Claude, Gemini) will generally plan more reliably on harder tasks at the
cost of latency or an API bill — swap `PEARL_LLM_PROVIDER` and the
matching `*_MODEL` variable, no code changes needed.

---

## VS Code Extension

The extension in [`vscode-extension/`](vscode-extension/) is a chat
webview backed by the same Python MCP server described above — it spawns
`python -m src.mcp` as a subprocess and talks JSON-RPC over its stdio.

**Run from source (development):**

```bash
cd vscode-extension
npm install
npm run compile
```

Then open the `vscode-extension/` folder in VS Code and press `F5` to
launch an Extension Development Host.

**Package for install:**

```bash
npm run package   # produces pearl-vscode-<version>.vsix
```

Install the resulting `.vsix` via VS Code's
"Extensions: Install from VSIX..." command.

The extension provides:

- A chat panel for natural-language requests, with the plan and each
  tool call rendered as it happens.
- An inline **plan approval** step — review the proposed tool calls
  before execution starts.
- An inline **patch approval** step — review a unified diff for every
  staged file write, approve or reject per patch, before anything is
  written to disk.
- A **memory explorer** tree view showing what Pearl has read, edited,
  and remembered during the session.
- A status bar item reflecting the MCP connection state.

---

## MCP (Model Context Protocol)

Pearl's backend is an MCP server (`python -m src.mcp`) speaking
newline-delimited JSON-RPC 2.0 over stdio. Logs go to stderr so stdout
stays clean for the protocol stream. Any MCP client can drive it — the
VS Code extension is one client among many possible ones.

| Method | Description |
|---|---|
| `initialize` | Handshake — protocol version, server info, capabilities. |
| `tools/list` | Lists every registered tool as an MCP tool descriptor (name, description, JSON Schema `inputSchema`). |
| `tools/call` | Executes a tool by name. Tool failures come back as a result with `isError: true`, not a JSON-RPC error. |
| `pearl/planOnly` | Returns the steps `Planner.plan()` would take, without executing — lets a client gate each step behind approval. |
| `pearl/runAutonomous` | Runs the full autonomous loop (plan → execute → reflect/replan on failure → pause for patch approval) for a request. |
| `pearl/approvePatches` / `pearl/rejectPatches` | Resumes a paused autonomous run after a human decision on staged patches. |
| `pearl/chat` | Sends a message straight to the LLM (no planning/tools). |
| `pearl/memory` | Returns the server's current `WorkspaceMemory` contents. Read-only. |
| `shutdown` / `exit` | Graceful shutdown handshake / stops the stdio read loop. |

While `pearl/runAutonomous`/`pearl/approvePatches`/`pearl/rejectPatches`
are in flight, the server also pushes `pearl/progress` **notifications**
(no `id`, no response expected) — one per `AutonomousExecutor` step
transition (planning, executing a step, replanning, awaiting approval,
...) — interleaved with, and ahead of, that call's eventual response.
A client that doesn't care about live progress can simply ignore any
line without an `id`; the request/response contract is unchanged
either way.

Try it manually:

```bash
printf '%s\n%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","method":"exit"}' \
  | python -m src.mcp
```

---

## Tool System

Every capability Pearl has — reading a file, running a shell command,
searching the repo — is a plain Python function registered with the
`@tool` decorator (`src/tools/metadata.py`), which attaches a name,
description, and JSON-Schema-inferred parameter spec. The `ToolRegistry`
holds every registered tool; the `ToolDispatcher` (`src/agent/dispatcher.py`)
looks one up by name and invokes it with validated arguments. The
`Planner` never touches a tool directly — it only ever sees the
registry's descriptions and the dispatcher's `execute(name, args)`
interface, so adding a new tool never requires planner changes.

Tool families: file tools, symbol-aware edit tools, shell tools, git
tools, and repository-intelligence tools (index/search/explain/summarize).

---

## Autonomous Execution

`AutonomousExecutor` (`src/agent/executor.py`) drives a full run: get a
plan from `Planner`, execute each step through the dispatcher, and if a
step fails, feed the failure back into a **replanning** prompt instead of
aborting outright — bounded by a replan limit so a stuck task doesn't loop
forever. A run can be cancelled mid-flight, and any step that would write
a file instead **pauses** the whole run and hands control to patch
approval (see below) before continuing.

---

## Personality

Pearl's own status/progress messages ("Planning...", "Tests passed.",
"Patch ready.") have a configurable voice:

```bash
PERSONALITY=cheeky      # professional | friendly | cheeky | savage (default)
EMOJI_MODE=minimal      # none | minimal | normal | fun
```

This only changes wording — it never touches planning, tool
selection, tool execution, code generation, or any LLM prompt/output;
that boundary is enforced by a static test, not just a convention. See
[`docs/personality.md`](docs/personality.md) for the full personality
table, the automatic "serious mode" override for security-related
messages, and how to add a new personality.

---

## Patch Approval

File-writing tools (`create_file`, `edit_lines`, `patch_file`,
`replace_in_file`, and the symbol-aware editors) never write to disk
directly — they stage a unified diff through `PatchManager`
(`src/tools/patch_manager.py`) and the executor pauses. Only after an
explicit `approve()` (surfaced via `pearl/approvePatches` over MCP, or the
VS Code diff-approval UI) is the patch applied to disk; `reject()` discards
it and execution resumes without the change. This is the mechanism that
guarantees no autonomous run modifies your working tree without a human
in the loop.

---

## Workspace Memory

`WorkspaceMemory` (`src/memory/workspace_memory.py`) is a session-scoped
record — not a database — of what happened this session: files read or
edited, symbols created, and freeform notes (e.g. TODOs surfaced during a
run). It has no persistence across process restarts by design; its job is
to make a *long* single session coherent (via `ContextBuilder`'s workspace
summary), not to be a long-term store.

---

## Context Manager

`ContextManager`/`ContextBuilder` (`src/tools/context_manager.py`) turn a
natural-language query into a single, token-budgeted context string for
the planner: rank every file in `RepositoryIndex` by relevance (symbol
matches, filename matches, recently-touched files from
`WorkspaceMemory`), then render the top files — verbatim if small,
compressed to their matched symbols plus TODO/FIXME lines if large —
until the token budget runs out. It reuses `RepositoryIndex` and
`SymbolEditor` rather than re-parsing or re-walking the filesystem itself.

---

## Repository Index

`RepositoryIndex` (`src/tools/repo_tools.py`) is the sole source of "what
files/symbols/imports exist" in the workspace — every symbol lookup,
reference search, and context-ranking pass reads from it instead of
re-scanning the filesystem. It's built once per root and cached
in-process; noisy directories (`.venv`, `node_modules`, `.git`, build/cache
dirs) are pruned *during* the directory walk, not filtered out afterward,
so indexing cost scales with actual source size, not with how much
unrelated tooling happens to live alongside it (see
[Performance](#performance) below).

---

## Git Tools

Exposed as regular tools (`src/tools/git_tools.py`): `git_status`,
`git_diff`, `git_log`, `git_create_branch`, `git_commit`, `git_restore` —
each a thin, validated wrapper over the `git` CLI, scoped to the
workspace root, so the planner can inspect and commit changes as part of
a larger task.

---

## Symbol Editor

`SymbolEditor` (`src/tools/symbol_editor.py`) locates a function, class,
or method by name via Python's `ast` module and returns its exact source
range (including decorators), so an edit can replace or insert around it
without disturbing the rest of the file's formatting — no whole-file
rewrite for a single-function change.

---

## Benchmarks

The benchmark suite (`benchmarks/run_benchmark.py`) drives Pearl's real
MCP server against shallow clones of FastAPI, Flask, Django, React,
LangChain, and `requests`, exactly as the VS Code extension would. See
[`benchmarks/REPORT.md`](benchmarks/REPORT.md) for the full write-up;
headline numbers from the most recent run:

| Metric | Result |
|---|---|
| Planning + execution (read-only task) | 18s–51s across 6 repos |
| Planning + execution (patch-producing task) | 17s–30s across 3 repos |
| Patch approval round trip | ~0.15s (no LLM involved) |
| Task success rate | 9/9 (100%) |
| Repository indexing (66-file repo, cold) | ~0.2s (see Performance) |

Run it yourself: `python benchmarks/run_benchmark.py` (requires a running
Ollama with the configured model pulled).

---

## Performance

Measured on this project's own repository during beta hardening:

- **`import torch` was removed** from `src/config/settings.py` — it was
  dead weight from an abandoned local-model design (nothing read the
  `Settings.DEVICE` value it computed) and cost **~7 seconds** on every
  process start. Removing it dropped `from src.config.settings import
  Settings` from ~7.2s to ~0.15s.
- **Repository indexing was fixed to prune ignored directories (`.venv`,
  `node_modules`, `.git`) during the filesystem walk instead of after
  globbing everything.** On this repo, cold indexing went from **19.4s to
  0.2s** — a ~99% reduction — since the old code was walking and
  stat'ing every file under `.venv`'s site-packages before throwing them
  away.
- Repeated indexing of the same root is already cached in-process
  (`RepositoryIndex`'s `_INDEX_CACHE`), so the fix above matters most for
  the first index of a session — after that, lookups are sub-millisecond.
- `ContextBuilder.build()` on this repo (66 files, 700 symbols) completes
  in ~0.04s once the index is warm.

---

## Troubleshooting

**Pearl calls a model that doesn't exist / wrong Ollama model.**
Check that `.env` sits at Pearl's own repository root (not your target
project's directory) — settings are resolved relative to `settings.py`'s
location, independent of the workspace `cwd` Pearl was launched from.

**`FileNotFoundError` for a prompt template when running against an
external project.** Make sure you're on a version that resolves
`Planner.PROMPT_FILE`/`REPLAN_PROMPT_FILE` relative to `planner.py`
itself, not `cwd` — this was fixed during Phase 23/24 hardening.

**Ollama connection refused.** Confirm `ollama serve` is running and
`OLLAMA_BASE_URL` matches its port (default `11434`).

**Indexing feels slow on first use.** Confirm no large unrelated
directory (a stray `.venv`, `node_modules`, or vendored dependency) lacks
an entry in `IGNORED_DIRS` (`src/tools/repo_tools.py`) — the walk prunes
known noisy directories by name, not by size heuristics.

**A patch never appears for approval.** Only the dedicated edit tools
(`create_file`, `edit_lines`, `patch_file`, `replace_in_file`, symbol
editors) stage patches; a step that uses `execute_shell` to write a file
bypasses `PatchManager` by design — shell commands are not diffed.

---

## FAQ

**Does Pearl require an API key?** No — the default provider is Ollama,
running fully locally with no external calls.

**Can Pearl modify files without asking?** No. Every file-writing tool
stages a diff via `PatchManager` and the run pauses until it's approved
or rejected — this is not configurable per-tool by design (see
[Patch Approval](#patch-approval)).

**Does Pearl index non-Python files?** `RepositoryIndex`/`SymbolEditor`
are Python-only today; other file types are still readable/writable/
searchable via the generic file and text-search tools, just without
symbol-level intelligence.

**Can I use a hosted model instead of Ollama?** Yes — set
`PEARL_LLM_PROVIDER` to `openai`, `openrouter`, `claude`, or `gemini` and
the matching API key/model variables.

---

## Contributing

```bash
pip install -e ".[dev]"
pytest -q                                  # Python test suite
pytest -q tests/test_e2e_mcp.py            # end-to-end only (real subprocess)
ruff check src/ tests/ benchmarks/         # lint
ruff format src/ tests/ benchmarks/        # format
cd vscode-extension && npm install && npm test   # TypeScript test suite
```

`tests/test_e2e_mcp.py` starts a real `python -m src.mcp` subprocess
and talks to it over real stdio — real framing, real planner, real
tool dispatch, real patch approval, real files. Only the model is
substituted, by the deterministic `scripted` provider, so it needs no
Ollama and runs in CI. Unit tests fake the transport and keep the
logic; these do the reverse, and that's the point: they catch the
integration bugs no in-process test can reach.

You can also run Pearl itself with no model server at all:

```bash
PEARL_LLM_PROVIDER=scripted \
PEARL_SCRIPTED_RESPONSES='["{\"steps\": [{\"tool\": \"pwd\", \"arguments\": {}}]}"]' \
python -m src.mcp
```

CI (`.github/workflows/`) runs the Python suite across 3.10–3.12, `ruff
check`, `ruff format --check`, and the VS Code extension's `tsc` compile
+ test suite on every push and pull request. Please keep the constraints
in [`docs/architecture.md`](docs/architecture.md) in mind before changing
core module boundaries (Planner, Executor, RepositoryIndex,
ContextManager, WorkspaceMemory, PatchManager, SymbolEditor, MCP
protocol) — see its "Design constraints" section.

See [`CHANGELOG.md`](CHANGELOG.md) for release history and
[`ROADMAP.md`](ROADMAP.md) for what's planned next.

---

## License

[MIT](LICENSE)
