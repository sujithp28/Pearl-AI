# Pearl V2 — Completion Snapshot

This source tree contains the integrated V2 autonomous-agent stack.

## Core loop
`CONDENSE → PLAN → EXECUTE → APPROVE → VERIFY → REFLECT → REPLAN/REPAIR → DONE`

## Implemented subsystems
- token-aware conversation condensation with emergency ContextLengthError recovery
- dual-budget ContextEngine for history and repository context
- relevance-based tool filtering
- structured LLM reflection with bounded repair/replan cycles
- deterministic plan validation and dependency ordering
- PatchManager staging/approval boundary
- SearchReplaceEditor with exact/normalized/fuzzy matching
- verification and verification-driven replanning
- persistent session manager with concurrency protection
- EventBus and SSE bridge with terminal-event ordering
- tiered tool risk metadata and headless safety policy
- repository symbol index, graph, ranking, semantic context and multi-language parsers
- provider-independent ModelRouter with planning/edit/chat/condenser/reflection roles
- standalone web UI with sessions, plans, tools, approval, diffs, verification and streaming
- checkpoint/Git integration and workspace isolation
- Windows-compatible process/path handling

## Safety invariants
- writes remain staged until approval
- dangerous/headless operations fail closed
- workspace boundaries are enforced
- malformed model output is rejected
- reflection cannot mark a run complete solely from an unbounded confidence value
- cancellation and terminal events are handled explicitly

## Validation
3,040 tests pass, 3 are skipped, and 14 are deselected by the project's real-model
marker configuration. Six API test modules were excluded from that run because
`fastapi` — a declared dependency — is absent from the environment the run was
made in; they are unmodified by this work and exercise code identical to `master`.
Three errors in `tests/test_completion_service.py` have the same cause and
reproduce on `master` unchanged.

## Merge note
Two of the defects fixed on this branch were also found and fixed independently on
`master`, by different means. `master`'s fixes were kept in both cases:

- **Line endings.** This branch detected a file's dominant newline and re-encoded
  on write. `master` added `src/tools/file_io.py`, which reads and writes with
  `newline=""` so endings are never translated in either direction. That preserves
  mixed-ending files exactly, which the dominant-newline heuristic would normalise.
- **Headless `--yes` risk level.** This branch derived the batch's risk by
  inspecting the staged edits. `master` records the declared risk tier of each tool
  that actually staged something, and fails closed when a tier cannot be resolved.

The tests this branch added for both defects were retargeted onto `master`'s
mechanisms rather than dropped, so the regressions they guard against stay covered.
