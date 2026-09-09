# Pearl — Contributor Guide

**Read this document before making any change to the codebase.**  
It is the only document every contributor must read. Everything else is reference material.

Pearl is a two-process autonomous coding agent. The TypeScript VS Code extension communicates
with a Python backend exclusively over JSON-RPC 2.0 via stdio. Pearl proposes changes;
the developer approves them. Nothing Pearl does autonomously reaches disk without explicit
human approval. That guarantee is the foundation everything else is built on.

A contributor who understands this document can navigate the codebase, make correct changes,
and know when to consult a detailed specification. Reading time: under 15 minutes.

---

## Table of Contents

1. [Before You Write Code](#1-before-you-write-code)
2. [Where Does This Code Belong?](#2-where-does-this-code-belong)
3. [When Do I Ask for Approval?](#3-when-do-i-ask-for-approval)
4. [How Do I Write a Tool?](#4-how-do-i-write-a-tool)
5. [How Do I Change Planning or AI Behavior?](#5-how-do-i-change-planning-or-ai-behavior)
6. [How Do I Call an LLM?](#6-how-do-i-call-an-llm)
7. [How Do I Handle Errors?](#7-how-do-i-handle-errors)
8. [How Do I Write Tests?](#8-how-do-i-write-tests)
9. [When Do I Update a Specification?](#9-when-do-i-update-a-specification)
10. [When Should I Write an ADR?](#10-when-should-i-write-an-adr)
11. [The Five Engineering Principles](#11-the-five-engineering-principles)
12. [Specification Ownership Matrix](#12-specification-ownership-matrix)

---

## 1. Before You Write Code

**Run the tests first.** Before touching anything, confirm the suite is green on your machine.

```bash
pytest tests/ -v
```

If tests are already failing before your change, that is a pre-existing defect. Fix it or
file it — do not let your PR inherit a broken baseline.

**Understand which layer you are working in.** Pearl's Python backend has six layers with
strict dependency rules. Read Section 2 before touching any import. A wrong import direction
is an architecture violation, not a style preference.

**Check the ownership matrix.** If your change touches an approval flow, a security
boundary, a planning loop, or any user-facing interaction, Section 12 tells you which
specification to read before writing code. The specification is the source of truth for
that area's invariants — not your intuition, and not this document.

**Ask the right question about scope.** Pearl's core philosophy is minimal footprint:
do exactly what the task requires and nothing more. Before adding code, ask whether the
change is genuinely required or whether it is an improvement you want to make. Unsolicited
improvements belong in a separate PR with their own rationale.

---

## 2. Where Does This Code Belong?

Pearl's Python backend is organized in six layers. Dependencies flow downward only —
a lower layer never imports from a higher one.

```
Layer 6 — Entry Points     src/main.py, src/mcp/__main__.py
                            Wires everything together. No logic lives here.

Layer 5 — Protocol         src/mcp/server.py
                            JSON-RPC dispatch and stdio framing only.
                            No business logic. No agent state beyond what
                            MCP methods strictly require.

Layer 4 — Agent            src/agent/
                            The execution loop, planner, executor, dispatcher.
                            Owns planning, approval lifecycle, and cancellation.

Layer 3 — Tools            src/tools/
                            Pure tool functions. No imports from Agent or MCP.
                            The only cross-tool imports allowed are the shared
                            @tool decorator and _ensure_within_workspace().

Layer 2 — Infrastructure   src/llm/, src/memory/
                            LLM client abstraction and session memory.

Layer 1 — Foundation       src/config/, src/prompts/
                            Settings and prompt templates. No runtime logic.
```

**Quick placement guide:**

- New file operation → `src/tools/file_tools.py` or a new module in `src/tools/`
- New LLM provider → `src/llm/`
- New agent behaviour (planning, execution, approval) → `src/agent/`
- New MCP method → `src/mcp/server.py` handler only; logic goes in `src/agent/`
- New configuration key → `src/config/settings.py`
- New prompt template → `src/prompts/`

If you find yourself importing from a higher layer, the code is in the wrong place.
Move it rather than adding an import that violates the dependency direction.

The TypeScript extension (`vscode-extension/`) handles UI, diff rendering, and MCP
communication. It does not execute Python or contain agent logic. All tool execution,
file I/O, and LLM calls happen in the Python backend.

For complete details see [`docs/engineering/01_ARCHITECTURE_RULES.md`](docs/engineering/01_ARCHITECTURE_RULES.md).

---

## 3. When Do I Ask for Approval?

This is Pearl's most important invariant. Every autonomous write to the workspace must
pass through `ChangeManager` and be explicitly approved by the user before reaching disk.
This is not a guideline — it is the guarantee Pearl makes to every developer who uses it.

**The three execution modes:**

When a human types a command directly into the CLI or calls `tools/call` explicitly,
Pearl executes the tool and writes directly. The human is already present and aware.

When Pearl runs autonomously — any path through `MCPServer._run_autonomous()` and
`AutonomousExecutor` — every write tool must stage its changes to `ChangeManager`. The
changes wait. The user sees a diff. The user approves. Only then does anything reach disk.

Shell commands follow the same principle via `CommandApprovalManager`. They are not
diffed (shell output is not structured), but they are still staged and approved before
execution in autonomous mode.

**If you are adding a new execution path that calls write tools autonomously, stop.**
Read [`docs/engineering/01_ARCHITECTURE_RULES.md`](docs/engineering/01_ARCHITECTURE_RULES.md) Section 3 before writing a line of code.
A path that writes files without activating `ChangeManager` is a P0 safety violation,
not a performance optimisation.

**The test that must always pass:**

```python
def test_write_file_stages_to_patch_manager_when_active():
    pm = ChangeManager()
    set_active_patch_manager(pm)
    try:
        write_file("test.py", "content")
        assert "test.py" in pm.staged_paths
        assert not Path("test.py").exists()   # not on disk
    finally:
        set_active_patch_manager(None)
```

This test must exist and pass in every sprint. If it fails, nothing else matters.

For complete details see [`docs/engineering/01_ARCHITECTURE_RULES.md`](docs/engineering/01_ARCHITECTURE_RULES.md) Section 3.

---

## 4. How Do I Write a Tool?

Tools are the actions Pearl can take. They live in `src/tools/` and are registered
with the `@tool` decorator. Every tool is a pure function — it does one thing,
takes typed arguments, and returns a result. No tool contains agent logic.

**The mandatory checklist for every tool:**

*Validate paths immediately before I/O.* Call `_ensure_within_workspace(path)` at the
point of use, not at the top of the function. Validating early and using late creates
a window where the validation no longer applies.

```python
# Correct — validate at the point of use
def write_file(path: str, content: str) -> str:
    resolved = _ensure_within_workspace(path)
    resolved.write_text(content, encoding="utf-8")
```

*Use async I/O.* All file reads and writes must use `asyncio.to_thread()`. A synchronous
file operation in a coroutine blocks the event loop for every other concurrent operation.

*Cap search results.* Any tool that returns a list of matches must cap at
`MAX_SEARCH_RESULTS = 200`. Return the cap with a note when results are truncated.
An uncapped search fills the entire LLM context with one result.

*Never use `shell=True`.* Shell commands with variable content go through
`execute_shell` with a list argument — never a formatted string with `shell=True`.

*Respect the approval gate.* Write tools check `get_active_patch_manager()`.
When a patch manager is active, stage — do not write.

*Annotate everything.* All parameters and return types are required. See
[`docs/engineering/02_CODING_STANDARDS.md`](docs/engineering/02_CODING_STANDARDS.md) for the full type annotation rules.

For complete details see [`docs/engineering/01_ARCHITECTURE_RULES.md`](docs/engineering/01_ARCHITECTURE_RULES.md) Section 9
and [`docs/engineering/04_SECURITY_GUIDELINES.md`](docs/engineering/04_SECURITY_GUIDELINES.md) Section 2.

---

## 5. How Do I Change Planning or AI Behavior?

Pearl's reasoning follows four phases every turn: Understand the task, Plan a sequence
of tool calls, Execute them in dependency order, Reflect on whether the goal was achieved.
Changes to planning or agent behaviour touch one or more of these phases. Before making
the change, be clear which phase is affected and why.

**Plans are validated deterministically.** The plan validator is a rule-based function,
not a second LLM call. If you change the plan structure, you must update the validator.
A plan that is not validated is a plan that may contain safety violations that reach
execution.

**Read before write — always.** No plan step that writes a file should be the first step.
A plan that writes to a file Pearl has not read in the current session is operating on
assumptions, not facts. The plan validator enforces this.

**Confidence scoring is computed, not generated.** The confidence score on each plan step
comes from observable factors — whether the target file has been read, whether referenced
symbols exist in the index, whether assumptions are verified. It is not a number the LLM
produces. If you are tempted to have the LLM rate its own confidence, the answer is no.

**The reflection phase is not optional.** A turn that ends after execution without
reflection has not verified that the goal was achieved. All plan steps succeeding does not
mean the goal was met. The reflection phase compares the actual post-execution state
against the stated goal and produces evidence-based COMPLETE, PARTIAL, or FAILED.

**When you change the reasoning loop, add a scripted-LLM integration test.** The
scripted LLM returns a deterministic plan, which makes the execution and reflection
behaviour testable without real API calls. See [`docs/engineering/03_TESTING_STANDARD.md`](docs/engineering/03_TESTING_STANDARD.md)
Section 3.

For complete details see [`docs/engineering/07_AI_ENGINEERING_GUIDELINES.md`](docs/engineering/07_AI_ENGINEERING_GUIDELINES.md).

---

## 6. How Do I Call an LLM?

**Stream the response.** An LLM call that waits for the full completion before returning
anything is a UX violation. The user must see the first token within three seconds of
dispatch. Use the streaming client; emit `pearl/progress` events as chunks arrive.

**The system prompt is static.** It is a version-controlled string in `src/prompts/`.
It is assembled once at session start and cached. It does not incorporate user input,
file content, or runtime state. A dynamic system prompt is a prompt injection surface.

**Wrap workspace content in delimiters.** File content included in the context must be
marked clearly as untrusted user data, not as instructions:

```python
def _format_workspace_file(path: str, content: str) -> str:
    return f"<workspace_file path={path!r}>\n{content}\n</workspace_file>"
```

**Respect the token budget.** The assembled context must not exceed 80% of the model's
context window at the point of dispatch. The remaining headroom is for the response and
defensive margin. The `TokenBudgetManager` enforces this — do not bypass it.

**Mark cacheable content.** For providers that support prompt caching, the system prompt
and tool definitions must be marked cacheable. Failing to mark them means paying input
token costs on every turn for content that does not change.

**Never claim a test passed without running it.** If the completion report says tests
pass, tests ran. "Should pass" is a hallucination. Evidence is required.

For complete details see [`docs/engineering/07_AI_ENGINEERING_GUIDELINES.md`](docs/engineering/07_AI_ENGINEERING_GUIDELINES.md) Sections 6
and 11, and [`docs/engineering/06_PERFORMANCE_ENGINEERING.md`](docs/engineering/06_PERFORMANCE_ENGINEERING.md) Sections 7–10.

---

## 7. How Do I Handle Errors?

**Catch specific exceptions, not bare `except`.** A bare `except Exception` that logs
and continues is hiding a defect, not handling it.

**Classify before recovering.** Transient errors (network timeout, rate limit) are
retried with exponential backoff. Plan failures trigger a replan. Safety violations halt
immediately — they are never retried or replanned around. Task-impossible errors surface
to the user with a specific explanation.

**Never swallow exceptions silently.** A background task that catches an exception and
does nothing has made a problem invisible. Log it at ERROR level with the full context.

**Safety violations are P0.** A path traversal attempt, a dangerous shell command, or
a write that bypasses the approval gate is not a recoverable error — it is a P0 bug
that blocks the sprint. Do not handle it gracefully; surface it loudly.

**Fail closed.** When a security check cannot be performed, deny the operation. A
security check that fails open on error is not a security check.

```python
# Correct — fail closed
def _ensure_within_workspace(path: str) -> Path:
    try:
        resolved = Path(path).resolve()
        if not str(resolved).startswith(str(self._workspace_root.resolve())):
            raise WorkspaceBoundaryError(path)
        return resolved
    except WorkspaceBoundaryError:
        raise
    except Exception as exc:
        raise WorkspaceBoundaryError(f"Cannot resolve {path!r}: {exc}") from exc
```

For complete details see [`docs/engineering/02_CODING_STANDARDS.md`](docs/engineering/02_CODING_STANDARDS.md) Section 5
and [`docs/engineering/04_SECURITY_GUIDELINES.md`](docs/engineering/04_SECURITY_GUIDELINES.md) Section 2.

---

## 8. How Do I Write Tests?

**Unit tests are fast and isolated.** No file system, no LLM, no subprocess. Mock at the
outermost boundary you don't own. All mock objects carry `spec=` so they reject calls to
methods that don't exist on the real object.

**Integration tests use a scripted LLM.** The `ScriptedLLMClient` returns a pre-defined
plan. This makes the executor's behaviour deterministic and testable without API calls or
network access.

**The approval invariant test is not optional.** Every sprint must have and pass the test
that verifies a file is staged but not on disk when a `ChangeManager` is active. See
Section 3 of this document.

**Security tests must exercise real boundaries.** A path traversal test that uses a
mocked `_ensure_within_workspace` proves nothing. Call the real function with a hostile
path and assert the right exception.

**Tests are specifications.** If a test is hard to write, the implementation is probably
wrong. A test that is hard to read will be bypassed when it fails. Both are signals to
fix the design, not to weaken the test.

**Release gates.** A PR that makes any of the following fail does not merge: unit tests,
integration tests, the approval invariant test, security boundary tests. Performance
benchmarks are advisory during development and mandatory before a release tag.

For complete details see [`docs/engineering/03_TESTING_STANDARD.md`](docs/engineering/03_TESTING_STANDARD.md).

---

## 9. When Do I Update a Specification?

Use this test: **if someone changed this without reading the specification, would they
break a safety guarantee or a user-visible contract?**

If yes — update the specification in the same PR. Not afterward. Not in a follow-up.
The specification and the code that implements it must change together.

If no — the change belongs in the code, tests, or an ADR. Do not update a specification
for implementation details: cache sizes, timeout values, algorithm choices, internal
data structures. Those are the code's responsibility.

**Examples that require a specification update:**

- Changing when the approval prompt appears
- Adding a new execution mode that handles write tools differently
- Changing the MCP response shape of an existing method
- Modifying the trust boundary between the extension and the backend
- Adding a new security validation or removing an existing one
- Changing the structure of the planning loop

**Examples that do not require a specification update:**

- Changing a retry count or backoff interval
- Optimising how the index is serialised
- Adjusting a token budget ceiling based on profiling
- Refactoring an internal class that has no external contract

When in doubt, the test above gives the answer.

---

## 10. When Should I Write an ADR?

Write an ADR when you make a decision that has lasting architectural consequences and
where the reasoning would not be obvious to a contributor reading the code a year from now.

**Write an ADR when:**

- You chose between two non-obvious technical approaches and the rejected one was
  reasonable — future contributors will wonder why the other was not used
- You accepted a known limitation or trade-off in exchange for a specific property
- You reversed or superseded a prior architectural decision
- You established a constraint that will affect many future changes

**Do not write an ADR for:**

- Routine implementation choices with obvious rationale
- Changes that are fully explained by the specification they implement
- Minor refactors or performance tweaks

**The process:**

1. Copy `docs/adr/TEMPLATE.md` to `docs/adr/ADR-NNN-short-title.md`
2. Fill in Context, Decision, Alternatives Considered, and Consequences
3. Add the entry to `docs/adr/INDEX.md`
4. If this ADR supersedes a previous one, mark the old entry as superseded in `INDEX.md`
   and note the supersession at the top of the old ADR file

ADRs are immutable once accepted. The decision log is append-only. If a decision changes,
write a new ADR that references and supersedes the old one — do not rewrite history.

---

## 11. The Five Engineering Principles

These principles govern every decision in Pearl's development — from choosing between
two implementations to deciding whether to write a specification.

**1. Establish safety invariants before they are needed.**  
The approval invariant was specified before the first autonomous run, not after a file
was accidentally overwritten. Correctness guarantees that are expensive to retrofit must
be built in from the start. Do not wait for a failure that is itself the harm.

**2. Do not automate until humans repeatedly fail.**  
A governance rule that can be followed manually does not need CI enforcement today.
Automation has a maintenance cost and introduces friction. Add it when evidence shows
the manual process is not working — not as a precaution against a failure you have
not yet observed.

**3. Do not document abstractions that do not exist.**  
A specification for a component that has not been built is speculation dressed as
architecture. Write specifications for things that exist. Let the things that do not
exist yet earn their specification by being built and understood first.

**4. Do not write policies for workflows you have not experienced.**  
Governance that is designed entirely in advance of development will be wrong in
ways that only become visible through use. Design the invariants upfront. Let the
processes that support them evolve from real contributor experience.

**5. Let everything else evolve from evidence.**  
Document consolidation, CI enforcement, ownership matrix expansion, test coverage
thresholds — these decisions are empirical, not architectural. Accumulate experience,
observe where the friction actually is, then act. Do not solve predicted problems.

---

## 12. Specification Ownership Matrix

When your change touches an area in the left column, read the corresponding specification
before writing code. The specification defines the invariants your change must not break.

| If you change… | Read this specification |
|---|---|
| Approval flow, ChangeManager, execution modes | [`01_ARCHITECTURE_RULES.md`](docs/engineering/01_ARCHITECTURE_RULES.md) Section 3 |
| Layer boundaries, module imports, MCP methods | [`01_ARCHITECTURE_RULES.md`](docs/engineering/01_ARCHITECTURE_RULES.md) Sections 1–6 |
| Python or TypeScript code style, async patterns | [`02_CODING_STANDARDS.md`](docs/engineering/02_CODING_STANDARDS.md) |
| Test strategy, release gates, scripted LLM usage | [`03_TESTING_STANDARD.md`](docs/engineering/03_TESTING_STANDARD.md) |
| File path handling, shell commands, secrets, prompt injection | [`04_SECURITY_GUIDELINES.md`](docs/engineering/04_SECURITY_GUIDELINES.md) |
| Approval UX, progress events, error messages, terminology | [`05_UI_UX_GUIDELINES.md`](docs/engineering/05_UI_UX_GUIDELINES.md) |
| Token budgets, caching, async I/O, indexing, benchmarks | [`06_PERFORMANCE_ENGINEERING.md`](docs/engineering/06_PERFORMANCE_ENGINEERING.md) |
| Planning loop, tool selection, reasoning, hallucination prevention | [`07_AI_ENGINEERING_GUIDELINES.md`](docs/engineering/07_AI_ENGINEERING_GUIDELINES.md) |

This matrix covers the specifications that exist today. It will grow as new specifications
are added. If your change touches an area that is not listed, use the test from Section 9
to decide whether a specification update is needed.

---

*Pearl Engineering Standards Series — [`docs/engineering/`](docs/engineering/)*  
*Detailed specifications are the source of truth. This document is the entry point.*
