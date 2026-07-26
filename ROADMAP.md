# Roadmap

Pearl's development direction. Nothing here is a timeline or a commitment —
see [CHANGELOG.md](CHANGELOG.md) for what has actually shipped.

---

## Milestones

| # | Milestone | Status |
|---|---|---|
| M1 | End-to-End Agent | 🔄 In progress |
| M2 | Planning Engine | ☐ Next |
| M3 | Tool Framework | ☐ Planned |
| M4 | Repository Intelligence | ☐ Planned |
| M5 | Memory | ☐ Planned |
| M6 | Self-hosting (Pearl builds Pearl) | ☐ Planned |

---

## M1 — End-to-End Agent (Current)

**Goal:** Pearl accepts a natural-language request, generates a plan, executes safe
tool calls, requests approval when needed, and produces a summary.

**The reference scenario:**
```
User: "Rename ToolRegistry to ToolCatalog."

  → Planner generates a step-by-step plan
  → Executor dispatches tool calls
  → PatchManager stages the diff
  → Approval prompt shown to user
  → On approval: changes written, checkpoint created
  → Summary: files changed, what was done
```

Until this works end-to-end without manual intervention, nothing else in this list
takes priority.

**Near-term items (v1.2.0-beta):**

- **Token usage tracking** — surface `usage` from LLM responses through to the user
  (cost and context-window visibility per run)
- **TypeScript linting** — add ESLint/Prettier to `vscode-extension/`; CI currently
  runs only `tsc` for the extension side
- **Non-Python symbol intelligence** — `RepositoryIndex` and `SymbolEditor` are
  Python-only today; other languages fall back to text search with no symbol awareness
- **Cross-platform install verification** — only exercised on Linux/WSL so far;
  needs validation on native Windows and macOS

---

## M2 — Planning Engine

**Goal:** Pearl's plans are reliable, validated, and self-correcting.

- Task decomposition into ordered, dependency-annotated steps
- Deterministic plan validation (structure, scope, safety) before execution
- Confidence scoring per step — computed from observable factors, not LLM-generated
- Retry and recovery: transient errors retried, plan failures replanned (max 3)
- Reflection phase: COMPLETE / PARTIAL / FAILED based on evidence, not step count

---

## M3 — Tool Framework

**Goal:** Tools are discoverable, permission-controlled, and robustly validated.

- Complete tool metadata and schema via `@tool` decorator
- Permission model: which tools require approval, which are read-only safe
- Input validation at the tool boundary, not upstream
- Async execution with concurrency limits and timeouts
- Batch tool variants (`read_files`, `search_multiple`) to avoid N+1 patterns

---

## M4 — Repository Intelligence

**Goal:** Pearl understands the codebase structurally, not just textually.

- AST-based symbol index with definition, callers, callees
- Incremental indexing: only re-parse changed files on each update
- Semantic search via embeddings alongside keyword search
- Cross-language symbol awareness (TypeScript, Go, Rust — not just Python)
- Symbol graph for impact analysis: "what else changes if I rename this?"

---

## M5 — Memory

**Goal:** Pearl accumulates understanding across a session and uses it effectively.

- Session memory: what was read, what was changed, what was tried and failed
- Context budgeting: relevance-ranked assembly within the token budget
- Summarization: prune old turns into compact prior-context blocks
- Project memory: patterns and preferences observed across sessions (opt-in)

---

## M6 — Self-hosting (Pearl builds Pearl)

**Goal:** Pearl is the primary tool used to develop Pearl.

Use Pearl to: refactor Pearl, generate tests, rename APIs, update documentation,
explain code, find dead code, fix lint issues. Every task done manually that Pearl
should be able to handle is a gap to close.

This milestone has no completion condition — it is a continuous practice.
The signal that it is working: the team reaches for Pearl before reaching for
a text editor.

---

## Later

- Multi-agent: parallel sub-agents on independent subtasks
- Deeper git integration: PR creation, branch-per-task workflows
- Additional IDE integrations: JetBrains, Cursor
- Vector memory alongside keyword index for large repositories

## Explicitly out of scope

- Bypassing patch approval for "trusted" runs — the approval guarantee is a core
  safety property, not a convenience feature to be made optional (see
  [ADR-002](docs/adr/ADR-002-approval-invariant.md))
- A hosted or managed version of Pearl — self-hostability is the point
