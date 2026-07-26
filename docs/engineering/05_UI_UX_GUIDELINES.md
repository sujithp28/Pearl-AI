# 05 — UI/UX Guidelines

**Pearl AI Coding Agent — Engineering Standards Series**  
Document owner: Lead Software Architect  
Applies to: Every sprint, every contributor, every user-facing feature  
Status: Authoritative — changes require architect sign-off

---

## Purpose

This document defines the authoritative user experience and interface engineering
standard for the Pearl AI Coding Agent. It governs every interaction a developer
has with Pearl — from the first approval prompt to a checkpoint restore to an error
message at 2 AM.

This is not a style guide. It is an engineering standard with binding rules,
measurable acceptance criteria, named anti-patterns, and a complete review checklist.
A PR that introduces a new user-facing interaction without consulting this document
is incomplete by definition.

Pearl's UX is fundamentally different from a conventional application's UX. Pearl
acts autonomously and then asks for permission. This reversal of the normal
action-confirmation order creates unique design challenges that generic UX guidelines
do not address. Every section in this document is written for Pearl's specific context.

---

## Scope

This document applies to:
- The CLI REPL (`src/main.py`) and all its prompts, output, and formatting
- The VS Code Extension (`vscode-extension/`) — every webview, status bar item,
  notification, command palette entry, and diff viewer
- The `PersonalityManager` and `format_event()` output system
- All approval prompts, checkpoint interactions, error messages, and progress events
- Any future interface that Pearl exposes (a web UI, a GitHub Action, a TUI)

It cross-references:
- `01_ARCHITECTURE_RULES.md` — for the boundary between Python backend and TS extension
- `02_CODING_STANDARDS.md` — for the `PersonalityManager` and `format_event()` rules
- `03_TESTING_STANDARD.md` — for UX testing via persona testing and dogfooding
- `04_SECURITY_GUIDELINES.md` — for secure handling of diffs and sensitive content in UI

---

## Table of Contents

1. [Design Philosophy](#1-design-philosophy)
2. [User Experience Principles](#2-user-experience-principles)
3. [AI Conversation Guidelines](#3-ai-conversation-guidelines)
4. [Approval Experience](#4-approval-experience)
5. [Progress Experience](#5-progress-experience)
6. [Error Experience](#6-error-experience)
7. [Checkpoint Experience](#7-checkpoint-experience)
8. [Repository Experience](#8-repository-experience)
9. [Diff Experience](#9-diff-experience)
10. [Notifications](#10-notifications)
11. [Accessibility](#11-accessibility)
12. [Visual Design System](#12-visual-design-system)
13. [Performance Perception](#13-performance-perception)
14. [UX Anti-Patterns](#14-ux-anti-patterns)
15. [Common UX Mistakes](#15-common-ux-mistakes)
16. [UX Review Checklist](#16-ux-review-checklist)
17. [Release UX Checklist](#17-release-ux-checklist)

---

## 1. Design Philosophy

Pearl's design philosophy is not an aesthetic stance. It is a set of engineering
commitments that constrain every feature decision. Each principle in this section
has a corresponding rule that is binding in code review.

### 1.1 Human-First Design

Pearl acts; the human decides. Every autonomous action that has lasting consequences
MUST surface to the user for explicit approval before taking effect. The agent
should feel like a capable pair-programmer who drafts changes for review — not a
system that acts first and reports afterward.

**Rule UX-1:** The human is always the author of record. Pearl proposes; the developer
approves. This principle is implemented by the approval invariant
(`01_ARCHITECTURE_RULES.md`, Section 3) and is non-negotiable.

**Rule UX-2:** The interface MUST make it clear, at every moment, what Pearl has done
versus what has been approved. Staging is Pearl's work. Approval is the human's work.
The UI must never conflate the two.

```
GOOD — Status after staging:
  "Changes staged and awaiting your approval. Nothing has been written to disk."

BAD — Status after staging:
  "Done! File has been updated."  ← implies write happened without approval
```

### 1.2 Transparency

The user must always be able to answer: "What is Pearl doing right now? What did it
just do? Why?"

**Rule UX-3:** Every autonomous action MUST emit at least one progress event visible
to the user before it completes. Silent execution is prohibited. The user must be
able to follow Pearl's reasoning in real time.

**Rule UX-4:** When Pearl makes a planning decision, the reasoning MUST be exposed
on request. It does not need to be front-and-center, but it MUST be accessible. The
diff approval screen MUST show which tool call produced each staged change.

**Rule UX-5:** Pearl MUST surface uncertainty. When the LLM-generated plan relies
on assumptions, those assumptions MUST be visible in the progress output. A plan
built on false assumptions should fail gracefully with a clear explanation — not
silently produce wrong output.

### 1.3 Predictability

The user must be able to build a reliable mental model of Pearl's behavior. Given
the same input in the same state, Pearl must behave the same way.

**Rule UX-6:** Pearl's response patterns MUST be consistent across sessions.
"I type X, Pearl does Y" must hold across every run. A new feature MUST NOT change
the response shape of an existing interaction without a deprecation notice.

**Rule UX-7:** Approval prompts MUST always appear at the same point in the flow:
after planning and execution, before any write reaches disk. A feature that moves
the approval prompt to a different point in the flow is a UX regression and an
architectural violation.

**Rule UX-8:** Command syntax MUST be stable. Once a CLI command (`:checkpoint`,
`:restore`, `approve`) is documented, it MUST continue to work. If the syntax must
change, a transition period with both old and new forms is required (one sprint minimum).

### 1.4 Trust Before Automation

Pearl earns the user's trust incrementally. It does not demand blanket trust upfront.
New users should be able to verify every Pearl action before Pearl is trusted to
batch operations.

**Rule UX-9:** Automation features (batch approval, auto-approve, skip confirmation)
MUST be opt-in and MUST require explicit configuration. They MUST NOT be the default
for any new user.

**Rule UX-10:** Pearl MUST NOT execute sequences of actions that compound into
irreversibility without a checkpoint first. Before writing files, a checkpoint is
taken. Before executing a destructive shell command, the user sees the full command.

**Rule UX-11:** The undo path MUST always be visible. When any approval prompt is
displayed, the UI MUST include a reminder of how to undo the action if it produces
unexpected results (e.g., "This will be checkpointed. You can restore with `:restore`.").

### 1.5 Progressive Disclosure

Pearl deals with complex, multi-step operations. The user should not be overwhelmed
with information they don't need for the immediate decision.

**Rule UX-12:** Present the summary first; details on demand. The approval screen
shows "3 files changed, 12 insertions, 4 deletions" as the lead; the full diff is
below. The progress stream shows the current step; prior steps are scrollable but
not primary.

**Rule UX-13:** Complexity that is required for the current decision must be visible.
Complexity that is not required for the current decision must be collapsed or hidden
by default. The test: if you removed the element, would the user's decision quality
change? If no, hide it.

**Rule UX-14:** Never paginate an approval screen. All the information the user needs
to make an approval decision must be visible in one scroll session, not spread across
multiple confirmation steps.

### 1.6 User Control

The user can always take over. Pearl's automation is a preference, not a constraint.

**Rule UX-15:** Cancellation MUST work at any point. Ctrl-C in the CLI, a Cancel
button in the extension — these MUST stop Pearl's current operation cleanly, within
2 seconds, and leave the workspace in a consistent state.

**Rule UX-16:** The user can bypass Pearl's autonomous mode and invoke any tool
directly. The tool registry is not locked behind the agent. This escape hatch is
documented and maintained.

**Rule UX-17:** The user's explicit input always overrides Pearl's inference. If the
user specifies a file path, a tool, or an approach, Pearl MUST use exactly that —
not substitute its own judgment about what the user "really meant."

### 1.7 Minimal Surprise Principle

Pearl should never produce an output that makes a reasonable developer say "I didn't
expect that."

**Rule UX-18:** Every new behavior Pearl introduces MUST be announced to the user
before it executes — not after. "I'm about to run `npm install`" before the command,
not "I ran `npm install`" after.

**Rule UX-19:** Default behavior MUST match the most conservative reasonable
interpretation of the user's intent. When in doubt, do less and ask rather than
doing more and apologizing.

**Rule UX-20:** Side effects that are not implied by the user's prompt MUST be
called out explicitly. If the user asked Pearl to "fix the bug" and Pearl also
decides to reformat the file, that reformatting is a side effect that MUST be
highlighted in the approval diff.

---

## 2. User Experience Principles

### 2.1 Keyboard-First Workflow

Pearl's primary users are developers. Developers work with keyboards. Every Pearl
interaction MUST be fully operable via keyboard without requiring the mouse.

**Rule UX-21:** Every CLI action MUST be expressible as a typed command. No Pearl
feature requires mouse interaction in the CLI.

**Rule UX-22:** In the VS Code extension, every Pearl action MUST be accessible via
the Command Palette (`Ctrl+Shift+P` / `Cmd+Shift+P`). Commands MUST have memorable,
consistent names: `Pearl: Run Task`, `Pearl: Approve Changes`, `Pearl: Reject Changes`,
`Pearl: List Checkpoints`, `Pearl: Restore Checkpoint`.

**Rule UX-23:** Approval responses in the CLI MUST accept single-keystroke
affirmatives. `y` and `Y` both approve; `n`, `N`, `Enter` (default), and `Escape`
all reject. The user must not need to type "yes" and press Enter.

**Rule UX-24:** Keyboard shortcuts in the VS Code extension MUST NOT conflict with
VS Code's own default shortcuts. Check the existing keybinding table before assigning.
Pearl bindings use the `ctrl+shift+p` → command palette pattern as primary; keyboard
shortcuts are secondary and optional.

### 2.2 Response Latency Targets

| Interaction | Latency target | Notes |
|---|---|---|
| Keystroke acknowledgment | < 50ms | The UI must respond to every keypress |
| Approval prompt appears after run | < 100ms | From run completion to prompt display |
| Checkpoint list display | < 200ms | Local filesystem operation |
| Checkpoint creation feedback | < 500ms | Shadow git commit |
| Progress event display | < 100ms from emission | Network/rendering latency |
| Diff rendering (< 200 lines) | < 300ms | |
| Diff rendering (> 1,000 lines) | Show skeleton in < 300ms | Full render continues in background |
| Repository index completion | Progress shown throughout | No silent blocking |

**Rule UX-25:** If an operation cannot complete within its latency target, a progress
indicator MUST appear within 300ms of the operation starting. The user must never
stare at an unresponsive interface without feedback.

### 2.3 Cognitive Load Management

**Rule UX-26:** At any given moment, Pearl MUST only require the user's attention
on ONE decision. If a run produces both file patches and shell commands, they are
presented sequentially — patches first, then commands — not as a simultaneous
multi-item approval form.

**Rule UX-27:** Status messages MUST be short enough to read in one glance. No status
message should require the user to parse more than one sentence to understand the
current state. If more context is needed, link to it — don't inline it.

**Rule UX-28:** Terminology MUST be consistent. The word "approve" means approve; the
word "reject" means reject. The document uses these words consistently. The UI must
never use "confirm," "accept," "OK," "yes," "apply," and "approve" interchangeably
for the same concept.

Pearl's canonical vocabulary:

| Concept | Canonical term | Never use |
|---|---|---|
| User approves a staged patch | **approve** | confirm, accept, apply, OK, yes |
| User declines a staged patch | **reject** | deny, cancel, no, discard |
| Taking a snapshot | **checkpoint** | snapshot, save, backup |
| Restoring a snapshot | **restore** | rollback, revert, undo (use for individual lines) |
| The agent's plan output | **plan** | proposal, suggestion, action list |
| A single agent action | **step** | action, operation, task item |
| The running agent process | **run** | session, process, task |

### 2.4 Consistency Across Surfaces

**Rule UX-29:** The CLI and the VS Code extension MUST present the same UX model.
A developer who knows how approval works in the CLI must not be surprised by how it
works in the extension. The mechanics (approve/reject, checkpoint/restore) are
identical; the presentation adapts to the surface.

**Rule UX-30:** When a concept exists in both the CLI and the extension, the
terminology MUST match exactly. "Checkpoint" in the CLI, "Checkpoint" in the extension
— not "Checkpoint" in one and "Snapshot" in the other.

---

## 3. AI Conversation Guidelines

### 3.1 When Pearl Should Ask Questions

Pearl's default is to attempt the task with reasonable assumptions. Pearl asks the
user a clarifying question only when proceeding without clarification would be
unsafe, wrong, or significantly wasteful.

**Decision tree — should Pearl ask or proceed?**

```
Is the request ambiguous?
│
├─ NO → Proceed. Document the assumption in the progress output.
│
└─ YES
    ├─ Would proceeding with the most likely interpretation be safe and reversible?
    │   └─ YES → Proceed with that interpretation. State it clearly.
    │
    └─ NO (or the wrong interpretation has high cost)
        ├─ Is there one clear clarifying question that resolves the ambiguity?
        │   └─ YES → Ask that one question. Not two. Not three.
        │
        └─ NO (the request is fundamentally underspecified)
            └─ Explain what's missing and why. Ask the user to rephrase.
```

**Rule AI-UX-1:** Pearl MUST NOT ask more than one clarifying question per response.
If multiple clarifications are needed, ask for the most important one and infer the
rest.

**Rule AI-UX-2:** Pearl MUST NOT ask clarifying questions for requests that can be
safely attempted with a reasonable interpretation. "Fix the bug in auth.py" should
produce a plan, not a question.

**Rule AI-UX-3:** When Pearl proceeds on an assumption, that assumption MUST be
stated in the first progress event of the run:

```
GOOD:  "Assuming you want to refactor auth.py (the only modified file).
        If you meant a different file, reject and clarify."

BAD:   [silently proceeds with auth.py, no mention of the assumption]
```

### 3.2 When Pearl Should Make Assumptions

**Rule AI-UX-4:** Pearl SHOULD make assumptions for:
- Which file to operate on, when there is one obvious candidate (recently modified,
  mentioned in conversation history, the only file of its type)
- Which test framework to use, when the project already uses one
- Which coding style to follow, when one is already present in the codebase
- The language/encoding of files, when it can be detected

**Rule AI-UX-5:** Pearl MUST ask (or refuse and explain) for:
- Destructive operations where the target is ambiguous (delete which file?)
- Operations that would modify files outside the detected project scope
- Shell commands where arguments make a material difference to the outcome
- Any operation on configuration or secrets files

### 3.3 How Pearl Explains Reasoning

Every plan Pearl produces must be explainable. The explanation follows a strict
format: what, why, and — for risky steps — the rollback path.

**Rule AI-UX-6:** Each step in a plan summary MUST follow the format:
```
[Step N] <what Pearl will do> — <why this step is needed>
```

```
GOOD:
  [Step 1] Read auth.py — to understand the current authentication flow.
  [Step 2] Write updated auth.py — to replace the session token storage
           with an encrypted alternative (required by ADR-008).
  [Step 3] Run tests — to verify the change doesn't break existing behavior.

BAD:
  [Step 1] Read auth.py
  [Step 2] Modify auth.py
  [Step 3] Run tests
```

**Rule AI-UX-7:** When a plan step involves a decision point (e.g., Pearl chose to
edit function X rather than class Y), the reasoning MUST be visible in the step
summary. The developer can then reject and redirect if Pearl chose wrong.

### 3.4 Failure Communication

**Rule AI-UX-8:** When a step fails, Pearl's failure message MUST answer:
1. What step failed
2. What the error was (specific, not generic)
3. What Pearl will do next (replan, retry, stop, ask)

```
GOOD:
  "Step 2 failed: write_file raised PermissionError for 'src/auth.py'.
   The file may be open in another process. Replanning to check file state first."

BAD:
  "Step 2 failed. Replanning."
```

**Rule AI-UX-9:** When all replanning attempts are exhausted, Pearl MUST provide
a human-readable summary of what was accomplished before the failure and what
remains to be done, so the developer can continue manually.

### 3.5 Confidence Communication

**Rule AI-UX-10:** Pearl MUST signal low confidence when it applies. The signals are:
- "I believe..." or "I'm not certain, but..." at the start of a response
- A note in the plan step that the approach may need adjustment
- An explicit prompt to the user: "This approach is experimental. Review the diff
  carefully before approving."

Pearl MUST NOT signal false confidence by presenting uncertain plans in the same
format as certain ones.

**Rule AI-UX-11:** Pearl MUST NOT use confidence language for definitively known
facts. "I believe the file exists" when the file has just been confirmed to exist
is unnecessary hedging that reduces trust.

### 3.6 Avoiding Verbosity

**Rule AI-UX-12:** Pearl MUST NOT repeat information the user just provided.
If the user says "Fix the null check in `process_order()`", Pearl MUST NOT begin
its response with "I'll fix the null check in `process_order()`." The user already
knows what they asked.

**Rule AI-UX-13:** Pearl MUST NOT add closing pleasantries. "Let me know if you
need anything else!" is not appropriate for a developer tool. The REPL prompt
returning to the user is sufficient signal that the turn is complete.

**Rule AI-UX-14:** Progress messages MUST be short enough to read in one second.
A progress event that requires three sentences to convey its meaning is too long.

| Length | Appropriate for |
|---|---|
| 1 line (< 80 chars) | Standard progress events |
| 2–3 lines | Step completion with a notable result |
| 1 paragraph | Full run completion summary |
| Multiple paragraphs | Only for error messages that require full context |

---

## 4. Approval Experience

### 4.1 The Approval Invariant in UX Terms

The approval screen is the most critical UI in Pearl. It is the moment where
autonomous automation becomes human-verified action. The UX of this screen
directly determines Pearl's safety in real-world use.

See `01_ARCHITECTURE_RULES.md`, Section 3 for the architectural contract.
This section defines how that contract is expressed to the user.

### 4.2 Patch Approval Screen Structure

The approval screen MUST follow this structure, in this order:

```
════════════════════════════════════════════════════════════
  PEARL — Changes ready for approval
════════════════════════════════════════════════════════════

  Summary
  ───────
  3 files changed  ·  12 insertions (+)  ·  4 deletions (-)

  Risk: LOW   [or CAUTION / DANGER]

  ✓ Checkpointed: checkpoint abc123 saved before this write.
    Restore with: :restore abc123

  Files changed:
  ───────────────────────────────────────────────────────
  [M] src/auth/session.py                 +8  -3
  [M] tests/test_auth.py                  +4  -1
  [A] src/auth/encryption.py             +12   0
  ───────────────────────────────────────────────────────

  [Full diff below — scroll to review]

  ... (unified diff) ...

Apply these changes? [y/N]
```

**Rule APR-1:** The approval screen MUST always show, at minimum:
- Total files changed, insertions, and deletions
- Risk level (LOW / CAUTION / DANGER)
- The checkpoint ID that was taken before this write (if applicable)
- The restore command for that checkpoint
- A per-file breakdown with change counts

**Rule APR-2:** The full diff MUST always be accessible from the approval screen.
In the CLI, it appears below the summary. In the extension, it opens in a diff viewer.
The user MUST NOT be forced to approve a change they cannot see.

**Rule APR-3:** The approval screen MUST default to rejection. Pressing Enter without
typing `y` MUST reject, not approve. The default action for a consequential decision
is always the safe one.

### 4.3 Risk Level Classification

| Risk Level | Color | Trigger conditions |
|---|---|---|
| **LOW** | Green / neutral | Adds new files; modifies < 50 lines; no deletions |
| **CAUTION** | Yellow | Modifies > 50 lines; deletes < 10 lines; adds shell commands |
| **DANGER** | Red | Deletes files; deletes > 10 lines; modifies security-sensitive files |

**Security-sensitive files:** Any file matching: `**/auth*`, `**/.env*`, `**/secrets*`,
`**/*credential*`, `**/config/settings*`, `**/pyproject.toml`, `**/package.json`.

**Rule APR-4:** Risk level is computed automatically and shown on every approval screen.
It is never hidden and never overridden by the agent. It is for the user's information —
not for the agent's decision-making.

```python
def compute_risk_level(patch: StagedPatch) -> RiskLevel:
    if patch.deletes_file or patch.net_deletions > 10:
        return RiskLevel.DANGER
    if patch.touches_security_sensitive_file(SECURITY_SENSITIVE_PATTERNS):
        return RiskLevel.DANGER
    if patch.has_shell_commands or patch.net_changes > 50:
        return RiskLevel.CAUTION
    return RiskLevel.LOW
```

### 4.4 Dangerous Operation Highlighting

**Rule APR-5:** In the diff view, deleted lines (lines beginning with `-`) MUST be
visually distinct from added lines. In the CLI, use ANSI color codes (red for
deletions, green for additions). In the extension, use the standard diff color theme.

**Rule APR-6:** File deletions (not line deletions — entire file removals) MUST be
called out in the summary above the diff with explicit language:

```
⚠ WARNING: This change deletes the following file(s):
  • tests/legacy/test_old_auth.py (243 lines — this cannot be undone without
    restoring checkpoint abc123)
```

**Rule APR-7:** Lines that modify security-sensitive patterns (API key assignments,
credential writes, file permission changes) MUST be highlighted with a `[!]` marker
in the CLI diff:

```diff
- API_KEY = "old_key_value"
+[!] API_KEY = "new_key_value"   ← credential change — review carefully
```

### 4.5 Shell Command Approval

Shell command approval is handled separately from patch approval (by
`CommandApprovalManager`). The approval screen for shell commands MUST:

```
════════════════════════════════════════════════════════════
  PEARL — Shell command requires approval
════════════════════════════════════════════════════════════

  Command:  npm run test

  Risk:     LOW

  Working directory:  /home/user/myproject
  Timeout:            30 seconds

  This command will be run in your shell. It cannot be undone.
  Pearl will checkpoint your workspace before execution.

Run this command? [y/N]
```

**Rule APR-8:** The full command string MUST be shown verbatim. It MUST NOT be
truncated, even if it is very long. The user is authorizing exactly this command.

**Rule APR-9:** The working directory MUST be shown. A `rm -rf build` in the
project root and the same command in a subdirectory are different operations.

**Rule APR-10:** The timeout MUST be shown. The user should know that Pearl will
terminate the command after N seconds if it hasn't completed.

### 4.6 Batch Approval

When a plan produces multiple discrete approval gates (e.g., file patches followed
by a shell command), they are presented sequentially. There is no "approve all" mode.

**Rule APR-11:** Batch approval shortcuts MUST NOT be implemented without explicit
opt-in configuration. The default is: one approval gate, one decision.

**Rule APR-12:** After each approval gate, Pearl MUST confirm the result before
proceeding to the next gate:

```
  Changes applied successfully (checkpoint abc123).
  ──────────────────────────────────────────────────
  Continuing with next step: running tests...
```

### 4.7 Reject Flow

**Rule APR-13:** Rejection MUST produce a clear, calm confirmation — not an error:

```
GOOD:
  "Changes rejected. No files were written.
   Your workspace is unchanged."

BAD:
  "Error: user rejected patch"
  "Operation cancelled"
  [silence / empty line]
```

**Rule APR-14:** After rejection, Pearl MUST state what the user's options are:
- Re-run the task with different instructions
- Approve only specific files (future feature — document as not yet available)
- Restore to a prior checkpoint (with the ID shown)

### 4.8 The Undo Path

**Rule APR-15:** Every approval confirmation MUST include the checkpoint ID that
was taken before the write:

```
  Changes applied successfully.
  Checkpoint: abc123 (taken before this write)
  Restore with: :restore abc123
```

This information is not optional — it is what gives the user a safety net for
every approval they make.

---

## 5. Progress Experience

### 5.1 Progress Event Architecture

Pearl's progress system is built on `ProgressEvent` objects emitted by
`AutonomousExecutor` and forwarded to the caller via the `on_progress` callback.
The UX layer renders these events. See `02_CODING_STANDARDS.md`, Rule OBS-7 for
the emission requirements.

### 5.2 Progress Event Display in the CLI

**Rule PRG-1:** Each progress event MUST be rendered on its own line in the CLI.
Events do not replace each other — they accumulate, giving the user a visible
log of what Pearl has done.

**Rule PRG-2:** Progress line format:
```
  [<step>/<total>] <emoji> <message>
```

Where `<step>` and `<total>` are omitted when the total is not yet known (during
planning). Example:
```
  [1/4]  Reading src/auth.py (2,847 bytes)
  [2/4]  Analyzing authentication flow...
  [3/4]  Writing patched version of src/auth.py → staged for approval
  [4/4]  Plan complete. 1 file staged.
```

**Rule PRG-3:** The EmojiMode from `PersonalityManager` applies to all progress
events. In `none` mode, all emoji are suppressed. In `minimal` mode, emoji appear
only on significant events (task complete, error, approval required). In `normal`
and `fun` modes, emoji appear on all events.

### 5.3 Live Progress in the VS Code Extension

**Rule PRG-4:** The VS Code extension MUST display live progress in a dedicated
output channel ("Pearl: Output") that is opened automatically when a run starts.
The user must not need to find the output themselves.

**Rule PRG-5:** The extension MUST also update the status bar item during runs:
```
$(loading~spin) Pearl: Running [2/4]
```
After completion:
```
$(check) Pearl: Ready
```
After error:
```
$(error) Pearl: Failed — see output
```

**Rule PRG-6:** The status bar item MUST be clickable and open the Pearl output
channel when clicked.

### 5.4 ETA Guidelines

**Rule PRG-7:** Pearl MUST NOT display ETAs for LLM operations. LLM inference time
is non-deterministic and depends on the model, the provider, and network conditions.
Displaying an ETA for an LLM call that will be wrong 70% of the time is worse than
displaying no ETA.

**Rule PRG-8:** Pearl MAY display elapsed time for operations that have been running
longer than 10 seconds:
```
  [3/4]  Running analysis... (14s elapsed)
```

**Rule PRG-9:** For file system operations where the total scope is known (e.g.,
indexing a repository), Pearl MAY display a count-based progress indicator:
```
  Indexing repository... 1,247 / 5,000 files
```

### 5.5 Long-Running Operations

**Rule PRG-10:** Any operation that runs for longer than 5 seconds MUST display
progress updates at least every 5 seconds. A user who sees no output for more than
5 seconds will assume Pearl has crashed.

**Rule PRG-11:** For operations that Pearl knows will be slow (large repository
indexing, complex multi-step plans), Pearl MUST warn the user before starting:
```
  Indexing this repository (~50,000 files) will take approximately 60–90 seconds.
  You can cancel with Ctrl-C at any time.
```

### 5.6 Cancellation UX

**Rule PRG-12:** Cancellation MUST be acknowledged within 2 seconds of the user's
input. If the current operation cannot be interrupted immediately, Pearl MUST
display:
```
  Cancellation received. Waiting for current LLM call to complete...
  (Ctrl-C again to force-cancel — this may leave staged patches uncleaned)
```

**Rule PRG-13:** After a clean cancellation, Pearl MUST display:
```
  Cancelled. No files were written.
  Workspace is unchanged.
```

**Rule PRG-14:** Force-cancellation (second Ctrl-C) MUST display a warning about
potential state issues:
```
  Force-cancelled. Some state may be inconsistent.
  Run :checkpoints to verify workspace integrity.
```

### 5.7 Retry UX

**Rule PRG-15:** When Pearl triggers a replan (after a failed step), the user MUST
see it clearly:
```
  Step 3 failed: write_file raised PermissionError for 'dist/app.js'
  Replanning (attempt 2 of 3)...
```

**Rule PRG-16:** When replan attempts are exhausted, Pearl MUST summarize what was
accomplished before the failure and what the user can do next:
```
  Run incomplete — replanning failed after 3 attempts.

  Completed steps (1 of 4):
    ✓ [1] Read src/auth.py

  Failed step:
    ✗ [2] Write dist/app.js — PermissionError (file locked by another process)

  Remaining steps not executed:
    · [3] Run tests
    · [4] Update documentation

  Suggestion: Close any editor or build process that may have 'dist/app.js'
  open, then re-run the task.
```

---

## 6. Error Experience

### 6.1 Error Design Principle

Errors in developer tools are not failures — they are information. A good error
message is the first step of recovery. A bad error message is a barrier to it.

**Rule ERR-UX-1:** Every user-facing error MUST answer three questions:
1. What happened? (specific, not "An error occurred")
2. Why did it happen? (root cause, not "Something went wrong")
3. What should the user do next? (actionable suggestion)

### 6.2 Error Severity Levels

| Level | Color / marker | When to use | Example |
|---|---|---|---|
| **Info** | Blue / `[i]` | Useful context; no action required | "Index rebuilt (5,247 files)" |
| **Warning** | Yellow / `[!]` | Something unusual; action may be needed | "File encoding is not UTF-8 — using latin-1" |
| **Error** | Red / `[✗]` | Operation failed; action required | "Permission denied: cannot write 'auth.py'" |
| **Fatal** | Red bold / `[✗✗]` | Run cannot continue | "LLM provider returned no response after 3 retries" |

**Rule ERR-UX-2:** Error messages MUST be displayed at the appropriate severity level.
A recoverable permission error that Pearl replanned around MUST be `Warning`, not
`Error`.

### 6.3 Error Message Format

```
[✗] <What failed>: <Specific error condition>

    Cause:  <Why this happened>
    Fix:    <What the user can do>

    Details available with: pearl diagnose <error-code>
```

Examples:

```
GOOD:
  [✗] File write failed: permission denied for 'src/auth.py'

      Cause:  The file is read-only (mode 0444). This may be intentional
              write-protection or the result of a git checkout option.
      Fix:    Run 'chmod u+w src/auth.py' or check your git configuration.

BAD:
  Error: PermissionError: [Errno 13] Permission denied: 'src/auth.py'
```

```
GOOD:
  [✗] LLM provider unavailable: Ollama returned HTTP 503

      Cause:  Ollama may not be running, or the model is still loading.
      Fix:    1. Verify Ollama is running: 'ollama list'
              2. Wait 30 seconds if a model is loading
              3. Check the model name in your .env: DEFAULT_MODEL=<name>

BAD:
  HTTPError: 503 Service Unavailable
```

### 6.4 Common Error Patterns

Pearl MUST have named, consistent error messages for its most common failure modes.
These messages MUST NOT vary between runs:

| Error | Canonical message | Do not use |
|---|---|---|
| File outside workspace | "Path resolves outside the workspace boundary. Pearl cannot access files outside the workspace root." | Any generic "access denied" |
| Tool not found | "No tool named '{name}' is registered. The plan references a tool that doesn't exist." | Any generic KeyError message |
| Stale lock file | "A workspace lock file exists from a previous session. Run 'pearl --clear-lock' to remove it if no other session is active." | Any OS-level lock error |
| Provider not configured | "No LLM provider is configured. Set PEARL_LLM_PROVIDER in your .env file. Run 'pearl --setup' for a guided configuration." | Any "NoneType has no attribute" |
| Plan parse failure | "The LLM returned a response that could not be parsed as a plan. This is usually transient — try the same request again." | Raw JSON parse error |
| Approval with no pending run | "No run is currently awaiting approval. Start a task first." | Any RuntimeError |

### 6.5 Technical Detail Access

**Rule ERR-UX-3:** Full technical details (Python tracebacks, raw API responses)
MUST NOT appear in the primary user output. They MUST be available in a debug mode
or written to a log file.

**Rule ERR-UX-4:** Every error MUST include a way to access more detail:
```
  For detailed diagnostics, run with: PEARL_LOG_LEVEL=DEBUG pearl
  or check: ~/.pearl/logs/pearl-<date>.log
```

**Rule ERR-UX-5:** The user MUST be able to copy diagnostic information with a
single command. For the CLI, the standard pattern is:
```
  To report this issue: pearl report-error --last
```

### 6.6 Recovery Suggestions by Error Category

| Error category | Suggested recovery actions |
|---|---|
| Permission error on a file | Check file permissions; check if file is open in another process |
| LLM provider unavailable | Verify provider is running; check network; try again in 30s |
| Context window exceeded | Reduce the scope of the request; clear conversation history |
| Workspace boundary violation | Re-run the task with an explicit workspace path |
| Plan parse failure | Re-run the same request (transient); report if persistent |
| Stale lock file | Clear the lock; check if another Pearl instance is running |
| Tool execution error | Check the error message; the tool reports the specific cause |

---

## 7. Checkpoint Experience

### 7.1 Checkpoint UX Purpose

Checkpoints are Pearl's undo system. The UX of checkpoints must communicate two things:
(a) when a checkpoint was taken and (b) how to use it. Users who don't understand
that checkpoints exist will not use them. Users who don't know how to restore will
lose the protection they provide.

### 7.2 Checkpoint Display Format

**Rule CHK-1:** The `:checkpoints` command MUST display checkpoints in reverse
chronological order (most recent first), in this format:

```
  CHECKPOINTS  (most recent first)
  ────────────────────────────────────────────────────────────────
  abc123  2026-07-26 14:32:07  Before: write src/auth.py (auto)
  def456  2026-07-26 13:58:42  Before: write tests/test_auth.py (auto)
  ghi789  2026-07-26 13:45:00  Sprint 28 stable point (manual)
  ────────────────────────────────────────────────────────────────
  3 checkpoints  ·  Workspace: /home/user/myproject

  :restore <id>  ·  :delete <id>  ·  :rename <id> <label>
```

**Rule CHK-2:** Checkpoint IDs displayed to the user MUST be short (6–8 characters),
unambiguous, and human-pronounceable. Full SHA hashes are not user-facing.

**Rule CHK-3:** The checkpoint label MUST always be present. Auto-generated labels
follow the pattern `Before: <action> (<type>)`. Manual checkpoints use the user's
provided label, or `Manual checkpoint` if no label was given.

### 7.3 Auto-Checkpoint Behavior

**Rule CHK-4:** Auto-checkpoints MUST be announced before the write they protect,
not after. The approval screen (Section 4.2) includes the checkpoint confirmation.
After the write, the confirmation is repeated:

```
  ✓ Checkpoint abc123 saved (before this write)
```

**Rule CHK-5:** Auto-checkpoints MUST NOT interrupt the user's workflow. The
checkpoint creation message is informational — it requires no response.

**Rule CHK-6:** If auto-checkpointing fails, the write MUST still proceed (per
`01_ARCHITECTURE_RULES.md`, Rule ERR-4). But the user MUST be warned:

```
  [!] Checkpoint could not be created (shadow git error).
      This write is NOT protected by a checkpoint.
      Proceeding with your explicit approval.
```

### 7.4 Restore Confirmation Flow

**Rule CHK-7:** `:restore <id>` MUST display a preview before asking for confirmation:

```
  Restoring checkpoint abc123 (2026-07-26 14:32:07)
  ──────────────────────────────────────────────────

  This will:
    revert  src/auth.py           (current → checkpoint version)
    revert  tests/test_auth.py    (current → checkpoint version)
    remove  src/auth/encryption.py (added after this checkpoint)

  Files NOT affected:
    README.md, pyproject.toml, (and 84 others unchanged since checkpoint)

  Proceed? [y/N]
```

**Rule CHK-8:** After a successful restore, Pearl MUST confirm exactly what changed:

```
  ✓ Workspace restored to checkpoint abc123.

  Files reverted:  src/auth.py, tests/test_auth.py
  Files removed:   src/auth/encryption.py

  Your workspace is now in the state it was in at 2026-07-26 14:32:07.
```

**Rule CHK-9:** If nothing has changed since a checkpoint, `:restore` MUST say so
clearly rather than completing silently:

```
  Nothing to restore — your workspace already matches checkpoint abc123.
```

### 7.5 Checkpoint Naming Rules

**Rule CHK-10:** Manual checkpoint labels MUST be free-form text up to 80 characters.
No required format, no forbidden characters except newlines.

**Rule CHK-11:** Auto-checkpoint labels MUST follow the pattern:
`Before: <tool_name> <file_path_or_description> (auto)`

**Rule CHK-12:** `:rename` MUST work on both manual and auto checkpoints. Renaming an
auto checkpoint to a meaningful label (e.g., "Stable before auth refactor") is a
supported workflow.

### 7.6 Checkpoint Hygiene

**Rule CHK-13:** Pearl MUST NOT auto-delete checkpoints. Checkpoint deletion is
always an explicit user action.

**Rule CHK-14:** When more than 50 checkpoints exist in a workspace, Pearl MUST
display a notice on the `:checkpoints` listing:

```
  50+ checkpoints found. Consider cleaning up with :delete to keep the list manageable.
```

---

## 8. Repository Experience

### 8.1 Search UX

**Rule REPO-UX-1:** Search results MUST always display:
- The number of results found
- Whether results were truncated (and at what limit)
- The query that produced the results

```
  Found 37 matches for "authenticate" in src/  (showing 37 of 37)

  ─────────────────────────────────────────────────────────
  src/auth/session.py:42       def authenticate_user(...)
  src/auth/token.py:18         # called by authenticate()
  tests/test_auth.py:91        def test_authenticate_with_expired_token(...)
  ...
```

**Rule REPO-UX-2:** When search results are capped at `MAX_SEARCH_RESULTS`, the
truncation notice MUST be prominent — not buried at the end of the list:

```
  [!] Search truncated: showing first 200 of 1,483 matches.
      Narrow your query for more specific results.
```

**Rule REPO-UX-3:** Search latency MUST be communicated. For searches that take
> 1 second, display a progress indicator:
```
  Searching... (2.3s)
```

### 8.2 Repository Indexing Progress

**Rule REPO-UX-4:** Repository indexing MUST display file-count progress. Silent
indexing of a large repository will appear as a frozen process:

```
  Indexing repository...
  ├ Scanning files:     12,450 / ~50,000
  ├ Parsing symbols:    8,204 parsed
  └ Estimated time:     ~45s remaining
```

**Rule REPO-UX-5:** After indexing completes, Pearl MUST display a summary:
```
  ✓ Repository indexed: 49,847 files, 412,300 symbols (62s)
  Run :search or ask Pearl to find files and symbols.
```

**Rule REPO-UX-6:** If indexing is interrupted (cancel, crash), the partial index
MUST NOT be used silently. Pearl MUST warn that the index is incomplete:
```
  [!] Repository index is incomplete (indexing was interrupted).
      Symbol search results may be inaccurate.
      Run :reindex to rebuild the index.
```

### 8.3 File and Symbol Navigation

**Rule REPO-UX-7:** `find_symbol` results MUST display the file path, line number,
and a one-line context snippet for each result:

```
  Symbol: authenticate_user
  ──────────────────────────────────────────────────────────
  src/auth/session.py:42     def authenticate_user(username: str, ...) -> bool:
  src/auth/middleware.py:18  # calls authenticate_user on every request
  ──────────────────────────────────────────────────────────
  2 definitions, 1 reference
```

**Rule REPO-UX-8:** File paths MUST always be shown relative to the workspace root.
Absolute paths are harder to read and reveal personal directory structure needlessly.

### 8.4 Large Repository Handling

**Rule REPO-UX-9:** For repositories with > 10,000 files, Pearl MUST display a notice
before the first indexing operation:

```
  This is a large repository (~50,000 files).
  Initial indexing will take 60–120 seconds.
  Subsequent sessions will re-use the cached index.
```

**Rule REPO-UX-10:** Large repository searches MUST show result counts before
displaying results, giving the user the option to refine:
```
  Found 1,483 matches for "config". Showing first 200.
  Add a path filter to narrow results: e.g., "config in src/api/"
```

---

## 9. Diff Experience

### 9.1 Diff Header

Every diff MUST begin with a summary header that gives the user a one-glance
understanding of the scope of the change:

```
  3 files changed  ·  24 insertions (+)  ·  7 deletions (-)
  Risk: CAUTION

  src/auth/session.py         +16  -4   [Modified]
  tests/test_auth.py           +8  -3   [Modified]
  src/auth/encryption.py           0    [New file]
```

**Rule DIFF-1:** The diff header MUST always precede the diff content. A diff that
starts with raw `diff --git` output without a human-readable header is not acceptable.

**Rule DIFF-2:** File status MUST use a four-state vocabulary:
- `[New file]` — file is being created
- `[Modified]` — existing file is being changed
- `[Deleted]` — existing file is being removed
- `[Renamed]` — file is being moved (from `old_path` to `new_path`)

### 9.2 Diff Content Format

**Rule DIFF-3:** Diffs MUST use unified diff format (the `---`/`+++`/`@@` format).
Context of 3 lines around each change is shown; unchanged sections between hunks
are collapsed with `@@ ... @@` markers.

**Rule DIFF-4:** In the CLI, diff output MUST use ANSI color:
- Added lines: green (or the terminal's ANSI green)
- Removed lines: red (or the terminal's ANSI red)
- Hunk headers (`@@ -N,N +N,N @@`): cyan / dimmed
- File headers: bold

**Rule DIFF-5:** Line numbers MUST be shown in the diff, both before and after.
A change with no line numbers makes it impossible to navigate to the changed code.

### 9.3 Large Diff Handling

**Rule DIFF-6:** Diffs larger than 500 lines MUST show a warning before the full
diff content:

```
  [!] Large diff (1,243 lines). Reviewing the summary above is recommended.
      Showing full diff below — scroll to review.
```

**Rule DIFF-7:** Diffs larger than 2,000 lines MUST be truncated by default, with
an explicit command to view the full diff:

```
  [!] Very large diff (4,891 lines). Showing first 2,000 lines.
      To review the complete diff: pearl diff --full
      To approve without viewing the full diff: y
      To reject: n (or Enter)
```

**Rule DIFF-8:** Files that are entirely new (no existing content to compare) MUST
show the full content with `+` prefix on each line. The diff header for new files
reads `[New file: N lines]`, not `@@ -0,0 +1,N @@`.

### 9.4 Risk Indicators in Diffs

**Rule DIFF-9:** Lines that match security-sensitive patterns (credential assignments,
file permission changes, `sudo` commands in shell scripts) MUST be marked with `[!]`
in the diff margin:

```diff
- password = "old_password"
+[!] password = "new_password"
```

**Rule DIFF-10:** Large deletions (more than 20 consecutive deleted lines) MUST be
collapsed by default with a notice:

```diff
  ... (24 lines deleted — press 'd' to expand)
```

---

## 10. Notifications

### 10.1 Notification Types and Behavior

| Type | Trigger | Display | Auto-dismiss |
|---|---|---|---|
| **Success** | Operation completed | Brief, bottom of output | N/A (CLI); 5s (extension) |
| **Warning** | Unexpected but recoverable | Yellow, inline | N/A (requires acknowledgment in CLI) |
| **Error** | Operation failed | Red, with recovery suggestions | Never — stays visible |
| **Info** | Useful context, no action needed | Blue / neutral | N/A (CLI); 8s (extension) |
| **Progress** | Operation in progress | Animated / streaming | Replaced by completion message |
| **Completion** | Run or major step done | Summary format | N/A |

**Rule NOTIF-1:** Success notifications MUST be brief (one sentence). They confirm
what happened without re-explaining the task.

```
GOOD:  "✓ 3 files written. Checkpoint abc123 taken."
BAD:   "Your request to refactor the authentication module has been successfully
        applied to the repository. All changes have been written to disk."
```

**Rule NOTIF-2:** Warnings MUST describe the condition AND whether action is required:
```
  [!] Repository index is 2 minutes old. Results may be stale.
      Run :reindex to refresh. (No action required if files haven't changed.)
```

**Rule NOTIF-3:** Errors are never dismissed automatically. The error persists until
the user takes action or starts a new task.

### 10.2 Non-Intrusive Behavior

**Rule NOTIF-4:** Notifications MUST NOT block the user's ability to type or navigate.
In the VS Code extension, use the status bar and output panel — not modal dialogs —
for informational notifications.

**Rule NOTIF-5:** Progress notifications MUST NOT produce a new line for every token.
Events are batched and rendered at a reasonable rate (maximum 10 updates per second
in the CLI, 4 per second in the extension status bar).

**Rule NOTIF-6:** Notifications that repeat with each run (e.g., "Index rebuilt") MUST
NOT appear every run. Show them only when the state has changed from the prior run.

### 10.3 Completion Messages

**Rule NOTIF-7:** Every run MUST end with a completion message that states:
1. What stop reason was reached
2. How many steps were completed
3. Whether anything was written to disk

```
  ─────────────────────────────────────────────────────────────
  Run complete (5 steps)  ·  2 files written  ·  Checkpoint abc123
  ─────────────────────────────────────────────────────────────
```

---

## 11. Accessibility

### 11.1 Guiding Standard

Pearl targets WCAG 2.1 Level AA compliance for all graphical interfaces (VS Code
extension webviews). The CLI is constrained by terminal capabilities; accessibility
guidance for the CLI focuses on what is achievable in a text-based environment.

### 11.2 Keyboard Navigation

**Rule ACC-1:** All VS Code extension UI elements MUST be fully navigable via keyboard.
Tab order MUST follow a logical reading sequence (top to bottom, left to right within
panels). There MUST be no keyboard trap — the user can always escape any modal or panel.

**Rule ACC-2:** Every interactive element in the extension (buttons, input fields,
checkboxes) MUST have a visible focus indicator that meets WCAG 2.1 SC 2.4.7
(minimum 2px solid outline or equivalent contrast).

**Rule ACC-3:** All keyboard shortcuts MUST be discoverable. The Command Palette
entry for each Pearl command MUST show the keyboard shortcut if one is assigned.

### 11.3 Screen Reader Support

**Rule ACC-4:** All VS Code extension UI elements MUST have appropriate ARIA labels.
Dynamic content regions (progress output, diff content) MUST use `aria-live="polite"`
so screen readers announce updates without interrupting the user.

**Rule ACC-5:** Progress updates in live regions MUST be debounced to a rate that
screen readers can keep up with: no more than one ARIA live region update per second.

**Rule ACC-6:** The diff viewer MUST have a plain-text alternative accessible to
screen readers. The default diff view uses color and layout; the accessible view
uses labeled text ("Added: ...", "Removed: ...").

**Rule ACC-7:** Icons MUST have text alternatives. In the extension, every icon button
MUST have a `title` attribute and an `aria-label` that describes its action.

### 11.4 Color Independence

**Rule ACC-8:** Color MUST NOT be the only means of conveying information. Risk
levels (LOW / CAUTION / DANGER) use color AND a text label AND an icon. Diff lines
use color AND +/- prefix characters.

```
GOOD:  [✗] Error (red)  — both icon and color convey error state
BAD:   [Error message in red with no other indicator]
```

**Rule ACC-9:** All color pairs used in the extension MUST meet WCAG 2.1 SC 1.4.3
minimum contrast ratio (4.5:1 for normal text, 3:1 for large text and UI components).

### 11.5 High Contrast Mode

**Rule ACC-10:** The VS Code extension MUST function correctly in VS Code's High
Contrast themes (both light and dark). Use VS Code's semantic color tokens
(`vscode.TextEditor.foreground`, `vscode.StatusBar.background`, etc.) rather than
hard-coded hex values. This ensures automatic compatibility with user theme overrides.

**Rule ACC-11:** Test the extension in High Contrast Black and High Contrast Light
before every release. Both themes must produce legible, functional UI.

### 11.6 Reduced Motion

**Rule ACC-12:** All animations in the VS Code extension MUST respect the
`@media (prefers-reduced-motion: reduce)` CSS media query. When reduced motion is
preferred, animations are replaced with instant state changes. Spinner animations
are replaced with static indicators.

```css
@keyframes spin {
  to { transform: rotate(360deg); }
}
.loading-icon {
  animation: spin 1s linear infinite;
}
@media (prefers-reduced-motion: reduce) {
  .loading-icon {
    animation: none;
    opacity: 0.6;  /* static visual indicator instead */
  }
}
```

### 11.7 Focus Management

**Rule ACC-13:** When a modal or dialog opens (e.g., the restore confirmation), focus
MUST move to the dialog. When the dialog closes, focus MUST return to the element
that triggered it.

**Rule ACC-14:** After an approval or rejection, focus MUST return to the main input
area so the user can immediately type their next prompt without reaching for the mouse.

---

## 12. Visual Design System

### 12.1 Typography

Pearl uses two type contexts:

| Context | Typeface | Size | Weight | Use |
|---|---|---|---|---|
| Code and diffs | Monospace (user's editor font) | 13px | Normal | File content, diffs, command output |
| UI labels | VS Code UI font (system sans-serif) | 13px | Normal | Labels, buttons, descriptions |
| Headings | VS Code UI font | 15–18px | Semibold | Panel titles, section headers |
| Data (line numbers, counts) | Monospace | 12px | Normal | Line numbers, counts, IDs |

**Rule VD-1:** Code and output content MUST always use a monospace typeface.
Displaying code or diff content in a proportional typeface breaks character alignment
and makes diffs unreadable.

**Rule VD-2:** Use `font-variant-numeric: tabular-nums` for all numeric columns
(line numbers, counts, change statistics) to ensure digits align.

### 12.2 Color System

Pearl's color system uses semantic roles, not decorative choices. The colors
reinforce meaning; they are not aesthetic selections.

| Semantic role | VS Code token | Light theme | Dark theme |
|---|---|---|---|
| Success / added | `gitDecoration.addedResourceForeground` | `#28a745` | `#4ec9b0` |
| Warning / caution | `editorWarning.foreground` | `#f0ad4e` | `#cca700` |
| Error / deleted | `gitDecoration.deletedResourceForeground` | `#d73a49` | `#f44747` |
| Info / neutral | `editorInfo.foreground` | `#1890ff` | `#75beff` |
| Accent / approval prompt | `button.background` | Theme default | Theme default |
| Checkpoint | Custom token: `pearl.checkpointForeground` | `#6f42c1` | `#c586c0` |

**Rule VD-3:** Never use hard-coded hex colors in the VS Code extension's CSS.
Use VS Code semantic color tokens exclusively. Hard-coded colors break in user-
customized themes.

**Rule VD-4:** The CLI MUST use ANSI escape codes for color, not hard-coded terminal
sequences. Use Python's `colorama` or equivalent cross-platform abstraction.

**Rule VD-5:** The CLI MUST detect whether the output is a terminal (TTY) before
emitting ANSI codes. If output is being piped to a file or another process, ANSI
codes MUST be suppressed. Check with `sys.stdout.isatty()`.

### 12.3 Spacing

Pearl uses an 8-pixel base spacing unit in the VS Code extension. All spacing values
are multiples of 4px or 8px.

| Space | Value | Use |
|---|---|---|
| `--space-xs` | 4px | Within tight UI elements (icon + label gap) |
| `--space-sm` | 8px | Between related elements (label + input) |
| `--space-md` | 16px | Between sections (summary + diff) |
| `--space-lg` | 24px | Between major panels |
| `--space-xl` | 32px | Page-level padding |

In the CLI, use consistent indentation: 2 spaces for list items, 4 spaces for
nested items, aligned columns using spaces (not tabs, which render differently).

### 12.4 Layout Principles

**Rule VD-6:** The VS Code extension uses a single-column layout for chat and a
two-panel layout for diff review (summary left, diff right). Panels MUST be
resizable. They MUST NOT have a fixed minimum size that makes them unusable at
common VS Code split-pane widths.

**Rule VD-7:** All scrollable content areas MUST have their overflow defined explicitly.
Uncontrolled overflow that causes the outer layout to grow is a layout bug.

**Rule VD-8:** Diff content areas MUST use `overflow-x: auto` with `white-space: pre`
to preserve code formatting without causing the page to scroll horizontally.

### 12.5 Icons

**Rule VD-9:** The VS Code extension MUST use VS Code's built-in codicon icon set
exclusively. Do not import additional icon libraries — they conflict with the user's
icon theme and increase bundle size.

Common Pearl codicon usage:

| Context | Icon | Codicon ID |
|---|---|---|
| Pearl agent running | Spinner | `$(loading~spin)` |
| Approval prompt | Checkmark circle | `$(check)` |
| Error | X circle | `$(error)` |
| Warning | Warning triangle | `$(warning)` |
| Checkpoint | Milestone | `$(milestone)` |
| Restore | History | `$(history)` |
| Cancel | Close | `$(close)` |

### 12.6 Panels and Dialogs

**Rule VD-10:** Confirmation dialogs (approve, reject, restore) MUST use VS Code's
built-in `vscode.window.showWarningMessage()` or `showInformationMessage()` API —
not custom webview modals — for simple yes/no decisions. Custom webviews are
reserved for complex content (diffs, checkpoint timelines).

**Rule VD-11:** The diff viewer MUST be implemented as a VS Code `TextEditor` diff
with `vscode.diff()` wherever possible. This gives users native VS Code diff
functionality (line-level navigation, existing keybindings) without reimplementing it.

---

## 13. Performance Perception

### 13.1 The 100ms Rule

Users perceive interactions as instant when they complete in under 100ms. Between
100ms and 1,000ms, users notice the delay but remain in flow. Beyond 1,000ms,
users lose confidence and begin to wonder if the system is working.

**Rule PERF-UX-1:** Any user action that triggers UI state change MUST produce
visible feedback within 100ms. If the underlying operation takes longer, show an
intermediate state (spinner, skeleton, "thinking...") within 100ms.

### 13.2 Skeleton Loading

**Rule PERF-UX-2:** When the VS Code extension loads a content panel (chat history,
checkpoint list, diff view), it MUST display a skeleton layout — placeholder elements
in the correct shape — within 100ms. Content fills in as it loads.

```
  SKELETON (shown within 100ms):
  ┌──────────────────────────────┐
  │ ████████████████ (loading)   │
  │ ██████████████████████       │
  │ ████████████                 │
  └──────────────────────────────┘

  LOADED (filled in when data arrives):
  ┌──────────────────────────────┐
  │ src/auth/session.py  +16 -4  │
  │ tests/test_auth.py   +8  -3  │
  │ src/auth/encryption  +0   0  │
  └──────────────────────────────┘
```

### 13.3 Incremental Rendering

**Rule PERF-UX-3:** LLM streaming output MUST be rendered incrementally — each token
(or small batch of tokens) appears as it arrives. Buffering the full LLM response
before rendering defeats the purpose of streaming and creates a jarring "blink"
where nothing happens and then everything appears at once.

**Rule PERF-UX-4:** Diff rendering MUST begin as soon as the first file's diff is
available. Users should not wait for all files to be diffed before seeing any diff
content.

### 13.4 Optimistic UI

**Rule PERF-UX-5:** For operations where the outcome is highly predictable and
reversible, Pearl MAY use optimistic UI — showing the expected state immediately
while the operation executes in the background.

This applies specifically to checkpoint creation: when the user runs `:checkpoint`,
the UI shows the checkpoint as created immediately (with a temporary ID), then
updates the ID once the shadow git commit completes.

Optimistic UI MUST be clearly reversed if the operation fails. A checkpoint that
appeared to be created and then silently disappeared would be worse than waiting.

### 13.5 Perceived Responsiveness

**Rule PERF-UX-6:** Do not use indeterminate loading spinners for operations where
progress can be measured. A progress bar with "12,450 / 50,000 files" is more
reassuring than an indefinite spinner.

**Rule PERF-UX-7:** For multi-step runs, display the current step number and total
steps. A user who can see "[3/7]" knows the run is making progress; a user who sees
only a spinner wonders if the run is stuck.

**Rule PERF-UX-8:** End the run completion message with a clear terminal state.
The REPL prompt returning (`Pearl > `) is the canonical signal that Pearl is ready
for the next command. The extension status bar returning to "Pearl: Ready" is its
equivalent. There MUST be no ambiguity about whether Pearl is ready for the next input.

---

## 14. UX Anti-Patterns

These patterns are unconditionally forbidden. Any PR that introduces one of them
is rejected in code review.

### ANT-01: Hidden Destructive Actions

```
BAD:
  Pearl deletes three test files as part of a refactor.
  The approval screen shows:
    "3 files modified."
  The deleted files are not mentioned.

WHY IT'S BAD:
  The developer approves without knowing they are deleting files.

CORRECT:
  The approval screen explicitly states:
    "[A] 2 files added  [M] 1 file modified  [D] 3 files deleted"
  File deletions are listed individually with a warning icon.
```

### ANT-02: Blocking Dialogs for Non-Urgent Information

```
BAD:
  A modal dialog appears: "Repository indexed successfully. Click OK."
  The user must click OK before they can continue typing.

WHY IT'S BAD:
  Non-urgent information must never block the user's primary workflow.

CORRECT:
  Status bar: "✓ Repository indexed: 5,247 files" (auto-dismisses in 5s)
  No user action required.
```

### ANT-03: Excessive Confirmations

```
BAD:
  :checkpoint
  → "Are you sure you want to create a checkpoint? [y/N]"
  → y
  → "Checkpoint created. Press Enter to continue."

WHY IT'S BAD:
  Creating a checkpoint is safe and reversible. It does not need confirmation.

CORRECT:
  :checkpoint
  → "Checkpoint abc123 saved: Manual checkpoint"
  (no confirmation step)
```

### ANT-04: Generic Error Messages

```
BAD:
  "An error occurred. Please try again."

WHY IT'S BAD:
  No information about what failed or how to fix it.

CORRECT:
  "[✗] Write failed: permission denied for 'src/auth.py'
   Fix: chmod u+w src/auth.py"
```

### ANT-05: Silent Failures

```
BAD:
  Pearl tries to create a checkpoint. The shadow git operation fails.
  Pearl continues and writes the files.
  The user never knows the checkpoint failed.

WHY IT'S BAD:
  The user believes they have a recovery path when they do not.

CORRECT:
  "[!] Checkpoint could not be created (shadow git error: insufficient permissions).
   This write is NOT protected by a checkpoint. Proceeding with your approval."
```

### ANT-06: Inconsistent Terminology

```
BAD:
  CLI:       "Apply these changes? [y/N]"
  Extension: "Accept proposed modifications?"
  Error log: "User denied confirmation"

WHY IT'S BAD:
  Three surfaces describe the same action three different ways. Developers
  reading documentation or error reports cannot correlate them.

CORRECT:
  All surfaces: "Apply these changes?" / "approve" / "reject"
  (See Section 2.3 canonical vocabulary table)
```

### ANT-07: Information Overload at the Wrong Moment

```
BAD:
  The approval screen shows: 4,891 lines of diff, all expanded, no summary.
  The user must scroll through the entire diff to find the approve prompt.

WHY IT'S BAD:
  The user's cognitive load is dominated by information they need to process
  before making a decision. The decision itself is buried.

CORRECT:
  Summary header at the top. Full diff scrollable below. Approve prompt always
  visible (sticky footer in extension; repeated after diff in CLI).
```

### ANT-08: Auto-Approve Creep

```
BAD:
  Pearl adds a `--skip-approval` flag that skips the approval prompt.
  Power users use it because the prompt is annoying.
  Six months later, a prompt-injection attack abuses --skip-approval.

WHY IT'S BAD:
  Approval bypass features accumulate, each justified individually.
  Together, they erode the core safety guarantee.

CORRECT:
  No approval skip. The approval prompt is not the problem — a verbose or
  poorly formatted approval screen is. Fix the screen, not the invariant.
  (See 01_ARCHITECTURE_RULES.md, Rule A-2)
```

### ANT-09: Progress Theater

```
BAD:
  Pearl shows:
  "Processing..."   (for 30 seconds, nothing else)
  "Almost done..."  (for 10 more seconds)
  "Complete!"

WHY IT'S BAD:
  The progress messages provide no information. The user cannot tell if the
  system is working or stuck.

CORRECT:
  "[1/4] Reading src/auth.py (2,847 bytes)"
  "[2/4] Analyzing authentication flow (LLM call in progress, 12s elapsed)"
  "[3/4] Writing changes to src/auth.py → staged"
  "[4/4] Plan complete. 1 file staged."
```

### ANT-10: Inconsistent Cancel Behavior

```
BAD:
  Ctrl-C during step 2 cancels the run.
  Ctrl-C during step 3 does nothing (the step catches the signal).
  Ctrl-C during step 4 crashes the process with a traceback.

WHY IT'S BAD:
  The user cannot build a reliable mental model of how cancellation works.
  An inconsistent cancel is worse than no cancel (it creates false confidence).

CORRECT:
  Ctrl-C at any point: triggers clean cancellation via cancel_event.
  Never crashes. Always leaves workspace in a consistent state.
  (See 01_ARCHITECTURE_RULES.md, Rule E-1; 02_CODING_STANDARDS.md, Rule ASYNC-3)
```

### ANT-11: Missing Undo Information After Approval

```
BAD:
  "Changes applied successfully."

WHY IT'S BAD:
  The user who immediately regrets the change has no information about how to undo it.

CORRECT:
  "Changes applied successfully.
   Checkpoint: abc123 (taken before this write)
   Restore with: :restore abc123"
```

### ANT-12: Truncation Without Notice

```
BAD:
  Search returns 200 results. The list ends. The user doesn't know if those
  are all the results or just the first 200.

CORRECT:
  "Showing first 200 of 1,483 matches. Add a path filter to narrow results."
```

### ANT-13: Stale UI State

```
BAD:
  The VS Code extension shows "Pearl: Ready" in the status bar while Pearl
  is actually running a task (the status update was dropped due to an error
  in the notification handler).

CORRECT:
  Status bar state is derived from the canonical run state, not from events
  that may be lost. Poll or derive state from the MCP server's report.
```

### ANT-14: Jargon in Error Messages

```
BAD:
  "ToolExecutionError: ToolNotFoundError: 'create_file' not in registry"

WHY IT'S BAD:
  Internal exception names are not meaningful to most users.

CORRECT:
  "[✗] Plan references an unknown tool: 'create_file'
   This usually indicates a planning error. Try rephrasing your request."
```

### ANT-15: Confirmation for Safe Actions

```
BAD:
  :checkpoints
  "Are you sure you want to list checkpoints? [y/N]"

WHY IT'S BAD:
  Read-only operations do not need confirmation. Asking for it trains users
  to confirm everything reflexively — including destructive operations.

CORRECT:
  :checkpoints  →  immediately displays the checkpoint list.
```

---

## 15. Common UX Mistakes

These mistakes have been observed in practice or are commonly introduced by
contributors who haven't read this document. Know them before opening a PR.

### Mistake 1: Using exit codes or exception text as user-facing error messages

**Symptom:** A Python exception with a stack trace appears in the CLI output.  
**Fix:** Catch all exceptions in `main()`, format them with the error template from
Section 6.3, and log the traceback to DEBUG only.

### Mistake 2: Assuming the user will read a long progress stream

**Symptom:** Progress output is 40 lines for a 3-step operation. Key information
(what was staged, the checkpoint ID) is buried in the middle.  
**Fix:** The completion summary repeats critical information (checkpoint ID, what was
written). Don't make the user scroll back to find it.

### Mistake 3: Designing for the happy path only

**Symptom:** The approval screen looks great for a 3-file change but is unusable
for a 50-file change (no truncation, no summary, just raw output).  
**Fix:** Test every UI element with both minimal (1 item) and maximal (200+ items)
inputs. Design the degenerate cases explicitly.

### Mistake 4: Using "cancel" and "reject" interchangeably

**Symptom:** The approval prompt says "Cancel" but the checkpoint screen says "Reject."  
**Fix:** See canonical vocabulary in Section 2.3. "Reject" is for approval flows.
"Cancel" is for in-progress operations (Ctrl-C equivalent).

### Mistake 5: Adding confirmation prompts to undo actions

**Symptom:** `:restore abc123` is asked "Are you sure you want to restore? (y/N)"
AND "This will overwrite current files. Are you really sure? (y/N)".  
**Fix:** One confirmation step, shown with a preview of what will change (Section 7.4).
Two confirmation steps for the same action is excessive.

### Mistake 6: Showing file paths as absolute paths

**Symptom:** Error message shows `/home/sujith/myproject/src/auth.py` instead of
`src/auth.py`.  
**Fix:** All file paths displayed to the user are relative to the workspace root.

### Mistake 7: Forgetting to test in dark mode AND light mode

**Symptom:** The extension looks fine in dark mode but diffs are unreadable in light
mode (green on white, low contrast).  
**Fix:** Test the complete approval flow in both VS Code's Dark+ and Light+ themes
before every release.

### Mistake 8: Progress events that appear after the operation completes

**Symptom:** The approval prompt appears, the user approves, and THEN the progress
events for the steps leading to the approval appear. Events arrive out of order.  
**Fix:** Progress events are emitted synchronously during execution. The approval
prompt appears only after all pre-approval events have been emitted.

### Mistake 9: Not testing cancellation at each step

**Symptom:** Cancellation during step 3 of a 5-step plan leaves context vars set
and no cleanup message shown.  
**Fix:** Cancel is tested at each step boundary in the test suite (Section 12.3,
CHAOS-04). The cancellation path MUST always clean up context vars and print the
cancellation message.

### Mistake 10: Writing error messages in past tense for ongoing failures

**Symptom:** "Indexing failed." (when the user might retry immediately)  
**Fix:** Error messages are present-tense and forward-looking: "Indexing could not
complete. Check that the repository is accessible and try again."

### Mistake 11: Displaying checkpoint IDs without the restore command

**Symptom:** "Checkpoint abc123 saved." — the user now has to remember the restore
syntax.  
**Fix:** Every checkpoint confirmation includes the restore command inline:
"Checkpoint abc123 saved. Restore with: :restore abc123"

### Mistake 12: Status bar messages that don't update on state change

**Symptom:** "Pearl: Running" stays in the status bar after the run completes.  
**Fix:** The run completion path MUST update the status bar. This is tested in
TypeScript extension tests.

### Mistake 13: Diff viewer that wraps long lines

**Symptom:** A 200-character SQL query in the diff is word-wrapped, making it
impossible to read as code.  
**Fix:** Diff viewers MUST use `white-space: pre` and `overflow-x: auto`. Code does
not wrap.

### Mistake 14: Not providing a way to see what the approval rejected

**Symptom:** After rejection, the diff disappears and the user cannot review what they
rejected.  
**Fix:** After rejection, the diff MUST remain accessible via `pearl diff --last` or
equivalent for at least the duration of the session.

### Mistake 15: Using progress events for debug information

**Symptom:** Progress output includes lines like "Building tool description string..."
or "Serializing 47 tool entries...".  
**Fix:** Internal implementation details are DEBUG-level logs, never progress events.
Progress events describe what is happening from the user's perspective, not the
implementation's perspective.

### Mistake 16: Inconsistent capitalization in UI copy

**Symptom:** Some buttons say "Apply Changes", others say "apply changes", others
say "Apply changes".  
**Fix:** UI copy follows Title Case for button labels and sentence case for all other
text. "Apply Changes" (button), "Apply these changes?" (prompt sentence).

---

## 16. UX Review Checklist

Use this checklist when reviewing any PR that touches user-facing behavior.

### Design Philosophy Compliance

- [ ] User is the author of record — no autonomously applied writes (approval invariant)
- [ ] Approval screen shows full diff, risk level, and checkpoint ID
- [ ] Default action on every confirmation prompt is the safe one (reject/no)
- [ ] Undo information visible after every approval
- [ ] Assumptions made by Pearl are stated explicitly in progress output

### Language and Terminology

- [ ] Uses canonical vocabulary from Section 2.3 (approve/reject, checkpoint/restore)
- [ ] No jargon or internal exception names in user-facing messages
- [ ] No generic error messages ("something went wrong", "an error occurred")
- [ ] Error messages answer: what happened, why, what to do next
- [ ] No "you did X" — always "Pearl did X" or passive

### Approval Experience

- [ ] Approval screen includes: summary, risk level, per-file breakdown, full diff
- [ ] Risk level computed from patch content (LOW / CAUTION / DANGER)
- [ ] File deletions explicitly called out in approval summary
- [ ] Security-sensitive changes marked with `[!]` in diff
- [ ] Checkpoint ID shown before and after write

### Progress and Feedback

- [ ] All operations > 300ms have a visible progress indicator
- [ ] Progress format: `[step/total] <message>`
- [ ] No progress events that expose implementation details
- [ ] ETA shown only for deterministic operations; LLM calls never have an ETA
- [ ] Run completion shows: steps run, files written, checkpoint ID

### Error Handling

- [ ] Error severity matches actual severity (Info/Warning/Error/Fatal)
- [ ] Canonical error message used for known failure modes (Section 6.4)
- [ ] Technical details available in debug mode, not in primary output
- [ ] Recovery suggestion included with every Error and Fatal message

### Accessibility

- [ ] All interactive elements operable via keyboard only
- [ ] ARIA labels on all icon buttons
- [ ] `aria-live="polite"` on all dynamic content regions
- [ ] Color is not the only means of conveying information
- [ ] Contrast ratio ≥ 4.5:1 for text elements
- [ ] `prefers-reduced-motion` respected in CSS animations

### Visual Design

- [ ] No hard-coded hex colors (VS Code semantic tokens used)
- [ ] Monospace font for all code and diff content
- [ ] `tabular-nums` for numeric columns
- [ ] Long lines in diff use `white-space: pre` with `overflow-x: auto`
- [ ] Extension tested in both Dark+ and Light+ themes

### Performance Perception

- [ ] UI acknowledges every action within 100ms
- [ ] Loading states show skeleton or spinner, not blank content
- [ ] Large diffs (> 500 lines) show warning and are truncated at 2,000
- [ ] Streaming output rendered incrementally, not buffered

### Anti-Pattern Check

- [ ] No hidden destructive actions (file deletions always called out)
- [ ] No blocking dialogs for non-urgent information
- [ ] No double confirmation for the same action
- [ ] No silent failures
- [ ] No generic success messages without specifics
- [ ] No confirmation prompts for read-only actions

---

## 17. Release UX Checklist

Complete this checklist before pushing any release tag. All items must be PASS
before the release is approved.

### Persona Testing (from `03_TESTING_STANDARD.md`, Section 12)

- [ ] Persona 1 (New User): all 6 steps PASS
- [ ] Persona 2 (Daily Developer): all 9 steps PASS
- [ ] Persona 3 (Power User): all 7 steps PASS — including large diff handling
- [ ] Persona 4 (Failure Tester): all 8 steps PASS — safe failure for every scenario

### Approval Flow Verification

- [ ] Approval screen shows correct risk level for LOW, CAUTION, and DANGER patches
- [ ] Checkpoint ID present and correct in approval confirmation
- [ ] Rejection produces clean "No files written" message
- [ ] File deletion highlighted with explicit warning in approval screen
- [ ] Shell command approval shows full command and working directory

### Error Message Review

- [ ] All error messages from Section 6.4 table verified as correct
- [ ] No raw Python exception text in any user-facing output
- [ ] Recovery suggestions present for every Error-level message

### Accessibility Review

- [ ] Extension tested in VS Code High Contrast Black theme: all features legible
- [ ] Extension tested in VS Code High Contrast Light theme: all features legible
- [ ] Full approval and checkpoint flows completed keyboard-only (no mouse)
- [ ] Screen reader smoke test: progress events announced, buttons labeled
- [ ] Reduced-motion preference: all animations suppressed or replaced

### Terminology Audit

- [ ] Search codebase for: "confirm", "accept", "OK" in user-facing strings — verify canonical replacements
- [ ] Search codebase for: "snapshot", "backup", "rollback" in user-facing strings — verify replaced with canonical terms
- [ ] Verify status bar text consistent with Section 12.5 codicon usage

### Performance

- [ ] Approval screen for a 50-file diff renders within 300ms
- [ ] Repository indexing progress updates every ≤ 5 seconds for a 5,000-file repo
- [ ] Cancellation acknowledged within 2 seconds at any step
- [ ] Status bar returns to "Pearl: Ready" within 500ms of run completion

### Theme and Color

- [ ] Full approval flow tested in Dark+ theme: all colors readable
- [ ] Full approval flow tested in Light+ theme: all colors readable
- [ ] Risk level indicator uses both color AND text AND icon (not color only)
- [ ] Diff lines use +/- prefix in addition to color

### Dogfooding Session

- [ ] At least one dogfooding session completed this sprint (see `03_TESTING_STANDARD.md`, Section 13)
- [ ] Any UX friction found during dogfooding is filed as an issue before release

### Sign-off

```
Release UX Checklist — complete before pushing release tag:

[ ] Persona testing: all 4 personas PASS
[ ] Approval flow: all 5 verification items PASS
[ ] Error messages: all PASS
[ ] Accessibility: all 5 items PASS
[ ] Terminology audit: PASS
[ ] Performance: all 5 items PASS
[ ] Theme/color: all 5 items PASS
[ ] Dogfooding session: completed and documented

Reviewer: ______________ Date: __________
```

---

*This document is part of the Pearl Engineering Standards Series.*  
*Previous: [04_SECURITY_GUIDELINES.md](04_SECURITY_GUIDELINES.md)*  
*Next: [06_PERFORMANCE_ENGINEERING.md](06_PERFORMANCE_ENGINEERING.md)*
