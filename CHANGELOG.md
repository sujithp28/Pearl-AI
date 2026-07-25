# Changelog

All notable changes to Pearl are documented here. Dates are the date the
work landed in this repository.

## [Unreleased]

### Sprint 1 — Checkpoint System

Complete checkpoint feature: create, restore, delete, rename, list,
metadata, persistence, auto cleanup, recovery, CLI, MCP, and VS Code
integration, plus timeline integration for the automatic checkpoint
already taken before every approved write.

- **Storage moved outside the workspace (ADR-005).** Checkpoints used
  to live at `<workspace>/.pearl/`, which required also editing the
  user's `.gitignore` to compensate — itself a sign the design was
  wrong. Now stored at `~/.pearl/workspaces/<key>/`, keyed by a hash
  of the workspace's absolute path; Pearl never writes into a user's
  project directory or `.gitignore` again. A legacy in-workspace store
  is migrated automatically, once, on first use.
- **Delete and rename**, via a small JSON metadata sidecar rather than
  rewriting git history: a git commit is treated as permanent content;
  the metadata layer is the mutable label/deleted-flag presentation on
  top of it. Deleting the middle of a linear commit history would mean
  rewriting every commit after it — real corruption risk for a
  safety-net feature, for no benefit on content this small.
- **Auto cleanup (retention)**: `PEARL_CHECKPOINT_MAX_COUNT` (default
  50) and `PEARL_CHECKPOINT_MAX_AGE_DAYS` (default 30), both 0 to
  disable. Applied after every `create()`; never prunes the checkpoint
  that was just created; a retention failure never fails the
  checkpoint that triggered it.
- **Recovery**: a stale `index.lock` (left by a killed process)
  surfaces a specific, actionable error instead of an opaque one. A
  missing or corrupted metadata file self-heals from `git log` rather
  than being treated as fatal. Metadata writes are atomic
  (write-temp-then-rename).
- **CLI**: `:checkpoints`, `:checkpoint [label]`, `:restore <id>`,
  `:delete <id>`, `:rename <id> <label>` in the REPL, with a
  preview-then-confirm step before restoring (restoring can delete
  files created since the checkpoint).
- **MCP**: six new methods — `pearl/checkpointCreate`,
  `pearl/checkpoints`, `pearl/checkpointRestorePreview`,
  `pearl/checkpointRestore`, `pearl/checkpointDelete`,
  `pearl/checkpointRename` — plus a `pearlCheckpoints` capability
  advertised in `initialize`. `MCPServer`/`PearlAgent` now hold one
  shared `CheckpointManager`, so a manual checkpoint and the automatic
  pre-write checkpoint appear in the same list — verified live against
  a real server that both actually land in one store.
- **VS Code**: a "Checkpoints" view in Pearl's activity bar
  (`checkpointClient.ts`/`checkpointTree*.ts`/`checkpointCommands.ts`,
  mirroring the existing Memory panel's structure) — create, refresh,
  and per-item restore (with a modal confirmation listing exactly what
  will change)/rename/delete.
- **Timeline integration**: a new `EventKind.CHECKPOINT` /
  `checkpoint_created` progress event fires when the automatic
  pre-write checkpoint actually captures something — not when there
  was nothing new to record (workspace already matched the last
  checkpoint), and not on a swallowed failure.
- **Two real bugs found by testing the mechanics directly, not by
  reasoning about them:**
  - `CheckpointManager`'s default workspace argument resolved via
    `Path(".").resolve()`, which reads the real process `os.getcwd()`
    and silently ignores `monkeypatch.setattr(Path, "cwd", ...)` — a
    pattern used throughout this codebase's own tests. A
    `CheckpointManager()` built with no explicit workspace could end
    up pointed at a completely different directory than the one a
    caller (or a test) believed it was operating on; in one observed
    case, the real Pearl repository itself. Fixed to resolve via an
    explicit `Path.cwd()` call, matching
    `file_tools._ensure_within_workspace` and
    `Planner._workspace_root()`.
  - The store-exclusion pattern was root-anchored to the *legacy*
    `.pearl` name, so it only protected the case where the store sat
    exactly at the workspace root. A workspace that happens to be an
    *ancestor* of `~/.pearl` (e.g. Pearl pointed at `~` itself) puts
    the live external store inside the very tree being checkpointed,
    and its own git internals get swept into checkpoints and then
    "restored" as workspace files. Fixed by excluding the store's
    actual resolved location, by its real path relative to the
    workspace, whenever it is nested inside the workspace at all.
- Tests never touch a real developer's home directory: a new
  session-wide `tests/conftest.py` fixture redirects `$HOME` (which
  also correctly propagates into the real-subprocess e2e tests, unlike
  a `Path.home` monkeypatch) — added after a full suite run left real
  checkpoint stores under a real `~/.pearl/workspaces/` during this
  sprint's own development.
- 694 Python tests (up from 644 at the end of Sprint 0), 239
  TypeScript (up from 210), lint/format/pyflakes clean, real MCP
  server verified live end to end (create/list/restore/rename/delete,
  and manual + automatic checkpoints sharing one store).

### Sprint 0 — Engineering Cleanup

Cleanup pass ahead of feature work, per Pearl's frozen architecture
(the ADR series recorded in this project's design discussion, not
yet a committed document). Highlights:

- **Removed a confirmed safety bug: three live code paths wrote files
  with zero approval.** `PearlAgent.run()`, `PearlAgent.plan_and_run()`,
  and the MCP method `pearl/plan` all dispatched planned tool calls
  directly via `Planner.run()`/`ToolDispatcher.execute()`, completely
  outside `AutonomousExecutor` — so no `PatchManager` was ever active,
  and every write tool (`create_file`, etc.) fell through to writing
  straight to disk. Verified live: calling the dispatcher the same way
  these paths did wrote a file with no review at all. This directly
  contradicted Pearl's core "approval is the one chokepoint" principle.
  All three, plus `Planner.run()` and `LLMToolSelector` (only used by
  `PearlAgent.run()`), are removed. `pearl/plan` was never called by
  the VS Code extension — only Pearl's own tests used it.
- **`PearlAgent` gained `approve()`/`reject()`**, making
  `run_autonomous()` a complete, usable session on its own (previously
  only `MCPServer` could resolve a paused run). Steps are recorded into
  `Memory` exactly once across a pause/resume, and a task paused for
  approval is no longer incorrectly marked `failed` in the meantime
  (a real, if minor, pre-existing bug fixed as a side effect of this
  work).
- **The CLI (`src/main.py`) now uses `run_autonomous()`** with an
  approve/reject prompt when a run pauses, instead of the removed
  single-tool `run()`. Verified live end to end, including the
  approve path actually writing a file to disk.
- **Cancellation now reaches the LLM call, not just the gaps between
  steps.** `AutonomousExecutor` already checked cancellation before
  replanning, but had no check before the *initial* plan call, and
  once any LLM call started (including retry backoff, up to ~7s) it
  could not be interrupted. `LLMClient.generate()` gained an optional
  `cancel_check`, and the retry-backoff wait now polls it instead of
  blocking a fixed `time.sleep`. Honestly scoped: this does not abort
  a request already in flight to the provider (only before it's sent,
  and during backoff) — full interruption would need an async/threaded
  rewrite, out of scope for a cleanup pass.
- Removed dead files: `hello.java`, `hello_world.py` (debug artifacts
  from earlier chat-grounding testing).
- Marked `benchmarks/REPORT.md` as superseded rather than deleted or
  silently left stale — it predates the GPU/dependency/prompt changes
  that materially affect its numbers.
- 644 Python tests (net: removed the deprecated-API tests, added
  cancellation/approve/reject/search-cap/emoji-mode coverage),
  lint/format/pyflakes clean.

### Added
- **Live execution progress streaming (Phase 27, M1).** The MCP server
  now pushes `pearl/progress` JSON-RPC notifications (planning,
  executing a step, replanning, awaiting approval, cancelled, ...)
  while `pearl/runAutonomous`/`pearl/approvePatches`/`pearl/rejectPatches`
  are in flight, instead of the client seeing nothing until the whole
  call resolves. Fully additive and backward compatible: the
  request/response contract is unchanged, and every existing
  `handle_request()` call (used throughout the test suite) behaves
  exactly as before by simply omitting the new optional `notify`
  parameter. The VS Code extension renders these live — a spinner,
  step counter, and current action — in the chat panel while an
  autonomous run is executing.
- **Personality engine.** `src/personality/` gives Pearl's own status/
  progress messages a configurable voice (`PERSONALITY=professional|
  friendly|cheeky|savage`, default `cheeky`) and emoji density
  (`EMOJI_MODE=none|minimal|normal|fun`, default `minimal`), with an
  automatic "serious mode" override for security-related messages.
  Wired into `AutonomousExecutor`'s progress narration
  (`ProgressEvent.current_action`) only — a static test asserts the
  package never imports `src.llm`/`src.agent`, and an integration
  test asserts an identical run under two different personalities
  produces identical steps/results/stop-reasons, differing only in
  progress-message wording. See [`docs/personality.md`](docs/personality.md).
- **Conversation history in chat.** `pearl/chat` and
  `PearlAgent.chat()` now send the session's recent turns
  (`PEARL_CHAT_HISTORY_TURNS`, default 10; `0` disables) so replies
  can follow the thread. `LLMClient.generate()` gained an optional
  `history` parameter; omitting it is the previous stateless
  behavior, which is what `generate_json` — and through it all
  planning/replanning — deliberately keeps doing.

- **End-to-end tests against a real subprocess.**
  `tests/test_e2e_mcp.py` spawns a real `python -m src.mcp` process
  and drives it over real stdio with real JSON-RPC bytes — real
  framing, real planner, real tool dispatch, real `PatchManager`
  approval gate, real files on disk. The only faked component is the
  model itself, via a new deterministic `scripted` provider
  (`PEARL_LLM_PROVIDER=scripted`), because model output is genuinely
  non-deterministic and can't be asserted on. Runs in CI as its own
  step with no model server needed. This closes the blind spot that
  let a wrong timeout constant ship past ~780 passing tests: every
  test faked the transport and kept the logic, which is the opposite
  of what was needed.
- **`scripted` LLM provider** for offline/deterministic runs — also
  useful for debugging the MCP protocol or the VS Code extension with
  no model server running. Selectable only by explicit configuration,
  never as a fallback, so a misconfigured real provider can't quietly
  start serving canned answers.
### Removed
- **`python-dotenv`** — replaced by a ~30-line stdlib `.env` loader
  (`Settings.load_env_file`). `openai` is now Pearl's only runtime
  dependency. The replacement preserves the behavior that mattered:
  existing environment variables always win over `.env`, which CI and
  the end-to-end tests rely on to point Pearl at the scripted
  provider without a developer's local `.env` overriding them.
  Verified by uninstalling the package and confirming a clean
  `pip install -e .` still completes a full autonomous run.
- **`pytest-timeout`** — briefly added, then removed. The protection
  it gave (bounding a blocked read on the subprocess pipe) is now a
  stdlib reader thread draining stdout into a `queue.Queue`, which is
  strictly more reliable: `select`-based waiting can't see bytes
  already buffered in Python's text-mode wrapper, and a plugin-level
  timeout can't distinguish a wedged server from a slow one. Verified
  by pointing the harness at a process that starts and never speaks —
  it now fails in exactly the configured window with the server's
  stderr attached.
- **Chat-mode capability grounding** (`src/prompts/system.py`, until
  now an empty and unimported file). `LLMClient.generate()` gained an
  optional `system` parameter; omitting it is the previous behavior,
  which `generate_json` — and through it all planning — keeps doing,
  since planning output is parsed as JSON and must stay unprimed.

### Fixed
- **`ToolDispatcher.has_tool()` raised instead of returning `False`,
  and `ToolDispatcher.list_tools()` raised `AttributeError`.** The
  former checked `get_tool(...) is not None` against a method that
  raises on a miss; the latter called a registry method that does not
  exist. Both had zero callers and zero tests, which is why they
  rotted unnoticed. **Removed rather than repaired** — dispatching is
  not registry inspection, and `ToolRegistry.has_tool()` /
  `.list_tools()` already answer these correctly.
- **`search_text` and `find_references` returned unbounded results.**
  A broad query on a large repository produced megabytes that fed
  straight into the next planning prompt. Capped at 200 matches, with
  an explicit truncation marker so the model is told its view is
  partial rather than assuming it saw everything.
- **Three of the four `EMOJI_MODE` values were identical.**
  `minimal`, `normal` and `fun` produced byte-identical output, making
  three documented settings dead configuration. Each now behaves
  distinctly: `none` (no emoji), `minimal` (outcomes only), `normal`
  (every event), `fun` (adds a celebratory accent, still within the
  two-emoji ceiling).
- **Pearl claimed to have performed actions it never took.** Asked to
  "write hello world and tell me where you store the file", chat mode
  printed code and said it "stores it in the /home/sujith directory";
  nothing had been created. Asked "did u create a file", it deflected.
  The chat model had no idea it was part of a coding agent. It now
  answers "No, I did not create or edit any files in this reply."
  Live testing drove the design: an initial draft phrased as "never
  describe yourself as an AI language model" caused the real 3B model
  to reply "as an AI language model, I don't have a physical
  workspace" — echoing the forbidden phrase and stating something
  false about itself. Rewritten in positive framing.
- **Chat had no memory.** `_chat` recorded both turns into `Memory`
  but never sent them back to the model, so every reply was generated
  as if it were the first message of the conversation — "ok" would be
  answered with "Hello! How can I assist you today?", and a follow-up
  question about what was just said got a generic non-answer. Memory
  was being written and never read.
- **`Memory.recent_conversation(limit=0)` returned the entire
  history** instead of nothing, because `self.conversation[-0:]` is
  `[0:]` in Python. Latent until something passed `0`; now the
  documented "no turns" behavior, with a regression test.
- **The planner was never told the workspace boundary.** It was
  enforced in `file_tools._ensure_within_workspace` but absent from
  `planning.txt`/`replanning.txt`, so the model would plan absolute
  paths like `/tmp/hello.py` that are rejected before they run. Both
  prompts now state the actual workspace root (resolved per call from
  the same `Path.cwd()` the enforcing side reads, so the advertised
  boundary can't drift from the enforced one) and require relative
  paths.

## [1.2.0-beta] — 2026-07-24

Beta hardening pass: no new features — stabilization, performance,
packaging, and documentation ahead of a public beta.

### Fixed
- Removed a dead `import torch` from `src/config/settings.py` (leftover
  from an abandoned local-model design; nothing read the `Settings.DEVICE`
  value it computed) — cut ~7 seconds off every process start.
- Fixed `RepositoryIndex`'s directory walk to prune ignored directories
  (`.venv`, `node_modules`, `.git`, build/cache dirs) *during* the walk
  instead of globbing the entire tree first and filtering after — cold
  indexing of this repository went from ~19.4s to ~0.2s.
- Removed further dead configuration fields (`MODEL_NAME`,
  `MODEL_CACHE_DIR`, `TOP_P`, `MODELS_DIR`, `LOGS_DIR`, `ROOT_DIR`,
  `SRC_DIR`, `PROMPTS_DIR`, `TOOL_SELECTION_PROMPT`, `BANNER`) that had no
  readers anywhere outside `settings.py` itself.
- Added missing `src/__init__.py` and `src/config/__init__.py`, required
  for `pip install -e .` to resolve `src` as an importable package.

### Added
- `pyproject.toml` — proper packaging metadata, optional extras
  (`claude`, `gemini`, `dev`, `all`), and a `pearl` console entry point.
- `requirements.txt` populated (previously empty).
- `LICENSE` (MIT) at the repository root and inside `vscode-extension/`.
- `.github/workflows/python.yml` — runs `pytest`, `ruff check`, and
  `ruff format --check` across Python 3.10–3.12 on every push/PR.
- `.github/workflows/vscode-extension.yml` — runs `tsc` (compile/type
  check) and the extension's test suite on every push/PR touching
  `vscode-extension/`.
- `docs/architecture.md` — full module-by-module architecture reference.
- `[tool.ruff]` linting/formatting configuration; adopted `ruff format`
  across `src/`, `tests/`, and `benchmarks/` as a one-time formatting
  baseline (cosmetic only — verified via the full test suite before and
  after).
- VS Code extension packaging (`npm run package` → `.vsix`) and package
  metadata (license, repository, description, version).

### Changed
- `Settings.VERSION` and the MCP server's `SERVER_VERSION` bumped to
  `1.2.0-beta`.
- README rewritten to reflect the current architecture (autonomous
  execution, patch approval, repository index, context manager,
  workspace memory, MCP, VS Code extension) rather than the early
  scaffold it previously described.

## [1.0.0-beta] — earlier

Prior work, summarized (see `git log` for full history):
- Multi-provider LLM client (Ollama, OpenAI, OpenRouter, custom,
  Anthropic, Gemini) over a shared OpenAI-compatible interface.
- Tool system: registry, dispatcher, `@tool` metadata decorator.
- File, shell, and git tools.
- MCP server exposing the tool registry over stdio JSON-RPC.
- VS Code extension: chat webview, MCP connection, memory explorer,
  plan visualization.
- Repository intelligence (`RepositoryIndex`), AST-based symbol editing
  (`SymbolEditor`).
- Patch preview/approval workflow (`PatchManager`).
- Autonomous execution loop with reflection, replanning, cancellation,
  and progress streaming.
- Workspace memory and session context tracking.
- Smart context manager (`ContextBuilder`) for token-budgeted, ranked
  repository context.
- End-to-end MCP integration and multi-repository benchmarking
  (`benchmarks/run_benchmark.py`, `benchmarks/REPORT.md`), including two
  workspace-path-resolution bug fixes found via that benchmarking
  (`.env` and prompt-template loading were `cwd`-relative instead of
  resolving relative to their own file locations).
