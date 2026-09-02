# Open-Source Architecture Review

Pearl vs. Cline, OpenHands, Aider — capability comparison and implementation plan.

**Licenses:** Cline (Apache 2.0) · OpenHands (MIT) · Aider (Apache 2.0)  
All three are open-source; implementation ideas are adoptable with attribution where required.

---

## Capability Comparison

| Capability | Cline | OpenHands | Aider | Pearl | Best approach |
|---|---|---|---|---|---|
| **Agent loop** | Session state machine; hooks around every model + tool call | Event-stream / Controller; Observation→Action cycle | Simple REPL loop; reflection cycles on test failure | `AutonomousExecutor` + plan-then-execute; replan on step failure | Pearl's replan loop is more structured; add Aider-style test-failure reflection |
| **Context / repo intelligence** | Manual file add; no built-in repo map | Browser + code agent; file tree access | **Repo map + PageRank** — tree-sitter tags, graph centrality, token-bounded binary search | `SemanticContextBuilder` — term match + IMPORTS-graph expansion + precise symbol extraction | Aider's PageRank ranking; Pearl's graph expansion. Combine: rank candidates by centrality before including |
| **Token budget** | No explicit enforcement | Summarization per turn | Binary search to fit token budget | Specified but **not implemented** (`TokenBudgetManager` absent) | Aider's binary-search approach; implement cap at 80% of model context window |
| **Tool system** | 6 built-ins + custom tools via `createTool()` | Code/Browse/File/IPython agents; CodeAct (LLM writes executable code) | Tools implicit in edit-format parser (no explicit tool dispatch) | `@tool` decorator + `ToolRegistry` + `ToolDispatcher`; 20+ registered tools | Pearl's explicit registry is more auditable; keep it |
| **Approval flow** | Per-tool policy: auto-approve reads, gate writes/exec | All mutations in sandboxed container (implicit approval) | No approval gate; auto-commits every edit | **All** autonomous writes gated through `PatchManager`; commands through `CommandApprovalManager` | Cline's per-tool policy: auto-approve reads in autonomous mode, keep write gate |
| **Plan validation** | None — model correctness trusted | None | None | **Deterministic `PlanValidator`** — forbidden paths, read-before-write, unknown tools, duplicate writes, dependency cycles | Pearl's validator is unique and valuable; keep and extend |
| **Error recovery** | Hook-driven; session abort on unrecoverable error | Retry with observation feedback | Reflection loop: failure → new prompt, capped at `max_reflections=3` | Replan on step failure (up to `max_replans=3`); plan retry on malformed JSON | Combine: Pearl's replan for tool failures + Aider's reflection for test failures |
| **Git integration** | Via `bash` tool (not first-class) | Via shell in sandbox | Auto-commit every edit; dirty-commit detection; `/undo` via git revert | First-class `git_tools.py`: status, diff, log, commit, restore, branch; `CheckpointManager` snapshots before apply | Pearl already leads; add Aider's dirty-commit detection before any edit |
| **Model abstraction** | Provider-agnostic; auto/local/hub/remote backend | Multi-provider; model per agent role | `Coder.create()` factory per edit-format; weak model for cheap tasks | `ModelRouter` — local (llama-cpp), OpenAI, Anthropic, OpenRouter, Gemini, custom | Add weak-model concept: use cheap/fast model for synthesis, commit messages |
| **Local / no-key inference** | Depends on backend mode | Depends on config | Depends on provider | **Default**: Qwen2.5-1.5B-Instruct Q4_K_M via llama-cpp-python, no API key | Pearl leads; no change needed |
| **Web search** | `fetch_web` tool | Browser agent | Not built-in | DuckDuckGo (no key); `WebService` with ranking and extraction | Pearl has it; improve citation/source formatting in Synthesizer output |
| **Test runner** | Via `bash` tool | Via shell in sandbox | Auto-test with `auto_test=True`; failure feeds reflection loop | `run_tests()` in `shell_tools.py`; `VerificationEngine` selects + runs targeted tests | Wire `VerificationEngine` failures back into executor replan loop |
| **Verification** | None | None | Linter + test (auto_lint, auto_test) | `VerificationEngine`: git diff, unexpected file detection, targeted pytest, risk/confidence scoring | Pearl leads significantly; no equivalent in Cline or OpenHands |
| **Context compression** | Not built-in | **Condenser**: emits `CondensationSummaryEvent` near token limit | Not built-in | Token budget truncation (`src/llm/token_budget.py`) | OpenHands' structured summary is better; Pearl's truncation is the safe minimum |
| **Memory across turns** | Session snapshot (`snapshot()`/`restore()`) | **Append-only event log** — zero mutable agent state, all state in stream | Conversation history in git commits | `WorkspaceMemory` + `MemoryManager` + conversation context in `PearlSession` | OpenHands' event-log design is elegant; Pearl's in-memory approach is adequate for local single-user use |
| **Sandboxing** | Allowlist + approval gate | **Docker container** (process isolation) | None | Allowlist + denylist + workspace boundary enforcement | OpenHands' container approach is correct for production; Pearl's allowlist is pragmatic for local use |
| **UI / streaming** | Rich TypeScript extension with diff viewer | Web UI with real-time event stream | Terminal with color output | FastAPI SSE + minimal HTML UI | Add markdown rendering + syntax highlighting to `pearl_ui/index.html` |
| **Multi-workspace** | Per-session isolation (extension manages) | Per-session container | Single workspace per process | Single workspace per `PearlSession`; remaining `os.chdir()` race in `session.py` | Thread workspace_root through all tools (replace `Path.cwd()` usage) |

---

## Pearl's Differentiated Strengths

These exist only in Pearl — worth preserving and deepening:

1. **Deterministic plan validation** — no other agent validates the plan before execution
2. **Atomic patch apply with rollback** — snapshot → write → rollback on failure; others write immediately
3. **Staged approval with resume** — approve/reject/resume without re-planning or re-running completed steps
4. **Evidence-based reflection** — `_reflect()` derives COMPLETE/PARTIAL/FAILED from observable evidence, not LLM self-assessment
5. **Confidence scoring from observables** — read coverage, symbol existence, assumption verification; not LLM-generated
6. **VerificationEngine** — git diff + unexpected file detection + targeted pytest + risk scoring after every apply
7. **Local-first** — works offline with Qwen2.5-1.5B, no API key required

---

## Implementation Plan: Gaps to Close

Ordered by impact-to-effort ratio.

### P1 — Token budget enforcement (high impact, low effort)

`TokenBudgetManager` is referenced in CLAUDE.md but doesn't exist. Without it, large repos can overflow the model context window and crash.

- Add `src/llm/token_budget.py` — estimate token count, truncate at 80% of model's context window
- Integrate into `SemanticContextBuilder.build()` and `Planner.plan()`

### P2 — Reflection loop on test failure (high impact, medium effort)

`VerificationEngine` runs tests after apply and produces structured results, but the failures are never fed back to the executor for replanning. Aider's reflection loop is the right pattern.

- After `approve()` in `executor.py`, if `VerificationEngine.verify()` returns `FAILED`, feed the test output back as a new replan with `failed={tool: "test_runner", error: <output>}`
- Cap at `max_replans` (already enforced)

### P3 — Dirty-commit detection before edits (medium impact, low effort)

Before staging any edit in `AutonomousExecutor`, check for uncommitted workspace changes. If any exist, either refuse or auto-commit them (user-configurable). Aider does this; it prevents losing in-progress work.

- Add `_check_dirty_workspace()` in `executor.py` called at the start of `run()`
- Emit a warning progress event; don't block by default

### P4 — Per-tool approval policy (medium impact, medium effort)

Currently ALL tool calls in autonomous mode are gated. Read tools (`read_file`, `search_code`, `git_status`, `list_files`, etc.) are safe to auto-approve — they produce no side effects. Gating them creates friction without adding safety.

- Add `SAFE_READ_TOOLS = frozenset({...})` in `executor.py`
- `PatchManager` / `CommandApprovalManager` only activates for write/exec tools

### P5 — PageRank context ranking (high impact, high effort)

Aider's PageRank personalisation (files in active chat get 50× weight, mentioned identifiers get 10×) is the most impactful context-management idea in the field. Pearl's `SemanticContextBuilder` uses term matching + graph expansion but no centrality scoring.

- Add `RepositoryGraph.pagerank(personalization: dict[str, float])` using NetworkX (already installed)
- Integrate into `SemanticContextBuilder.build()` as a ranking step after candidate selection

### P6 — Workspace-root propagation (medium impact, high effort)

The remaining `os.chdir()` in `session.py:run_autonomous_stream()` is a race condition for concurrent sessions. Fixing it properly requires threading `workspace_root: Path` through every tool function that currently calls `Path.cwd()`.

- Add `workspace_root` parameter to `_ensure_within_workspace()`, `_workspace_cwd()`, and all callers
- `set_workspace_root()` / `get_workspace_root()` context-var pattern (mirrors `_active_patch_manager`)

### P7 — Markdown + syntax highlighting in UI (low impact, low effort)

`pearl_ui/index.html` renders plain text. Chat messages with code blocks, diffs, and lists should render as formatted Markdown.

- Add `marked.js` (CDN) for Markdown parsing
- Add `highlight.js` (CDN) for code syntax highlighting
- Sanitize with DOMPurify before inserting into DOM

---

## What NOT to Adopt

- **Ollama** — user prohibited; Pearl uses llama-cpp-python natively
- **Docker sandbox** — correct for production, out of scope for local-first tool
- **CodeAct** (OpenHands) — LLM writing executable Python as the action mechanism; Pearl's explicit tool registry is more auditable and safer
- **Aider's auto-commit** — Pearl's staged approval is more conservative and correct for a developer tool; auto-commit removes the human gate
