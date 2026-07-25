# Changelog

All notable changes to Pearl are documented here. Dates are the date the
work landed in this repository.

## [Unreleased]

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

### Fixed
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
