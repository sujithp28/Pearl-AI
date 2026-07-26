# Architecture Decision Records — Index

This index shows the current decision for each architectural topic.
Each entry links to the full ADR where context, alternatives, and consequences are documented.
ADRs are immutable. When a decision changes, a new ADR is written that supersedes the old one.

---

## Current Decisions

| ID | Title | Status | Date |
|---|---|---|---|
| [ADR-001](ADR-001-two-process-architecture.md) | Two-Process Architecture: TypeScript Extension + Python Backend | Accepted | 2026-07-26 |
| [ADR-002](ADR-002-approval-invariant.md) | The Approval Invariant: No Autonomous Write Without Human Approval | Accepted | 2026-07-26 |

---

## How to Add an ADR

1. Copy [`TEMPLATE.md`](TEMPLATE.md) to `ADR-NNN-short-title.md` (next sequential number)
2. Fill in all sections
3. Add a row to the table above
4. If this supersedes an existing ADR, update the old ADR's **Status** line and mark
   the old entry in this index as `Superseded by ADR-NNN`

## When to Write an ADR

Write an ADR when:
- You chose between two non-obvious technical approaches
- You accepted a known trade-off in exchange for a specific property
- You reversed a prior architectural decision
- A future contributor reading the code would reasonably wonder why the obvious alternative was not used

See [`CLAUDE.md`](../../CLAUDE.md) Section 10 for the full guidance.
