# 🦪 Pearl

> A self-hostable AI coding agent — plan, execute, and review multi-step
> engineering tasks against your own codebase, from your own VS Code or browser.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Status](https://img.shields.io/badge/Status-Public%20Beta-orange)
![License](https://img.shields.io/badge/License-MIT-yellow)
![Version](https://img.shields.io/badge/Version-1.4.0--beta-informational)

---

## Overview

Pearl is an open-source AI coding agent. It plans multi-step engineering
tasks against a real repository, executes them through a sandboxed tool
system (file edits, shell commands, git, repository search), pauses for
human approval before any file on disk actually changes, and remembers
what it did during the session.

**Pearl runs entirely offline by default.** No API keys, no cloud services,
no data leaving your machine. The default provider downloads a
[Qwen2.5-1.5B-Instruct Q4\_K\_M](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF)
model (~1 GB) on first run and runs it locally via
[llama-cpp-python](https://github.com/abetlen/llama-cpp-python).

Pearl ships as two halves:

- **A Python backend** (`src/`) — the planner, synthesizer, tool system,
  patch manager, repository index, and MCP server. Usable standalone from
  a terminal, a browser UI, or any MCP-speaking client.
- **A VS Code extension** (`vscode-extension/`) — a chat webview that
  drives the backend, renders plans and diffs, and gates every write
  behind an in-editor Approve/Reject UI.

---

## What's New (Milestones 1 & 2)

### Milestone 1 — Grounded AI loop

- **Synthesizer**: after tool execution, a dedicated LLM call turns the
  collected evidence into a coherent Markdown answer. The backend now emits
  an explicit `final_answer` field — the UI never synthesises answers
  from raw tool output via JavaScript heuristics.
- **Repository context injection**: the planner receives a semantic context
  block (symbols, imports, graph neighbours) for every prompt, grounded in
  the actual workspace index.
- **Grounded planning**: the planning prompt now requires *search before
  read* for unknown paths, uses web tools for current/online questions, and
  treats tool results as evidence — not as suggestions the model can ignore.

### Milestone 2 — Reliable autonomous loop

- **Plan-time retry (G1)**: if the first plan attempt fails (bad JSON,
  validation error), the executor retries once with the error fed back.
  `LLMCancelled` is never swallowed.
- **Verification before synthesis (G2)**: after approval, tests run and
  git status is captured before the Synthesizer is called, so the final
  answer can confirm whether changes actually landed.
- **Inference mutex (G3)**: `llama_cpp.Llama` is not thread-safe;
  a module-level lock serialises all `create_chat_completion()` calls so
  concurrent planning and chat never race on the shared singleton.
- **Thread-safe workspace CWD (G4)**: `chat_stream()` no longer calls
  `os.chdir()` — the workspace path is passed explicitly so concurrent
  requests cannot clobber each other's working directory.
- **Repeated-action guard (G5)**: if the same tool with the same arguments
  fails twice across replanning attempts, the executor aborts immediately
  rather than looping forever on a broken step.
- **Failed steps in evidence (G7)**: the Synthesizer evidence block now
  includes failed steps labelled `[FAILED]` so the model knows what was
  not accomplished.
- **Duplicate-write detection (G8)**: the plan validator rejects any plan
  that writes the same path more than once, preventing silent clobber.

---

## Features

- **Autonomous multi-step execution** with plan-time retry, reflection,
  and controlled replanning when a step fails, plus mid-run cancellation.
- **Grounded final answers** — a dedicated Synthesizer LLM call turns tool
  results into a Markdown answer; failed steps are included as evidence.
- **Patch-preview approval** — every file write is staged as a diff first;
  nothing touches disk until a human approves it.
- **Standalone web UI** — a FastAPI server + ChatGPT-style browser client
  (`pearl_ui/`) for running Pearl without VS Code.
- **Repository intelligence** — a cached, incremental index of every
  Python file, symbol, and import in the workspace, used for symbol
  lookup, reference search, and project summaries.
- **Symbol-aware editing** — replace or insert a function/class/method by
  name via AST, preserving surrounding formatting exactly.
- **Context management** — ranks and compresses the most relevant files
  for a query into a token-budgeted context string.
- **Workspace memory** — a session-scoped record of what Pearl has read,
  edited, and learned, fed back into future planning.
- **Git tools** — status, diff, log, branch, commit, and restore, exposed
  as regular tools the planner can call.
- **Zero-config local inference** — `llama-cpp-python` downloads and runs
  Qwen2.5-1.5B-Instruct Q4\_K\_M on first use; no API key, no Ollama.
- **Any OpenAI-compatible provider** — Ollama, OpenAI, OpenRouter, or any
  custom endpoint; optional Anthropic and Gemini providers.
- **MCP server** — the same tool registry, planner, and memory the CLI
  uses, exposed over stdio JSON-RPC for any MCP client.

---

## Architecture

```
        User
          │
          ├──── Browser  ──▶  FastAPI Web UI (pearl_ui/)
          │
          └──── VS Code Extension  (chat webview, plan/patch approval UI)
                   │  MCP (JSON-RPC 2.0 over stdio)
                   ▼
               MCP Server  (src/mcp)
                   │
                   ▼
                Planner  ──uses──▶  SemanticContextBuilder ──▶ RepositoryIndex
                   │
                   ▼
          AutonomousExecutor
           ├── plan_with_retry()    ← G1: retry bad plans once
           ├── validate_plan()      ← G8: duplicate write detection
           ├── _failed_action_counts ← G5: repeated-action guard
           │
           ▼
          Tool Dispatcher  (src/agent/dispatcher.py)
           │
           ▼
          PatchManager  ──▶ pause for approval ──▶ apply to disk ──▶ Git
           │
           ▼
          Synthesizer   ← G2/G7: grounded final answer with verification
           │
           ▼
        final_answer (Markdown)
```

See [`docs/engineering/01_ARCHITECTURE_RULES.md`](docs/engineering/01_ARCHITECTURE_RULES.md)
for the full module layering rules every contributor must follow.

---

## Installation

Requires **Python 3.10+**. Works on Linux, macOS, Windows, and WSL.

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

Install Pearl in editable mode:

```bash
pip install -e .
```

Install local inference support (downloads model on first run):

```bash
pip install llama-cpp-python \
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
```

Optional extras:

```bash
pip install -e ".[claude]"    # Anthropic provider
pip install -e ".[gemini]"    # Gemini provider
pip install -e ".[dev]"       # pytest, ruff, pyflakes — for contributing
pip install -e ".[all]"       # everything above
```

---

## Quick Start

### Option A — Standalone Web UI (recommended for first try)

```bash
# Start the web server (from the Pearl-AI directory):
uvicorn src.api.server:app --reload

# Open http://localhost:8000 in your browser.
# Pearl downloads the local model (~1 GB) on the first request.
```

### Option B — VS Code Extension

See [VS Code Extension](#vs-code-extension) below.

### Option C — Terminal REPL

```bash
cd /path/to/your/project
pearl
```

`pearl` starts an interactive REPL in the current directory. Type a
request and Pearl plans and executes it, pausing for approval before
any file write.

---

## Local Inference (Default)

Pearl's default provider is `local` — it runs
**Qwen2.5-1.5B-Instruct Q4\_K\_M** on your CPU via `llama-cpp-python`.

| Property | Value |
|---|---|
| Model | Qwen2.5-1.5B-Instruct Q4\_K\_M |
| Size | ~1 065 MB (downloaded once to `~/.pearl/models/`) |
| Speed | ~16 tok/s on a mid-range laptop CPU (AVX2) |
| License | Apache 2.0 |
| Context | 2 048 tokens |
| API key | none required |

Override the model via `.env`:

```bash
LOCAL_MODEL_REPO=Qwen/Qwen2.5-1.5B-Instruct-GGUF
LOCAL_MODEL_FILE=qwen2.5-1.5b-instruct-q4_k_m.gguf
```

---

## Configuration

Pearl reads a `.env` file from its own repository root.

| Variable | Default | Purpose |
|---|---|---|
| `PEARL_LLM_PROVIDER` | `local` | One of `local`, `ollama`, `openai`, `openrouter`, `custom`, `claude`, `gemini`. |
| `LOCAL_MODEL_REPO` | `Qwen/Qwen2.5-1.5B-Instruct-GGUF` | HuggingFace repo for the GGUF model. |
| `LOCAL_MODEL_FILE` | `qwen2.5-1.5b-instruct-q4_k_m.gguf` | GGUF filename inside the repo. |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama's OpenAI-compatible endpoint. |
| `OLLAMA_MODEL` | `llama3` | Model tag — set to `qwen2.5-coder:3b` for coding tasks. |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | — / `gpt-4o-mini` | Used when `PEARL_LLM_PROVIDER=openai`. |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` | — / `openrouter/auto` | Used when `PEARL_LLM_PROVIDER=openrouter`. |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | — / `claude-sonnet-4-5` | Used when `PEARL_LLM_PROVIDER=claude`. |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | — / `gemini-2.5-flash` | Used when `PEARL_LLM_PROVIDER=gemini`. |
| `PEARL_INFERENCE_API_KEY` / `PEARL_INFERENCE_BASE_URL` | — | Pearl-hosted inference endpoint (optional). |

All settings live in [`src/config/settings.py`](src/config/settings.py).

---

## VS Code Extension

The extension in [`vscode-extension/`](vscode-extension/) is a chat
webview backed by the same Python MCP server — it spawns
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
- An inline **patch approval** step — review a unified diff for every
  staged file write, approve or reject before anything is written to disk.
- A **memory explorer** tree view showing what Pearl has read, edited,
  and remembered during the session.
- A status bar item reflecting the MCP connection state.

---

## MCP (Model Context Protocol)

Pearl's backend is an MCP server (`python -m src.mcp`) speaking
newline-delimited JSON-RPC 2.0 over stdio.

| Method | Description |
|---|---|
| `initialize` | Handshake — protocol version, server info, capabilities. |
| `tools/list` | Lists every registered tool as an MCP tool descriptor. |
| `tools/call` | Executes a tool by name. |
| `pearl/planOnly` | Returns the planned steps without executing. |
| `pearl/runAutonomous` | Runs the full autonomous loop (plan → execute → synthesize → pause for approval). |
| `pearl/approvePatches` / `pearl/rejectPatches` | Resumes a paused autonomous run after a human decision. |
| `pearl/chat` | Sends a message straight to the LLM (no planning/tools). |
| `pearl/memory` | Returns the server's current `WorkspaceMemory` contents. |
| `pearl/checkpointCreate` / `pearl/checkpoints` / … | Checkpoint system — see [`docs/checkpoints.md`](docs/checkpoints.md). |
| `shutdown` / `exit` | Graceful shutdown. |

---

## Autonomous Execution

`AutonomousExecutor` (`src/agent/executor.py`) drives a full run:

1. **Plan** — `Planner` produces a structured tool sequence grounded in
   semantic workspace context. If the first plan fails to parse, it retries
   once with the error fed back (G1).
2. **Validate** — `validate_plan()` checks step count, forbidden paths,
   extension hallucination, read-before-write, and duplicate writes (G8).
3. **Execute** — each step runs through the dispatcher. Failed steps are
   recorded; if the same tool+args fails twice, execution aborts (G5).
4. **Replan** — on recoverable failure, one replan is attempted. The
   failed step and its error are included in the replan prompt.
5. **Approve** — any step that would write a file pauses the run for
   human approval via `PatchManager`. No write reaches disk without it.
6. **Verify** — after approval, tests run and git status is captured (G2).
7. **Synthesize** — the `Synthesizer` makes one final LLM call, grounded
   in both tool results (succeeded and failed) and verification data,
   producing a `final_answer` in Markdown (G7).

---

## Patch Approval

File-writing tools (`create_file`, `edit_lines`, `patch_file`,
`replace_in_file`, and the symbol-aware editors) never write to disk
directly — they stage a unified diff through `PatchManager`
(`src/tools/patch_manager.py`) and the executor pauses. Only after an
explicit `approve()` is the patch applied; `reject()` discards it.

This is the mechanism that guarantees **no autonomous run modifies your
working tree without a human in the loop**.

---

## Checkpoints

Undo for anything Pearl writes. A checkpoint is a snapshot of the whole
workspace, taken automatically right before an approved patch batch
reaches disk, or on demand. Snapshots live in a shadow git repository
outside the workspace (`~/.pearl/workspaces/<key>/`). See
[`docs/checkpoints.md`](docs/checkpoints.md) for the full guide.

---

## Workspace Memory

`WorkspaceMemory` (`src/memory/workspace_memory.py`) is a session-scoped
record of files read/edited, symbols created, and freeform notes. It has
no persistence across process restarts by design — its job is to keep a
long single session coherent.

---

## Benchmarks

The benchmark suite (`benchmarks/run_benchmark.py`) drives Pearl's real
MCP server against shallow clones of FastAPI, Flask, Django, React,
LangChain, and `requests`. See [`benchmarks/REPORT.md`](benchmarks/REPORT.md)
for the full write-up; headline numbers:

| Metric | Result |
|---|---|
| Planning + execution (read-only task) | 18s–51s across 6 repos |
| Planning + execution (patch-producing task) | 17s–30s across 3 repos |
| Patch approval round trip | ~0.15s (no LLM involved) |
| Task success rate | 9/9 (100%) |
| Repository indexing (66-file repo, cold) | ~0.2s |

---

## Troubleshooting

**Model download is slow / fails.**
The first run downloads ~1 GB via `huggingface_hub`. Check your internet
connection; the download resumes from where it left off if interrupted.
Set `HUGGING_FACE_HUB_TOKEN` in `.env` if your network requires auth.

**`llama-cpp-python` import error.**
Install it for your platform:
```bash
pip install llama-cpp-python \
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu
```
For CUDA GPU acceleration, see the
[llama-cpp-python installation guide](https://github.com/abetlen/llama-cpp-python#installation-with-specific-hardware-acceleration).

**Pearl calls a model that doesn't exist / wrong Ollama model.**
Check that `.env` sits at Pearl's own repository root — settings are
resolved relative to `settings.py`'s location, independent of the
workspace `cwd` Pearl was launched from.

**Ollama connection refused (when using `PEARL_LLM_PROVIDER=ollama`).**
Confirm `ollama serve` is running and `OLLAMA_BASE_URL` matches its port
(default `11434`).

**A patch never appears for approval.**
Only the dedicated edit tools stage patches; a step that uses
`execute_shell` to write a file bypasses `PatchManager` by design —
shell commands are not diffed.

---

## FAQ

**Does Pearl require an API key?**
No — the default provider is `local`, running fully locally with no
external calls.

**Can Pearl modify files without asking?**
No. Every file-writing tool stages a diff via `PatchManager` and the run
pauses until it's approved or rejected — this is not configurable per-tool
by design.

**Does Pearl index non-Python files?**
`RepositoryIndex`/`SymbolEditor` are Python-only today; other file types
are still readable/writable/searchable via the generic file and
text-search tools, just without symbol-level intelligence.

**Can I use a hosted model instead of the local one?**
Yes — set `PEARL_LLM_PROVIDER` to `openai`, `openrouter`, `claude`, or
`gemini` and the matching API key/model variables.

**How much RAM does the local model use?**
Qwen2.5-1.5B Q4\_K\_M uses approximately 1.2 GB of RAM at runtime.
The 0.5B variant (override `LOCAL_MODEL_FILE`) uses ~0.5 GB.

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

Read [`CLAUDE.md`](CLAUDE.md) before making any change — it is the only
document every contributor must read. It covers layering rules, the
approval invariant, tool authoring, planning changes, LLM call rules,
error handling, and when to update specifications.

See [`CHANGELOG.md`](CHANGELOG.md) for release history and
[`ROADMAP.md`](ROADMAP.md) for what's planned next.

---

## License

[MIT](LICENSE)
