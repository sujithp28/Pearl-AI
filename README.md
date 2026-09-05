# 🦪 Pearl

> A self-hostable AI coding agent — plan, execute, verify, and review
> multi-step engineering tasks against your own codebase, from a terminal,
> a browser, or VS Code.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Status](https://img.shields.io/badge/Status-Public%20Beta-orange)
![License](https://img.shields.io/badge/License-MIT-yellow)
![Version](https://img.shields.io/badge/Version-1.2.0--beta-informational)
![Tests](https://img.shields.io/badge/Tests-2%2C769%20passing-brightgreen)

---

## Overview

Pearl is an open-source AI coding agent. It plans multi-step engineering
tasks against a real repository, executes them through a sandboxed tool
system (file edits, shell commands, git, repository search), pauses for
human approval before any file on disk actually changes, runs your tests,
and judges — from that evidence, not from whether a tool merely returned —
whether the task actually succeeded.

```
PLAN → EXECUTE → STAGE → APPROVE → VERIFY → REFLECT → REPLAN or DONE
```

A tool returning without error is not the same thing as the task being
correct. Pearl's reflection phase exists specifically to catch the case
where every step "succeeded" but the tests fail or an unrelated file
changed — and to feed that back into another planning pass rather than
reporting done.

**Pearl runs entirely offline by default.** No API keys, no cloud services,
no data leaving your machine. The default provider downloads a
[Qwen2.5-1.5B-Instruct Q4\_K\_M](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF)
model (~1 GB) on first run and runs it locally via
[llama-cpp-python](https://github.com/abetlen/llama-cpp-python). Point it at
OpenAI, Anthropic, Gemini, or OpenRouter instead with one setting, or mix
both — a fast local model for cheap, frequent work and a stronger remote
one for planning — via [model profiles](#model-routing).

Pearl ships as three surfaces over one Python backend:

- **A terminal CLI** (`python -m src.cli`) — the full loop in your shell,
  with JSON output for scripting and CI.
- **A standalone web UI** (`pearl_ui/`) — a FastAPI server + browser client.
- **A VS Code extension** (`vscode-extension/`) — a chat webview with
  inline diff/patch approval, connected over MCP.

All three drive the same `AutonomousExecutor` — nothing is duplicated
per-surface, so a fix or a new tool shows up everywhere at once.

---

## What's New — V2: the reflection loop

Earlier milestones (Synthesizer, repository context, plan-time retry,
duplicate-write detection, the inference mutex, checkpoints) are unchanged
and still active; see [`CHANGELOG.md`](CHANGELOG.md) for that history. V2
adds the pieces that turn "the tools ran" into "the task is verified done":

- **VerificationEngine wired into every surface** — after approval, the
  planned files are checked against `git status`, and the tests selected by
  impact analysis actually run. Previously this engine existed but no
  execution path constructed one, so it never ran in production.
- **ReflectionEngine judges completion from real evidence** — a dedicated
  LLM call receives the verification result (test pass/fail counts,
  unexpected file changes) and returns a structured verdict: `complete`,
  `retry`, `replan`, or `blocked`, with a confidence score and, when
  incomplete, the specific missing requirements.
- **REFLECT → REPLAN is a real edge** — a `replan` verdict now drives an
  actual second planning pass with the reflection's stated gap fed back in,
  bounded by the same replan budget and cancellation checks as any other
  recovery path. It does not loop forever: budget exhaustion or repeated
  failure on the same step halts the run rather than retrying blindly.
- **Terminal CLI** (`src/cli/`) — a third client alongside the web UI and
  the extension, showing every stage (plan → stage → diff → approve →
  verify → reflect) in the shell, with `--json` for CI and `--yes` for
  headless runs that still route through the approval policy rather than
  bypassing it.
- **Role-based model routing** (`src/llm/router.py`) — separate model
  tiers for planning, chat, editing, condensation, reflection, and
  (new) autocomplete and vision, selectable per-role or via one
  `PEARL_MODEL_PROFILE` setting. See [Model Routing](#model-routing).
- **Inline completion** — ghost-text suggestions as you type in VS Code,
  served by a small local model with no API key. See
  [Inline Completion](#inline-completion).
- **Multi-language repository parsing** — Python (AST-based), plus
  regex-based parsers for JavaScript, TypeScript, Go, Rust, and Java, all
  registered by default and degrading gracefully (never raising) on
  malformed input.
- **Persistent sessions** — conversations survive a server restart
  (`SessionManager`, `src/agent/session_manager.py`), with the web UI
  synced against the same `/api/sessions` the API exposes.
- **ContextEngine** — every planning/chat call goes through a single
  token-budgeted context pipeline (history, condensed history, repository
  context) rather than each call site trimming ad hoc.

---

## Features

- **The full reflection loop** — plan, execute, stage, approve, verify
  (real tests), reflect (LLM judgement from that evidence), and replan on
  a genuine gap rather than declaring success because tools didn't error.
- **Patch-preview approval** — every file write is staged as a diff first;
  nothing touches disk until a human approves it, on every surface.
- **Three interchangeable surfaces** — terminal CLI, standalone web UI,
  and VS Code extension, all driving the same backend and executor.
- **Repository intelligence** — a cached, incremental index of every
  file's symbols and imports across Python, JavaScript, TypeScript, Go,
  Rust, and Java, used for symbol lookup, reference search, and context.
- **Symbol-aware editing** — replace or insert a function/class/method by
  name via AST (Python) or regex-based parsing (other languages),
  preserving surrounding formatting exactly.
- **Search-replace editing with confidence guards** — exact, then
  whitespace-normalised, then fuzzy matching; below a similarity
  threshold the edit fails rather than silently touching the wrong region.
- **Role-based model routing** — planning, chat, edit, condensation,
  reflection, autocomplete, and vision can each use a different provider
  and model, configured per-role or via one profile setting.
- **Context management** — a single token-budgeted pipeline (history,
  condensed history, repository context) feeds every planning/chat call.
- **Persistent sessions** — conversations survive a server restart.
- **Workspace memory** — a session-scoped record of what Pearl has read,
  edited, and learned, fed back into future planning.
- **Git tools** — status, diff, log, branch, commit, and restore, exposed
  as regular tools the planner can call. Pearl never auto-commits.
- **Zero-config local inference** — `llama-cpp-python` downloads and runs
  Qwen2.5-1.5B-Instruct Q4\_K\_M on first use; no API key required.
- **Any OpenAI-compatible provider** — OpenAI, OpenRouter, or any custom
  endpoint; optional Anthropic and Gemini providers.
- **MCP server** — the same tool registry, planner, and memory the CLI
  uses, exposed over stdio JSON-RPC for any MCP client.
- **Headless / CI mode** — a configurable auto-approval policy for safe
  operations, with dangerous operations always blocked regardless of mode.

---

## Architecture

```
             User
              │
    ┌─────────┼──────────────────┐
    │         │                  │
 Terminal   Browser         VS Code Extension
(src/cli)  (pearl_ui/)      (chat webview, patch approval UI)
    │         │  HTTP+SSE        │  MCP (JSON-RPC 2.0 / stdio)
    │         ▼                  ▼
    │    FastAPI Server     MCP Server (src/mcp)
    │    (src/api)               │
    └────────────┬────────────────┘
                  ▼
           ContextEngine  ──▶ Memory + Condenser + RepositoryService
                  │
                  ▼
               Planner  ──uses──▶ SemanticContextBuilder ──▶ RepositoryIndex
                  │
                  ▼
         AutonomousExecutor
          ├── plan_with_retry()        retry a bad plan once
          ├── validate_plan()          read-before-write, no duplicate writes
          ├── ApprovalCoordinator      PatchManager + CommandApprovalManager
          ├── CheckpointCoordinator    snapshot before every write batch
          │
          ▼
         Tool Dispatcher  (src/agent/dispatcher.py)
          │
          ▼
         PatchManager  ──▶ pause for approval ──▶ apply to disk ──▶ Git
          │
          ▼
         VerificationEngine   git status + impact-selected tests
          │
          ▼
         ReflectionEngine   complete | retry | replan | blocked
          │              │
          │  replan ─────┘ (back to Planner, bounded by max_replans)
          ▼
         Synthesizer   final_answer (Markdown), grounded in verification
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

### Option A — Terminal CLI (fastest way to see the full loop)

```bash
cd /path/to/your/project
python -m src.cli
```

Interactive session in the current directory: type a request, watch it
plan → execute → stage a diff → wait for your `y`/`N` → verify → reflect.

```bash
python -m src.cli "add input validation to the login form"   # one-shot
python -m src.cli --chat "what does this repo do?"            # chat, no tools
python -m src.cli --json "run the tests" --yes                # CI-friendly
```

`--yes` does **not** bypass approval — it routes the decision through the
same headless execution policy described in [Headless / CI Mode](#headless--ci-mode),
which still refuses dangerous operations. There is no flag that skips
approval entirely.

### Option B — Standalone Web UI

```bash
# Start the web server (from the Pearl-AI directory):
uvicorn src.api.server:app --reload

# Open http://localhost:8000 in your browser.
# Pearl downloads the local model (~1 GB) on the first request.
```

### Option C — VS Code Extension

See [VS Code Extension](#vs-code-extension) below.

### Option D — Legacy REPL

```bash
pearl
```

The console script installed by `pip install -e .`. Runs the older
`PearlAgent` loop (no verification/reflection) — kept for backward
compatibility; the CLI above is the recommended terminal client.

---

## Local Inference (Default)

Pearl's default provider is `pearl` — with no `PEARL_INFERENCE_API_KEY` set,
this runs **Qwen2.5-1.5B-Instruct Q4\_K\_M** on your CPU via
`llama-cpp-python`, entirely on-device.

| Property | Value |
|---|---|
| Model | Qwen2.5-1.5B-Instruct Q4\_K\_M |
| Size | ~1 065 MB (downloaded once to `~/.pearl/models/`) |
| Speed | ~16 tok/s on a mid-range laptop CPU (AVX2) |
| License | Apache 2.0 |
| Context | 8 192 tokens |
| API key | none required |

Override the model via `.env`:

```bash
LOCAL_MODEL_REPO=Qwen/Qwen2.5-1.5B-Instruct-GGUF
LOCAL_MODEL_FILE=qwen2.5-1.5b-instruct-q4_k_m.gguf
```

Up to `PEARL_LOCAL_MAX_LOADED_MODELS` (default 2) distinct local GGUFs can
be resident at once — e.g. the 1.5B for planning alongside the smaller
0.5B for autocomplete — each with its own inference lock, so a fast
autocomplete request is never queued behind a slow planning call on a
different model.

---

## Model Routing

Different tasks want different models: planning benefits from a strong
model, autocomplete needs to answer in milliseconds, and chat/condensation
are cheap enough to run locally even when planning goes remote. Pearl
routes each of these independently rather than using one model for
everything.

Set one profile and every role follows it:

```bash
PEARL_MODEL_PROFILE=auto     # default: local with no key, hybrid with one
PEARL_MODEL_PROFILE=local    # everything on-device — no key, no network
PEARL_MODEL_PROFILE=hybrid   # planning/reflection/edit remote, rest local
PEARL_MODEL_PROFILE=cloud    # everything remote except autocomplete
```

Autocomplete deliberately never follows a `cloud`/`hybrid` profile into a
network call — the latency budget for inline completion rules that out —
and vision has **no local fallback**: no bundled model is multimodal, so
an unconfigured vision role returns nothing rather than describing an
image it never saw.

Any role can be pinned individually, which always overrides the profile:

```bash
PEARL_PLANNING_PROVIDER=anthropic
PEARL_PLANNING_MODEL=claude-sonnet-4-6
PEARL_CHAT_PROVIDER=pearl               # stays local even under `cloud`
```

If a remote provider is unreachable, Pearl falls back to the local model
rather than failing the request (`PEARL_MODEL_FALLBACK_TO_LOCAL=true`,
the default) — the zero-config guarantee holds even when a cloud provider
is down.

---

## Inline Completion

Ghost-text suggestions as you type in VS Code, on by default and served
entirely by a local model — no API key, nothing leaving your machine.

```jsonc
// VS Code settings
"pearl.inlineCompletion.enabled": true   // default
```

It uses the smallest local model (Qwen2.5-0.5B by default) rather than
the planning model, because a suggestion that arrives after you have
typed past it is worse than none. Three things keep it out of the way:

- **Debounced** (150 ms) — a burst of keystrokes costs one request.
- **Cancelled on the next keystroke** — an in-flight suggestion computed
  for a cursor position you have already left is discarded, not rendered.
- **Locally gated** — no request at all on an empty line, mid-word (where
  VS Code's own completion applies), or inside a comment.

Completions are cached per cursor context, so re-visiting the same
position is instant rather than a fresh inference. Failures are always
silent: an unavailable model shows no suggestion, never an error.

Expect roughly **0.5 s** per suggestion on a mid-range laptop CPU after
the model has loaded (the first one also pays a one-off load). That is
usable but noticeably slower than a hosted completion model — it is the
cost of the suggestion never leaving your machine.

### Trying it without VS Code

The standalone web UI has a **Code** tab (next to Agent / Chat) with a
scratch editor wired to the same completion service — the quickest way
to see it working:

```bash
uvicorn src.api.server:app --reload
```

Open <http://localhost:8000>, click **Code**, and start typing. Same
behaviour as the editor: pause to get a suggestion, <kbd>Tab</kbd> to
accept, <kbd>Esc</kbd> to dismiss.

Two other surfaces expose the same service: `POST /api/complete` for
scripting, and the `pearl/complete` MCP method for any MCP client.

**Not implemented: fill-in-the-middle.** The bundled Qwen2.5-*-Instruct
models are not FIM-tuned, so text *after* the cursor cannot be used as
true FIM context — it is used for overlap trimming and stop-sequence
derivation only. A Coder-tuned model would allow proper FIM prompting.

---

## Configuration

Pearl reads a `.env` file from its own repository root.

| Variable | Default | Purpose |
|---|---|---|
| `PEARL_LLM_PROVIDER` | `pearl` | One of `pearl`, `openai`, `openrouter`, `custom`, `claude`/`anthropic`, `gemini`. |
| `PEARL_MODEL_PROFILE` | `auto` | `auto`, `local`, `hybrid`, or `cloud` — see [Model Routing](#model-routing). |
| `LOCAL_MODEL_REPO` | `Qwen/Qwen2.5-1.5B-Instruct-GGUF` | HuggingFace repo for the local GGUF. |
| `LOCAL_MODEL_FILE` | `qwen2.5-1.5b-instruct-q4_k_m.gguf` | GGUF filename inside the repo. |
| `LOCAL_MODEL_CTX` | `8192` | Local model context window (tokens). |
| `PEARL_AUTOCOMPLETE_LOCAL_MODEL_FILE` | `qwen2.5-0.5b-instruct-q4_k_m.gguf` | Small local GGUF used for the autocomplete role. |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | — / `gpt-4o-mini` | Used when the provider resolves to `openai`. |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` | — / `openrouter/auto` | Used when the provider resolves to `openrouter`. |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | — / `claude-sonnet-4-5` | Used when the provider resolves to `claude`/`anthropic`. |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | — / `gemini-2.5-flash` | Used when the provider resolves to `gemini`. |
| `PEARL_INFERENCE_API_KEY` / `PEARL_INFERENCE_BASE_URL` | — | Pearl-hosted gateway; when set, `pearl` runs remotely instead of on-device. |
| `PEARL_EXECUTION_MODE` | `interactive` | `interactive`, `headless`, or `ci` — see [Headless / CI Mode](#headless--ci-mode). |

Per-role overrides (`PEARL_PLANNING_PROVIDER`, `PEARL_CHAT_MODEL`,
`PEARL_EDIT_PROVIDER`, `PEARL_CONDENSER_MODEL`, `PEARL_REFLECTION_PROVIDER`,
`PEARL_AUTOCOMPLETE_PROVIDER`, `PEARL_VISION_PROVIDER`, …) are documented
in [`src/config/settings.py`](src/config/settings.py), which is the source
of truth for every setting.

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

- **Inline ghost-text completions** as you type, from a local model —
  see [Inline Completion](#inline-completion).
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
| `pearl/runAutonomous` | Runs the loop up to the next pause point (plan → execute → stage, or completion). |
| `pearl/approvePatches` / `pearl/rejectPatches` | Resumes a paused run after a human decision; approval's response includes `verification` and `reflection` when they ran. |
| `pearl/chat` | Sends a message straight to the LLM (no planning/tools). |
| `pearl/complete` | Returns one inline completion for a cursor position. Never errors for a model failure — returns an empty completion with a reason. |
| `pearl/memory` | Returns the server's current `WorkspaceMemory` contents. |
| `pearl/checkpointCreate` / `pearl/checkpoints` / … | Checkpoint system — see [`docs/checkpoints.md`](docs/checkpoints.md). |
| `shutdown` / `exit` | Graceful shutdown. |

---

## Autonomous Execution

`AutonomousExecutor` (`src/agent/executor.py`) drives a full run:

1. **Plan** — `Planner` produces a structured tool sequence grounded in
   semantic workspace context, drawn through `ContextEngine`. If the first
   plan fails to parse, it retries once with the error fed back.
2. **Validate** — `validate_plan()` checks step count, forbidden paths,
   extension hallucination, read-before-write, and duplicate writes.
3. **Execute** — each step runs through the dispatcher. Failed steps are
   recorded; if the same tool+args fails twice, execution aborts rather
   than looping.
4. **Replan** — on recoverable failure, a replan is attempted (bounded by
   `max_replans`). The failed step and its error are fed back into the
   next planning prompt.
5. **Approve** — any step that would write a file, or run a shell command,
   pauses the run for human approval via `PatchManager` /
   `CommandApprovalManager`. No write reaches disk and no command runs
   without it. A checkpoint is taken immediately before the write batch
   lands, so it can always be undone.
6. **Verify** — after approval, `VerificationEngine` checks the applied
   files against `git status` and runs the tests selected by impact
   analysis. If tests fail, one more replan is attempted with the failure
   fed back before reflection runs.
7. **Reflect** — `ReflectionEngine` receives the verification evidence
   (not just "the tools ran") and returns a structured verdict — `complete`,
   `retry`, `replan`, or `blocked` — with a confidence score and, when
   incomplete, the specific missing requirements.
8. **Replan from reflection** — a `replan` verdict drives one more planning
   pass with the stated gap fed back in, still bounded by `max_replans` and
   cancellation. `complete`/`blocked`/`retry` end the loop.
9. **Synthesize** — the `Synthesizer` makes one final LLM call, grounded in
   tool results (succeeded and failed) and the verification data, producing
   a `final_answer` in Markdown.

A tool call finishing without an exception is step 3. Whether the task is
actually done is decided in steps 6–8, from evidence — never assumed from
step 3 alone.

---

## Headless / CI Mode

`PEARL_EXECUTION_MODE` controls what may auto-approve without a human
present:

| Mode | Safe (read-only) tools | Staged (write) tools | Dangerous tools |
|---|---|---|---|
| `interactive` (default) | approval required | approval required | approval required |
| `headless` | auto-approved | follows `PEARL_HEADLESS_STAGED_POLICY` (`approve` or `block`, default `block`) | **always** requires approval |
| `ci` | auto-approved | blocked by default | **always** requires approval |

Dangerous operations are never auto-approved in any mode — that decision
is made by `ExecutionPolicy` (`src/agent/headless.py`), not by any
per-surface flag. The CLI's `--yes` and any equivalent option elsewhere
route through this same policy rather than defining their own bypass.

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

These numbers predate V2's verification and reflection additions and have
not been re-run through the formal suite since. As an approximate,
informally measured data point on the same 1.5B local model: a full
`plan → stage → approve → verify → reflect` cycle for a one-file change
took ~35s to plan and ~11s for approve+verify+reflect. Verification and
reflection add real latency in exchange for the correctness guarantee —
that trade is the point of V2, but it means the table above understates
current end-to-end time for an approved run.

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

**Pearl calls the wrong model, or one that doesn't exist.**
Check that `.env` sits at Pearl's own repository root — settings are
resolved relative to `settings.py`'s location, independent of the
workspace `cwd` Pearl was launched from. Also check `PEARL_MODEL_PROFILE`:
under `hybrid`, planning/reflection/edit follow `PEARL_LLM_PROVIDER` while
chat/condenser stay local — a role behaving differently than expected is
usually the profile routing it on purpose. `GET /api/provider` (web UI)
reports what each role actually resolves to.

**A patch never appears for approval.**
Only the dedicated edit tools stage patches; a step that uses
`execute_shell` to write a file bypasses `PatchManager` by design —
shell commands are approved separately via `CommandApprovalManager`, not
diffed.

**A run keeps replanning and never finishes.**
Bounded by `max_replans` (executor) and `Settings.REFLECTION_MAX_ITERATIONS`
(reflection) — a run that hits either budget stops rather than looping
forever. If it stops before the task is actually done, check the
`reflection.missing_requirements` in the result for what it judged was
still missing.

---

## FAQ

**Does Pearl require an API key?**
No — the default provider (`pearl`, with no `PEARL_INFERENCE_API_KEY` set)
runs fully locally with no external calls.

**Can Pearl modify files without asking?**
No. Every file-writing tool stages a diff via `PatchManager` and the run
pauses until it's approved or rejected — this is not configurable per-tool
by design, on any of the three surfaces.

**If a tool call succeeds, is the task done?**
Not necessarily, and Pearl doesn't report it that way. A tool returning
without error only means that one step ran; `VerificationEngine` then
checks the actual file changes and test results, and `ReflectionEngine`
judges completion from that evidence — a run can execute every step
successfully and still be reported `blocked` if the tests it triggered
fail or an unrelated file changed.

**Does Pearl index non-Python files?**
Yes — Python (AST-based), plus regex-based parsers for JavaScript,
TypeScript, Go, Rust, and Java, all registered by default. A parser never
raises on malformed input; it returns whatever it could extract.

**Can I use a hosted model instead of the local one?**
Yes — set `PEARL_LLM_PROVIDER` to `openai`, `openrouter`, `claude`/
`anthropic`, or `gemini`, or set `PEARL_MODEL_PROFILE=hybrid`/`cloud` to
mix remote reasoning with local speed. See [Model Routing](#model-routing).

**How much RAM does the local model use?**
Qwen2.5-1.5B Q4\_K\_M uses approximately 1.2 GB of RAM at runtime. If a
second local model is also resident (e.g. the 0.5B for autocomplete),
budget for both — `PEARL_LOCAL_MAX_LOADED_MODELS` bounds how many can
load at once.

---

## Contributing

```bash
pip install -e ".[dev]"
pytest -q                                  # Python test suite (fast; excludes real-model tests)
pytest -m real_model -q                    # real local-model E2E — slow, no mocks, no network calls out
pytest -q tests/test_e2e_mcp.py            # MCP end-to-end (real subprocess)
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
