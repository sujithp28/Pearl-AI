# ADR-002 — The Approval Invariant: No Autonomous Write Without Human Approval

**Date:** 2026-07-26  
**Status:** Accepted  
**Deciders:** Lead Architect

---

## Context

Pearl is an autonomous coding agent that generates plans and executes tool calls
on behalf of a developer. Some of those tool calls write files to the developer's
workspace. The core risk of an autonomous agent with write access is that it
modifies files the developer did not intend to modify, in ways the developer did
not anticipate, with consequences that may be difficult or impossible to reverse.

This risk is not theoretical. Every autonomous coding agent that does not enforce
a review gate has documented incidents of unintended file modifications, deleted
code, and corrupted state. The question is not whether Pearl will make mistakes —
it will — but whether those mistakes reach disk before the developer can catch them.

Two design philosophies were considered:

1. **Optimistic execution:** Pearl writes to disk autonomously and provides an undo
   mechanism after the fact. Fast, low-friction, but the damage is done before the
   developer sees it.

2. **Approval gate:** Pearl stages changes and presents them for review before
   anything reaches disk. Slightly more friction per operation, but the developer
   is always the last line of defence before a change becomes real.

---

## Decision

Every write to the developer's workspace that is initiated by an autonomous run must
pass through `PatchManager` and receive explicit human approval before reaching disk.
This is a non-negotiable invariant. It applies to every execution path, every agent
mode, and every future feature that performs autonomous file writes.

Shell commands in autonomous mode follow the same principle via `CommandApprovalManager`
— they are staged and approved before execution, though they are presented as a command
string rather than a diff.

---

## Alternatives Considered

| Alternative | Why rejected |
|---|---|
| Optimistic write with undo | By the time the developer sees the result, the write has happened. Undo requires correct state tracking and is error-prone under concurrent edits. The harm is the write; preventing it is safer than reversing it. |
| Approval only for destructive operations | Defining "destructive" is ambiguous and adversarial: a non-destructive write that adds a backdoor is more harmful than a destructive one that deletes a temp file. All writes require approval. |
| Auto-approve by default with opt-out | Inverts the trust model. New users would have no visibility into what Pearl is writing. Trust must be earned incrementally, not assumed. |
| Approval per file rather than per plan | Reviewing 10 separate single-file diffs is harder than reviewing one coherent multi-file diff. Grouping changes into a single approval makes the review meaningful. |

---

## Consequences

**Positive:**
- Developers always see what Pearl is about to write before it reaches disk
- The workspace can always be restored to a known pre-plan state via checkpoints
- Pearl's mistakes are visible and stoppable, not invisible and irreversible
- The invariant is testable: a single end-to-end test verifies it holds
- Trust in Pearl is earned incrementally — developers can verify every change before
  expanding their use of autonomous features

**Negative / Trade-offs:**
- Every autonomous write requires a round-trip to the user, which adds latency to
  the development workflow
- Batch operations (writing 20 files) require 20 files of diff to be reviewed, which
  is cognitively demanding. Mitigated by the approval UX in `05_UI_UX_GUIDELINES.md`
  which presents multi-file diffs as a single coherent review

**Risks:**
- A contributor adds a new execution path that bypasses `PatchManager`. Mitigated by:
  the approval invariant unit test (which must exist every sprint), the plan validation
  pipeline, and code review with the ownership matrix.
- The approval gate causes friction that leads users to disable it. Mitigated by:
  making auto-approve opt-in rather than opt-out, and investing in approval UX quality.

---

## Notes

This decision is implemented and enforced by `01_ARCHITECTURE_RULES.md` Section 3.
The rules A-1 through A-4 are the operational expression of this ADR. The checkpoint
system (shadow git repo) is the rollback mechanism that makes this invariant meaningful
even after approval — if an approved change turns out to be wrong, it can be restored.

Any proposal to remove or weaken this invariant must supersede this ADR and must
include a detailed analysis of the failure modes that the approval gate currently prevents.
