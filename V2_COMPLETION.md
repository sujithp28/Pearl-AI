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
The package compiles successfully in the provided environment. The complete pytest
suite contains 2,931 tests (14 intentionally deselected by the project's real-model
marker configuration). Full provider-test execution requires the declared `openai`
dependency, which is not installed in the execution environment used to build this
archive.
