# Roadmap

Pearl's near-term direction, in rough priority order. Nothing here is a
commitment or a timeline — see [CHANGELOG.md](CHANGELOG.md) for what has
actually shipped.

## Now: Public Beta (v1.2.0-beta)

Stabilize, document, and package what already exists — see
`CHANGELOG.md`'s `1.2.0-beta` entry. No architectural changes.

## Next

- **Token usage tracking** — surface the `usage` field already present in
  OpenAI-compatible chat responses up through `LLMClient`/`Planner`, so
  cost/context-window usage is visible per run (identified as a gap
  during benchmarking, not yet implemented).
- **TypeScript linting/formatting** — the `vscode-extension/` package has
  no ESLint/Prettier configuration yet; CI currently only runs `tsc`
  (type checking) and the test suite for that half of the codebase.
- **Non-Python symbol intelligence** — `RepositoryIndex`/`SymbolEditor`
  are Python-only today; other languages fall back to generic
  file/text-search tools with no symbol-level awareness.
- **Cross-platform install verification** — the backend has no
  OS-specific code paths and uses `pathlib` throughout, but a from-scratch
  install has only been exercised in a Linux/WSL sandbox during this
  hardening pass, not natively on Windows or macOS.

## Later

- Multi-agent collaboration on a single task.
- Vector-based memory/retrieval alongside the existing symbol/keyword
  index, for larger repositories where keyword ranking alone is too
  coarse.
- Deeper git integration (PR creation, branch-per-task workflows).
- Additional IDE integrations beyond VS Code (JetBrains, Cursor).

## Explicitly out of scope for now

These are common asks that are deliberately not planned:

- Bypassing patch approval for "trusted" runs — the pause-for-approval
  guarantee is a core safety property, not a convenience feature to be
  made optional.
- A hosted/managed version of Pearl — self-hostability is the point.
