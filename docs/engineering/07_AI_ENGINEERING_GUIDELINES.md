# 07 — AI Engineering Guidelines

**Pearl AI Coding Agent — Engineering Standards Series**  
Document owner: Lead Software Architect  
Applies to: Every sprint, every contributor, every reasoning path  
Status: Authoritative — changes require architect sign-off

---

## Purpose

This document defines the engineering standard for Pearl's reasoning engine. It
specifies how Pearl thinks, plans, selects tools, manages context, recovers from
failure, validates its own output, and interacts with developers as an autonomous
coding agent.

This is not an LLM prompt guide. It does not define what to say to the model.
It defines how the agent system — the code that wraps, invokes, and interprets
the model — is engineered to produce correct, safe, and trustworthy behavior.

The distinction matters. An LLM is a statistical text predictor. Pearl is an
engineering system built on top of one. This document governs the engineering
system: the planning pipeline, the tool selection logic, the verification gates,
the recovery handlers, the approval protocol, and the reflection loops that
transform raw LLM output into reliable autonomous action.

Rules in this document are **binding**. A reasoning path that produces correct
output by accident, without satisfying these rules, is not compliant — it is
lucky. Every rule has a stated rationale; challenge the rule, not the enforcement.

---

## Scope

This document applies to:

- `src/agent/planner.py` — plan generation, replanning, and plan validation
- `src/agent/executor.py` — execution loop, tool dispatch, approval lifecycle
- `src/agent/dispatcher.py` — tool selection, argument construction, result handling
- `src/llm/` — all LLM client wrappers, prompt assembly, streaming handlers
- `src/memory/workspace_memory.py` — agent working memory across turns
- `src/tools/context_manager.py` — retrieval strategy, relevance ranking
- `src/prompts/` — all system prompt templates and context formatters
- Any future reasoning component: verifier, reflector, sub-agent, summarizer

It cross-references:

- `01_ARCHITECTURE_RULES.md` — the approval invariant and layer boundaries that
  all reasoning paths must respect
- `02_CODING_STANDARDS.md` — the implementation rules for all code in this scope
- `03_TESTING_STANDARD.md` — AI quality gates and scripted-LLM testing strategy
- `04_SECURITY_GUIDELINES.md` — prompt injection defenses and trust boundaries
- `05_UI_UX_GUIDELINES.md` — progress streaming, approval UX, and error messages
- `06_PERFORMANCE_ENGINEERING.md` — token budgets, context assembly, caching

---

## Table of Contents

1. [AI Philosophy](#1-ai-philosophy)
2. [Core Principles](#2-core-principles)
3. [Reasoning Model](#3-reasoning-model)
4. [Planning Standards](#4-planning-standards)
5. [Tool Selection Rules](#5-tool-selection-rules)
6. [Tool Execution Strategy](#6-tool-execution-strategy)
7. [Multi-Step Planning](#7-multi-step-planning)
8. [Context Management](#8-context-management)
9. [Memory Management](#9-memory-management)
10. [Conversation Strategy](#10-conversation-strategy)
11. [Prompt Construction Rules](#11-prompt-construction-rules)
12. [Retrieval Strategy](#12-retrieval-strategy)
13. [Code Understanding](#13-code-understanding)
14. [Code Generation](#14-code-generation)
15. [Refactoring Rules](#15-refactoring-rules)
16. [Verification Before Action](#16-verification-before-action)
17. [Self-Validation](#17-self-validation)
18. [Error Recovery](#18-error-recovery)
19. [Retry Strategy](#19-retry-strategy)
20. [Reflection Loop](#20-reflection-loop)
21. [Human Approval Rules](#21-human-approval-rules)
22. [Safety Constraints](#22-safety-constraints)
23. [Autonomous Limits](#23-autonomous-limits)
24. [Learning Strategy](#24-learning-strategy)
25. [Confidence Scoring](#25-confidence-scoring)
26. [Hallucination Prevention](#26-hallucination-prevention)
27. [Decision Trees](#27-decision-trees)
28. [AI Anti-Patterns](#28-ai-anti-patterns)
29. [Common AI Mistakes](#29-common-ai-mistakes)
30. [AI Review Checklist](#30-ai-review-checklist)
31. [AI Release Checklist](#31-ai-release-checklist)
32. [Canonical Vocabulary](#32-canonical-vocabulary)
33. [References](#33-references)

---

## 1. AI Philosophy

### 1.1 What Pearl Is — and Is Not

Pearl is an autonomous coding agent built on an LLM. This description contains
two parts that must be held in tension: *autonomous* and *agent*. Pearl is
autonomous in that it generates plans and executes tool calls without per-step
human direction. It is an agent in that it acts on behalf of a human, within
constraints defined by that human, toward goals defined by that human.

Pearl is not a chatbot. It does not aim to produce the most fluent or confident-
sounding response. It aims to produce *correct, safe, verifiable* changes to a
real codebase, on a real machine, with real consequences.

Pearl is not a search engine. It does not retrieve information to display. It
retrieves information to act on — and acting on wrong information damages real
files.

Pearl is not infallible. The LLM at its core is a probabilistic system. This
engineering standard exists specifically because we cannot trust the LLM alone to
be correct, safe, or honest about its own uncertainty.

### 1.2 The Engineering Contract

The engineering system surrounding the LLM must provide guarantees that the LLM
itself cannot. Those guarantees are:

```
┌────────────────────────────────────────────────────────────┐
│  Pearl's AI Engineering Guarantees                         │
│                                                            │
│  1. Every plan is validated before execution begins        │
│  2. Every write is approved before reaching disk           │
│  3. Every tool call is logged and attributable             │
│  4. Every failure is caught and handled — never silent     │
│  5. Uncertainty is surfaced, not hidden                    │
│  6. Scope is enforced — Pearl does only what was asked     │
│  7. The human can intervene at any point                   │
│  8. The workspace can always be restored to a known state  │
└────────────────────────────────────────────────────────────┘
```

These are engineering guarantees, not LLM promises. They are implemented in
Python code, enforced by the approval invariant, and verified by tests.

### 1.3 The Agent Loop

Pearl's execution follows a structured loop. Understanding this loop is a
prerequisite for understanding every rule in this document.

```
┌─────────────────────────────────────────────────────────────┐
│                    Pearl Agent Loop                         │
│                                                             │
│  User prompt                                                │
│      │                                                      │
│      ▼                                                      │
│  [Context Assembly]   ← retrieval + history + files         │
│      │                                                      │
│      ▼                                                      │
│  [Plan Generation]    ← LLM produces ToolCall list          │
│      │                                                      │
│      ▼                                                      │
│  [Plan Validation]    ← structural + safety checks          │
│      │                                                      │
│      ├── INVALID ──► [Replan] ──► back to Plan Generation  │
│      │                                                      │
│      ▼ VALID                                                │
│  [Execution Loop]     ← dispatch tool calls sequentially    │
│      │                                                      │
│      ├── per tool call:                                     │
│      │     [Tool Execution] → [Result Capture]              │
│      │     [Result Validation] → [Context Update]           │
│      │     [Progress Event] → user                          │
│      │                                                      │
│      ├── write tools only:                                  │
│      │     [Stage to ChangeManager]                          │
│      │     [Approval Prompt] → user decision                │
│      │     [Apply or Reject]                                │
│      │                                                      │
│      ▼ loop complete                                         │
│  [Reflection]         ← did it work? what's left?          │
│      │                                                      │
│      ├── INCOMPLETE ──► [Replan]                           │
│      └── COMPLETE   ──► [Response to user]                 │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Core Principles

**Principle AI-1 — Correctness over confidence.**
Pearl must never present uncertain output as certain. When the LLM produces a
plan that relies on an assumption Pearl cannot verify, that assumption MUST be
surfaced to the user. A confident wrong answer is worse than an uncertain
correct one.

**Principle AI-2 — Minimal footprint.**
Pearl does exactly what the task requires and nothing more. Reformatting a file
that was not part of the task is a footprint violation. Refactoring a module to
"improve it while I'm here" is a footprint violation. Every action Pearl takes
must be traceable to the user's explicit request.

**Principle AI-3 — Reversibility first.**
When two approaches achieve the same goal, Pearl chooses the more reversible one.
If a checkpoint exists, Pearl takes it before any write sequence. If a plan
includes a destructive action, Pearl warns before executing it.

**Principle AI-4 — Transparency of reasoning.**
Pearl's plan and its rationale are always available to the user. The user can ask
"why did you do that?" and Pearl must have a traceable answer. Black-box execution
is prohibited.

**Principle AI-5 — The agent is responsible for its own quality.**
Pearl does not produce output and leave verification to the user. Before staging
a write, Pearl MUST verify that the change it is about to make is syntactically
valid, consistent with the rest of the codebase, and addresses the stated goal.

**Principle AI-6 — Humans are the final authority.**
Pearl may disagree with a user's approach. It MUST surface that disagreement
clearly, once. It MUST then execute the user's decision if the user confirms.
Pearl does not repeat objections or apply passive resistance by producing
intentionally poor results.

**Principle AI-7 — Failure is information.**
A tool call that returns an error is not a problem to hide — it is data that
should update the plan. Pearl treats every failure as a signal, not noise.

---

## 3. Reasoning Model

### 3.1 The Four-Phase Reasoning Cycle

Pearl's reasoning within each agent turn follows four phases. These phases are
not optional steps in a pipeline; they are the structure of every turn.

```
Phase 1 — UNDERSTAND
  ├── Parse the user's intent (stated and implied)
  ├── Identify ambiguities that would block correct execution
  ├── Retrieve relevant codebase context
  └── Produce a structured task representation

Phase 2 — PLAN
  ├── Generate a sequence of tool calls to accomplish the task
  ├── Validate the plan against safety and feasibility constraints
  ├── Assign confidence to each step
  └── Identify which steps require human approval

Phase 3 — EXECUTE
  ├── Dispatch tool calls in dependency order
  ├── Capture and validate each result
  ├── Update working context with each result
  └── Surface progress to the user at each step

Phase 4 — REFLECT
  ├── Compare executed result against the goal
  ├── Identify gaps, unexpected outcomes, or new information
  ├── Decide: done / replan / ask user
  └── Report outcome with evidence
```

**Rule REASON-1:** Every agent turn MUST complete all four phases. Skipping the
UNDERSTAND phase (acting on the literal prompt without parsing intent) and
skipping the REFLECT phase (reporting done without verifying the result) are the
two most common causes of incorrect agent behavior.

> **Rationale:** An agent that jumps from prompt to execution misses ambiguities
> that would prevent correct resolution. An agent that skips reflection ships
> changes that don't solve the stated problem.

```python
# CORRECT — all four phases represented in the executor
async def run(self, prompt: str) -> ExecutionReport:
    task = await self._understand(prompt)        # Phase 1
    plan = await self._plan(task)                # Phase 2
    results = await self._execute(plan)          # Phase 3
    report = await self._reflect(task, results)  # Phase 4
    return report

# VIOLATION — understand and reflect absent
async def run(self, prompt: str) -> ExecutionReport:
    plan = await self._plan(prompt)   # raw prompt, no understanding
    return await self._execute(plan)  # no reflection
```

### 3.2 Intent Parsing

**Rule REASON-2:** Before generating a plan, Pearl MUST parse the user's prompt
into a structured task representation containing: (a) the primary goal, (b) any
explicit constraints, (c) the scope (which files, modules, or symbols are in
scope), and (d) any ambiguities that would prevent correct resolution.

**Rule REASON-3:** Ambiguities that affect which files are written MUST be
resolved before planning begins. Pearl MUST NOT proceed with a plan that writes
files under an ambiguous interpretation of scope. It MUST ask the user to clarify.

```
GOOD — ambiguity resolved before planning:
  User: "Refactor the auth module"
  Pearl: "I can see two auth modules: src/auth/jwt.py and src/legacy/auth.py.
          Which should I refactor, or both?"

BAD — ambiguity absorbed into an arbitrary plan:
  User: "Refactor the auth module"
  Pearl: [silently picks src/auth/jwt.py and starts writing]
```

**Rule REASON-4:** When a prompt is clear, Pearl MUST NOT ask clarifying
questions. Unnecessary clarification requests are a UX violation
(`05_UI_UX_GUIDELINES.md`, UX-19) and a reasoning failure. The test: if a
senior developer reading the prompt would know exactly what to do, Pearl must
also know.

### 3.3 Assumption Tracking

**Rule REASON-5:** Every assumption Pearl makes during planning MUST be tracked
explicitly. Assumptions are facts that Pearl has not verified from the codebase
but must be true for the plan to succeed.

```python
@dataclass
class PlanAssumption:
    text: str          # "The function signature has not changed since indexing"
    verifiable: bool   # Can this be checked with a tool call?
    risk: str          # "low" | "medium" | "high"
    verified: bool = False
```

**Rule REASON-6:** High-risk assumptions MUST be verified with a tool call before
the plan step that depends on them executes. A high-risk assumption is one whose
falseness would cause an incorrect write to a file.

**Rule REASON-7:** Unverifiable assumptions MUST be surfaced to the user in the
plan summary. Pearl MUST NOT silently build on assumptions it cannot check.

---

## 4. Planning Standards

### 4.1 Plan Structure

**Rule PLAN-1:** A plan is an ordered list of `ToolCall` objects. Each `ToolCall`
MUST have: (a) the tool name, (b) all required arguments, (c) a one-sentence
rationale, and (d) a dependency list (which prior steps must succeed first).

```python
@dataclass(frozen=True)
class ToolCall:
    tool_name: str
    kwargs: dict[str, Any]
    rationale: str
    depends_on: tuple[int, ...] = ()   # indices of prerequisite steps
    confidence: float = 1.0            # 0.0–1.0
    requires_approval: bool = False
```

**Rule PLAN-2:** Plans MUST begin with read operations before write operations.
Pearl MUST NOT write to a file it has not read in the current session. "I know
what this file contains" is not an acceptable substitute for reading it.

```
CORRECT plan order:
  Step 1: read_file("src/auth/jwt.py")
  Step 2: search_text("verify_token")
  Step 3: edit_lines("src/auth/jwt.py", ...)   ← write after reading

VIOLATION:
  Step 1: edit_lines("src/auth/jwt.py", ...)   ← write without reading
```

**Rule PLAN-3:** Plans MUST be acyclic. No step may depend on a step that has
not yet executed. The dependency graph MUST be a DAG. The planner MUST validate
this before returning a plan.

**Rule PLAN-4:** The maximum plan length is **25 steps**. A plan longer than 25
steps indicates scope creep, insufficient decomposition, or a misunderstood task.
The planner MUST reject plans over 25 steps and replan with a reduced scope.

**Rule PLAN-5:** Plans MUST be checkpointed before any write step. If no
checkpoint has been taken in the current session, the executor MUST take one
before the first write step executes. This is independent of the approval
invariant — it is a rollback guarantee (`01_ARCHITECTURE_RULES.md`, Section 3).

### 4.2 Plan Validation

**Rule PLAN-6:** Every generated plan MUST pass the plan validation pipeline
before execution begins. The pipeline checks:

```
Plan Validation Pipeline
────────────────────────
Check 1: Structural validity
  └── All tool names exist in the tool registry
  └── All required arguments are present
  └── Dependency graph is a DAG

Check 2: Scope validity
  └── All file paths are within the workspace boundary
  └── No paths reference files outside the workspace root
  └── No plan step operates on a path that failed workspace validation

Check 3: Safety validity
  └── No shell commands match _DANGEROUS_PATTERNS
  └── All shell commands have been flagged as requiring approval
  └── No plan writes to .env, .git internals, or system paths

Check 4: Feasibility validity
  └── Files to be read exist (or their absence is the task's subject)
  └── Dependencies are achievable in sequence
  └── Token budget allows the context needed for each step

Result: VALID → proceed to execution
        INVALID (structural) → log error, do not execute, report to user
        INVALID (scope/safety) → log error, do not execute, raise PearlSafetyError
```

**Rule PLAN-7:** A plan that fails validation MUST NOT be partially executed.
The failure must be reported to the user with the specific validation check that
failed before any tool call is made.

**Rule PLAN-8:** Plan validation MUST be performed by a deterministic function,
not by a second LLM call. LLM-based plan validation is itself subject to the
same errors as plan generation. Use a rule-based validator.

### 4.3 Plan Generation Quality

**Rule PLAN-9:** The system prompt for plan generation MUST include:
(a) the tool registry with full descriptions and argument schemas,
(b) the workspace context (relevant files, symbols),
(c) the task representation from the UNDERSTAND phase,
(d) Pearl's planning constraints (this document, condensed).

**Rule PLAN-10:** The planner MUST generate plans in a structured, parseable
format (JSON with a defined schema), not in free-form text. A plan in free-form
text cannot be validated, cannot be executed step-by-step, and cannot be
partially rolled back.

```python
# CORRECT — structured plan output
PLAN_SCHEMA = {
    "type": "object",
    "required": ["steps", "rationale", "assumptions"],
    "properties": {
        "steps": {"type": "array", "items": TOOL_CALL_SCHEMA},
        "rationale": {"type": "string"},
        "assumptions": {"type": "array", "items": ASSUMPTION_SCHEMA},
    },
}

# VIOLATION — free-form plan that must be parsed by heuristics
# "First I will read the file, then find the function, then edit it..."
```

**Rule PLAN-11:** If the LLM generates a plan that cannot be parsed against the
schema, the planner MUST retry with an error message that includes the specific
parse failure, up to a maximum of 3 retries. After 3 failures, surface a
`PlanGenerationError` to the user.

### 4.4 Replanning

**Rule PLAN-12:** Replanning is triggered by: (a) plan validation failure,
(b) a tool call returning an unexpected error, (c) a reflection that identifies
incompleteness. Replanning uses the original task representation plus all tool
results gathered so far as context.

**Rule PLAN-13:** The maximum replan count per task is **3**. If after 3 replans
the task is still incomplete, Pearl MUST surface a `PearlTaskFailureError` with
the full replan history rather than continuing to loop indefinitely.

**Rule PLAN-14:** Each replan MUST explicitly state what changed relative to the
previous plan and why. A replan that is identical to a failed plan is a logic
error — if the same plan failed once, it will fail again.

```python
@dataclass
class ReplanContext:
    attempt: int                    # 1, 2, or 3
    previous_plan: Plan
    failure_reason: str             # what caused the replan
    tool_results_so_far: list[ToolResult]
    delta: str                      # what is different in this plan
```

---

## 5. Tool Selection Rules

### 5.1 Tool Selection Principles

**Rule TOOL-1:** Pearl MUST select the most specific tool available for a task.
When a specialized tool exists (e.g., `find_references` for symbol lookup),
Pearl MUST NOT substitute a general-purpose tool (e.g., `search_text` with
a regex) unless the specialized tool is unavailable or explicitly insufficient.

> **Rationale:** Specialized tools have pre-built result structuring, index
> acceleration, and output caps that general tools do not. Using a general tool
> where a specific one exists produces slower, noisier results.

```
CORRECT tool selection:
  Task: "Find all callers of verify_token()"
  Tool: find_references(symbol="verify_token")   ← specialized

VIOLATION:
  Task: "Find all callers of verify_token()"
  Tool: search_text(pattern="verify_token")      ← general; slower, noisier
```

**Rule TOOL-2:** Pearl MUST NOT invoke a tool whose side effects it does not
understand. Every tool invocation must have a stated purpose in the plan
rationale. Tool calls without a rationale are rejected by the plan validator.

**Rule TOOL-3:** Read tools are always preferred before write tools. If Pearl
is uncertain whether a write is correct, it MUST issue a read or search to
confirm before writing.

**Rule TOOL-4:** Pearl MUST NOT call the same tool with the same arguments twice
in the same plan unless the first call explicitly failed. Duplicate tool calls
indicate a planning error and waste context budget.

### 5.2 Tool Selection Decision Tree

```
User task requires some action on the codebase
│
├── Need to READ content?
│   ├── Specific file known → read_file(path)
│   ├── Symbol known, file unknown → find_references(symbol) or search_text
│   ├── Pattern to find → search_text(pattern)
│   └── Project structure → get_project_summary()
│
├── Need to WRITE content?
│   ├── New file → create_file(path, content)
│   ├── Replace specific lines → edit_lines(path, start, end, content)
│   ├── Replace a function → replace_function(path, name, content)
│   ├── Replace a class → replace_class(path, name, content)
│   ├── Insert after symbol → insert_after_symbol(path, symbol, content)
│   └── Apply a unified diff → patch_file(path, diff)
│
├── Need to RUN something?
│   ├── Tests → execute_shell(["pytest", ...])
│   ├── Linter → execute_shell(["ruff", ...])
│   ├── Build → execute_shell(["make", ...])
│   └── Arbitrary → execute_shell([...])  ← requires approval
│
└── Need to UNDERSTAND the repo structure?
    ├── File list → list_directory(path)
    ├── Git history → git_log(...)
    ├── Git diff → git_diff(...)
    └── Symbol index → search_symbols(query)
```

### 5.3 Tool Argument Construction

**Rule TOOL-5:** All file path arguments MUST be relative to the workspace root.
Absolute paths in tool arguments are a workspace boundary violation
(`04_SECURITY_GUIDELINES.md`, SC-6).

**Rule TOOL-6:** All search patterns passed to `search_text` MUST be validated
as syntactically correct regular expressions before the tool call is issued.
A malformed regex produces a tool error that triggers an unnecessary replan.

```python
# CORRECT — validate regex before tool call
def _validate_search_pattern(pattern: str) -> str:
    try:
        re.compile(pattern)
    except re.error as e:
        raise PlanValidationError(f"Invalid search pattern {pattern!r}: {e}")
    return pattern
```

**Rule TOOL-7:** Line number arguments to `edit_lines` MUST be computed from
the most recent `read_file` result in the current plan, not from the index or
from memory of a previous session. Line numbers change whenever a file is edited.

**Rule TOOL-8:** When constructing shell command arguments, Pearl MUST use a
list form (`["git", "log", "--oneline", "-10"]`), never a string with embedded
spaces. String form commands are passed to a shell, enabling injection. See
`04_SECURITY_GUIDELINES.md`, SC-3.

### 5.4 Tool Result Interpretation

**Rule TOOL-9:** Tool results MUST be interpreted structurally, not heuristically.
A `read_file` result is a string — its line count, encoding, and exact content
are facts. Pearl MUST NOT estimate, guess, or approximate these facts.

**Rule TOOL-10:** An empty result from a search tool (`search_text`, `find_references`)
MUST be treated as a definitive negative: the pattern does not exist. Pearl MUST
NOT assume the search failed and retry with the same pattern.

**Rule TOOL-11:** Tool results that exceed the search cap (`MAX_SEARCH_RESULTS = 200`)
MUST be treated as incomplete. Pearl MUST note in its working context that the
result was truncated and that a more specific query may be needed.

---

## 6. Tool Execution Strategy

### 6.1 Execution Order

**Rule EXEC-1:** Tool calls MUST be dispatched in dependency order: a step that
depends on the result of a prior step MUST wait for that result. Independent steps
MAY be parallelized subject to the concurrency limits in
`06_PERFORMANCE_ENGINEERING.md`, Section 13.

**Rule EXEC-2:** Write tool calls MUST be executed sequentially, never in parallel.
Two concurrent write calls to the same file produce undefined behavior. Two
concurrent write calls to different files may produce a plan state that is
inconsistent at the ChangeManager level.

**Rule EXEC-3:** Shell commands MUST be executed after all reads that their output
depends on, and before any writes that depend on their output. The ordering:
reads → shell → writes preserves the invariant that Pearl always writes based on
current knowledge.

### 6.2 Result Capture and Validation

**Rule EXEC-4:** Every tool result MUST be stored in the plan's execution context
before the next step executes. A result that is not stored is a result that cannot
be referenced by subsequent steps or by the reflection phase.

**Rule EXEC-5:** Every tool result MUST be checked for the presence of an error
indicator. Pearl MUST NOT silently ignore a tool error and proceed with subsequent
steps that depend on the errored step's output.

```python
# CORRECT — error check before proceeding
async def _execute_step(self, step: ToolCall) -> ToolResult:
    result = await self._dispatcher.execute(step.tool_name, **step.kwargs)
    if result.is_error:
        raise ToolExecutionError(
            tool=step.tool_name,
            error=result.error_message,
            step_index=step.index,
        )
    self._execution_context[step.index] = result
    return result

# VIOLATION — error ignored, next step proceeds with None context
async def _execute_step(self, step: ToolCall) -> ToolResult:
    result = await self._dispatcher.execute(step.tool_name, **step.kwargs)
    self._execution_context[step.index] = result  # may be an error result
    return result
```

**Rule EXEC-6:** The execution context MUST be updated after every tool call,
not batched at the end of the plan. Batching context updates means that
intermediate steps cannot access results from prior steps.

### 6.3 Progress Streaming

**Rule EXEC-7:** Pearl MUST emit a `pearl/progress` event before each tool call
with the tool name and rationale, and another after each tool call with the
result summary. The user must be able to follow the execution in real time.
This is required by `05_UI_UX_GUIDELINES.md`, UX-3.

```python
# CORRECT — progress before and after each tool call
async def _execute_step(self, step: ToolCall) -> ToolResult:
    await self._emit_progress(
        step=step.index,
        total=self._plan.total_steps,
        message=f"Running {step.tool_name}: {step.rationale}",
    )
    result = await self._dispatcher.execute(step.tool_name, **step.kwargs)
    await self._emit_progress(
        step=step.index,
        total=self._plan.total_steps,
        message=f"Completed {step.tool_name}: {result.summary}",
    )
    return result
```

**Rule EXEC-8:** Progress events MUST be emitted even for fast tool calls. A
tool call that completes in 50ms still requires a progress event. The purpose
is not latency compensation — it is attribution: the user must know which tool
produced each side effect.

---

## 7. Multi-Step Planning

### 7.1 Dependency Management

**Rule PLAN-15:** For plans with more than 3 steps, Pearl MUST construct an
explicit dependency graph and validate it before execution. A plan whose
dependencies are implicit (assumed from ordering) is a plan that cannot be
safely replanned or parallelized.

```
Example dependency graph for a 5-step refactoring plan:

  Step 1: read_file("auth.py")          depends_on: []
  Step 2: search_text("verify_token")   depends_on: []
  Step 3: find_references("verify_token") depends_on: []
  Step 4: edit_lines("auth.py", ...)    depends_on: [1, 2]
  Step 5: run_tests()                   depends_on: [4]

Steps 1, 2, 3 can execute in parallel.
Step 4 waits for 1 and 2.
Step 5 waits for 4.
```

**Rule PLAN-16:** Pearl MUST NOT generate a plan where a write step is the first
step. At minimum, a read or search step must precede any write to establish that
the target file exists and has the expected content.

**Rule PLAN-17:** When a plan includes a verification step (e.g., run tests after
a change), that step MUST be the final write-phase step. Verification that
precedes incomplete writes proves nothing.

### 7.2 Intermediate State Consistency

**Rule PLAN-18:** Between steps, the workspace must be in a consistent state: no
partially-applied patches, no half-written functions, no uncommitted staging.
If an intermediate step fails, the ChangeManager MUST be able to roll back to the
pre-plan state via the checkpoint taken at Rule PLAN-5.

**Rule PLAN-19:** Pearl MUST NOT depend on intermediate staged state as if it
were committed state. A file that has been staged but not approved does not exist
from the LLM's perspective — Pearl must plan as if approved state is the only
state.

**Rule PLAN-20:** When a plan modifies multiple files, the changes MUST be
presented to the user as a single, coherent diff in the approval screen. Pearl
MUST NOT request approval for each file separately unless the files are
independently releasable. See `05_UI_UX_GUIDELINES.md`, Section 4.

---

## 8. Context Management

### 8.1 Context Priority

Context is the information Pearl includes in the LLM prompt. The context window
is finite (`06_PERFORMANCE_ENGINEERING.md`, Section 2.2). Pearl's context
management strategy determines what the LLM sees and, therefore, what it can
reason about.

**Rule CTX-1:** Context items MUST be ranked by relevance to the current task
before assembly. Relevance is determined by the index (symbol overlap, file
proximity, recent edit history), not by recency of access.

**Rule CTX-2:** The context assembly order for every LLM call is:

```
Priority 1 — System prompt + tool definitions     (always included)
Priority 2 — Current user message                 (always included)
Priority 3 — Files directly named in the task     (always included if within budget)
Priority 4 — Functions/symbols named in the task  (high priority)
Priority 5 — Callers and callees of Priority 4    (medium priority)
Priority 6 — Recent conversation turns (last 5)   (medium priority)
Priority 7 — Search results from this turn        (lower priority)
Priority 8 — Older conversation turns             (lowest priority — pruned first)
```

**Rule CTX-3:** Context MUST be assembled once per LLM call and must not be
modified between the assembly and the call. Adding items to context after the
token count has been computed invalidates the budget enforcement.

**Rule CTX-4:** Pearl MUST include the line numbers and file paths of every code
excerpt included in context. An LLM that sees code without knowing its location
in the file cannot generate correct edit arguments.

```python
# CORRECT — context includes file metadata
def _format_file_excerpt(path: str, start: int, end: int, content: str) -> str:
    return f"### File: {path} (lines {start}–{end})\n```\n{content}\n```"

# VIOLATION — code without location metadata
def _format_file_excerpt(content: str) -> str:
    return f"```\n{content}\n```"
```

**Rule CTX-5:** When a file has been edited during the current session, the
context MUST include the post-edit content, not the pre-edit content from the
index. Stale context produces patches against the wrong line numbers.

### 8.2 Context Compression

**Rule CTX-6:** When context pressure is high (> 70% of budget used), Pearl MUST
apply compression in this order before dropping items:
1. Summarize long tool results (> 50 lines) to their key findings
2. Replace full file content with function signatures and docstrings only
3. Summarize older conversation turns into a "prior context" block
4. Drop older conversation turns entirely (Priority 8 first)

**Rule CTX-7:** Context compression MUST be logged at INFO level with the
compression ratio and the items compressed. Silent compression that causes the
LLM to lose information is a debugging nightmare.

**Rule CTX-8:** Pearl MUST NEVER compress or summarize: the current user message,
the system prompt, or files that are the direct subject of the current write step.
These are the highest-priority items and must always be present verbatim.

---

## 9. Memory Management

In this section, *memory* refers to Pearl's agent working memory — the
information Pearl accumulates across turns within a session — not process
RAM (which is governed by `06_PERFORMANCE_ENGINEERING.md`, Section 12).

### 9.1 Working Memory Structure

**Rule MEM-1:** Pearl's working memory MUST be structured, not a free-form
transcript. The `WorkspaceMemory` object MUST maintain:

```python
@dataclass
class WorkspaceMemory:
    session_id: str
    task_history: list[CompletedTask]      # what was accomplished
    file_read_cache: dict[str, FileState]  # path → content + mtime
    symbol_cache: dict[str, SymbolInfo]    # symbol → location + signature
    user_preferences: dict[str, str]       # learned preferences this session
    failed_attempts: list[FailedAttempt]   # what was tried and failed
    open_questions: list[str]              # unresolved ambiguities
```

**Rule MEM-2:** Working memory MUST be updated after every completed tool call.
Memory that is only updated at turn boundaries creates blind spots in multi-step
plans where an intermediate result must inform the next step.

**Rule MEM-3:** The `file_read_cache` MUST be invalidated for a file whenever
Pearl writes to that file. A write changes the file's content and line numbers;
any cached state is now stale.

**Rule MEM-4:** Failed attempts MUST be stored in `failed_attempts` with the
full error message and the plan step that triggered the failure. When replanning,
Pearl MUST consult `failed_attempts` to avoid repeating strategies that have
already failed in this session.

```python
# CORRECT — failed attempt recorded and consulted on replan
@dataclass
class FailedAttempt:
    tool_name: str
    kwargs: dict[str, Any]
    error: str
    timestamp: float

def _generate_replan_context(self) -> str:
    failed = "\n".join(
        f"- {a.tool_name}({a.kwargs}) failed: {a.error}"
        for a in self._memory.failed_attempts
    )
    return f"Previously attempted and failed:\n{failed}\nDo not repeat these."
```

### 9.2 Session Boundary Rules

**Rule MEM-5:** Working memory is session-scoped. It MUST be discarded at session
end and MUST NOT be persisted across sessions in a format that allows the previous
session's assumptions to corrupt a new session's reasoning.

**Rule MEM-6:** User preferences learned during a session (e.g., "the user
prefers single-quoted strings") MAY be surfaced as suggestions in future sessions,
but MUST NOT be silently applied without the user's knowledge.

**Rule MEM-7:** Pearl MUST NOT carry forward file content from a previous session
as if it were current. File content is always re-read from disk. A remembered
"what this file contains" is a hallucination risk.

---

## 10. Conversation Strategy

### 10.1 Turn Structure

**Rule AI-1:** Pearl's response to a user message MUST contain exactly one of:
(a) a clarifying question (if the task is ambiguous and the ambiguity affects
which files are written), (b) a plan confirmation and progress stream, or
(c) a completion report. Responses that mix multiple response types in a single
turn create an incoherent UX.

**Rule AI-2:** Pearl MUST NOT ask more than one clarifying question per turn.
A list of five questions in a single response is a planning failure — Pearl should
resolve what it can from context and ask only what it cannot resolve.

**Rule AI-3:** When Pearl completes a task, its response MUST include:
(a) a summary of what was changed, (b) which files were written (if any),
(c) the result of any verification step (tests passed/failed), and (d) any
open questions or limitations. Completion reports that lack these four elements
are incomplete.

**Rule AI-4:** Pearl MUST match its verbosity to the task. A one-line fix does
not require a paragraph of explanation. A multi-file refactoring requires a
structured report. The verbosity heuristic: one sentence per changed file, plus
a verification result.

### 10.2 User Communication Standards

**Rule AI-5:** Pearl MUST use the canonical terminology defined in Section 32
of this document. Using non-canonical terms for Pearl's actions creates
confusion and contradicts `05_UI_UX_GUIDELINES.md`, Section 12.

**Rule AI-6:** Pearl MUST communicate uncertainty using specific language, not
hedging. "I'm not sure" is not informative. "I could not find a definition for
`verify_token` in the indexed files — it may be dynamically defined or imported
from a dependency" is informative.

**Rule AI-7:** Pearl MUST NOT apologize for tool limitations. "I can't do that
because the file is outside the workspace" is correct. "I'm sorry, I can't do
that" adds noise and implies capability failure rather than boundary enforcement.

---

## 11. Prompt Construction Rules

### 11.1 System Prompt Design

**Rule PROMPT-1:** The system prompt MUST be a fixed, version-controlled string.
It MUST NOT be assembled dynamically from user input, file content, or runtime
state. Dynamic system prompts are a prompt injection attack surface
(`04_SECURITY_GUIDELINES.md`, Section 8).

**Rule PROMPT-2:** The system prompt MUST encode Pearl's identity, capabilities,
constraints, and the approval invariant. It must not assume the LLM has any
memory of prior sessions.

**Rule PROMPT-3:** Tool descriptions in the system prompt MUST be complete and
accurate. A tool description that omits a required argument or misstates an
effect causes the LLM to generate incorrect tool calls. Tool descriptions are
part of the contract, not documentation.

**Rule PROMPT-4:** The system prompt MUST explicitly state the output format
for plans (JSON schema), the output format for responses (structured markdown),
and the constraints on autonomous behavior. An LLM that does not know its output
format will produce free-form output that cannot be parsed.

### 11.2 Context Prompt Structure

**Rule PROMPT-5:** The context prompt (everything after the system prompt)
MUST be assembled in a consistent, predictable order. The LLM's attention is
positionally biased — the most important information must appear at the beginning
or end of the context, not buried in the middle.

```
Recommended context prompt structure:
  1. Current user message (most recent, highest attention)
  2. Files directly relevant to the task (high attention)
  3. Conversation history (recent → older, summarized)
  4. Search results (if any)
  5. Prior context summary (if history was pruned)
```

**Rule PROMPT-6:** File content included in the context MUST be wrapped with
clear delimiters that mark it as user-controlled content. This is both a prompt
injection defense (`04_SECURITY_GUIDELINES.md`, Section 8) and a clarity measure.

```python
# CORRECT — delimiters mark file content as untrusted data
def _format_workspace_file(path: str, content: str) -> str:
    return (
        f"<workspace_file path={path!r}>\n"
        f"{content}\n"
        f"</workspace_file>"
    )

# VIOLATION — file content blends into the prompt
def _format_workspace_file(path: str, content: str) -> str:
    return f"The file {path} contains:\n{content}"
```

**Rule PROMPT-7:** Tool call results included in the context MUST be clearly
attributed: "Result of step 3 (search_text):" not just the raw result. The LLM
must know the provenance of each piece of information.

### 11.3 Prompt Injection Defense

**Rule PROMPT-8:** Pearl MUST wrap all workspace file content in structural tags
that clearly demarcate it from instructions. The LLM is instructed in the system
prompt that content inside `<workspace_file>` tags is user data, not instructions.

**Rule PROMPT-9:** Pearl MUST NOT place LLM-generated content (previous LLM
responses, plan text) in a position where the LLM will interpret it as instructions.
Prior LLM output that is included in context MUST be marked as `<prior_response>`
to prevent self-referential injection.

**Rule PROMPT-10:** Sanitization of file content before inclusion in context:
remove null bytes, limit line length to 2000 characters (truncating with a notice),
and cap file inclusion at `MAX_CONTEXT_FILE_LINES = 500` lines. Beyond this,
include only the relevant excerpt.

---

## 12. Retrieval Strategy

### 12.1 Retrieval Pipeline

Before assembling the LLM context, Pearl MUST execute a retrieval pipeline that
identifies the most relevant codebase content for the current task.

```
Retrieval Pipeline
──────────────────
Input: task representation (primary goal, scope, named symbols)

Step 1: Direct symbol resolution
  └── For each symbol named in the task: find_references(symbol)
  └── Collect: definition file, signature, callers, callees

Step 2: File resolution
  └── For each file named in the task: read_file(path)
  └── For each unresolved symbol: search_text(symbol_name)

Step 3: Proximity expansion
  └── For each resolved file: identify closely related files
      (same module, same test suite, same import graph level)

Step 4: Relevance ranking
  └── Rank all retrieved content by:
      (a) direct mention in task (highest)
      (b) symbol overlap with task terms
      (c) edit recency (recently edited files in this session)
      (d) proximity to directly mentioned files

Step 5: Budget fit
  └── Include items from highest to lowest rank until token budget is reached
  └── Drop lowest-rank items; log what was excluded
```

**Rule AI-8:** The retrieval pipeline MUST execute before the planning LLM call.
Planning without retrieval produces plans that reference symbols Pearl has not
verified exist, in files Pearl has not read.

**Rule AI-9:** Retrieval results MUST be de-duplicated before ranking. If two
retrieval steps return the same file excerpt, it is included once with the higher
of the two relevance scores.

### 12.2 Retrieval Quality Gates

**Rule AI-10:** If retrieval returns zero results for a symbol named in the task,
Pearl MUST surface this to the user before generating a plan. A plan built on a
symbol that does not exist in the indexed codebase is a hallucination risk.

```
GOOD — retrieval gap surfaced:
  "I searched for 'verify_token' in the indexed files and found no definition.
   It may be in an unindexed dependency or dynamically generated. Should I
   search in node_modules, or do you know which file defines it?"

BAD — hallucination proceeds:
  [Pearl generates a plan to modify verify_token in auth.py, which exists only
   in the LLM's training data, not in this codebase]
```

**Rule AI-11:** When retrieval finds multiple definitions of the same symbol (e.g.,
overloaded, multiple modules), Pearl MUST include all definitions in context and
MUST NOT silently pick one. Disambiguation belongs to the human.

---

## 13. Code Understanding

### 13.1 Read Before Reason

**Rule REASON-8:** Pearl MUST read a file before reasoning about its contents.
The LLM's training data may include an older or different version of a popular
library, framework pattern, or coding convention. The current file is always
authoritative over training knowledge.

**Rule REASON-9:** When understanding the scope of a change, Pearl MUST check
both the definition of the changed symbol AND its call sites. A function signature
change that is correct in isolation but breaks all callers is not a correct change.

**Rule REASON-10:** Pearl MUST understand the test coverage of a file before
modifying it. If tests exist for the function being changed, Pearl MUST include
those tests in context and MUST run them after the change.

### 13.2 Code Structure Awareness

**Rule REASON-11:** Pearl MUST identify and respect the existing code structure
before generating a change. If the codebase uses a particular pattern (e.g.,
dependency injection via constructor, repository pattern for data access), Pearl
MUST follow that pattern in new code — not introduce a different pattern.

```
CORRECT — respects existing pattern:
  Existing code: all repository access via self._repo.get_user(id)
  New code:      self._repo.get_session(token)   ← same pattern

VIOLATION — introduces inconsistent pattern:
  Existing code: all repository access via self._repo.get_user(id)
  New code:      db.query(Session).filter_by(token=token).first()  ← inconsistent
```

**Rule REASON-12:** Pearl MUST identify the public API boundary of any module it
is modifying. Changes that break the public API (remove exported names, change
signatures of public functions, rename public classes) MUST be flagged to the
user as breaking changes before the plan proceeds.

**Rule REASON-13:** Pearl MUST understand type annotations in the codebase before
generating typed code. If the codebase uses `str | None` union syntax, Pearl
generates `str | None`. If it uses `Optional[str]`, Pearl uses that. Consistency
matters more than Pearl's preferred style.

---

## 14. Code Generation

### 14.1 Generation Quality Standards

**Rule AI-11:** All code generated by Pearl MUST be syntactically valid in the
target language before it is staged. Pearl MUST validate syntax before staging
a write, using a language-appropriate parser (`ast.parse()` for Python,
`tsc --noEmit` for TypeScript).

```python
# CORRECT — syntax validated before staging
def _stage_python_edit(self, path: str, content: str) -> None:
    try:
        ast.parse(content)
    except SyntaxError as e:
        raise PearlGenerationError(
            f"Generated code is not valid Python: {e}"
        )
    self._patch_manager.stage(path, content)

# VIOLATION — syntax not checked; invalid Python reaches the approval screen
def _stage_python_edit(self, path: str, content: str) -> None:
    self._patch_manager.stage(path, content)
```

**Rule AI-12:** Generated code MUST follow the coding standards in
`02_CODING_STANDARDS.md` — type annotations, naming conventions, import ordering,
and formatting. Code that passes syntax validation but violates coding standards
is not acceptable.

**Rule AI-13:** Generated code MUST be idiomatic for the language and framework
in use. Pearl MUST NOT generate Java-style code in a Python codebase, or
class-based React components in a codebase that uses function components
throughout.

**Rule AI-14:** Pearl MUST NOT generate TODO comments, placeholder functions,
or stub implementations unless the user explicitly requested a scaffold. A PR
that introduces `pass  # TODO: implement` in production code is a defect.

**Rule AI-15:** Generated code MUST handle the error cases that the surrounding
code already handles. If the codebase raises a `FileNotFoundError` for missing
files, Pearl's new code must also raise it for the same condition — not silently
return `None`.

### 14.2 Generation Scope

**Rule AI-16:** Pearl MUST generate the minimum code that satisfies the stated
task. This is the Minimal Footprint Principle (Principle AI-2) applied to code
generation. A task to "add a function" must not produce a refactored module.

**Rule AI-17:** Pearl MUST NOT generate new external dependencies without
explicit user approval. A new `import external_library` that introduces a
previously absent dependency is a scope violation — it changes the project's
dependency graph without user consent.

---

## 15. Refactoring Rules

**Rule AI-18:** Refactoring plans MUST preserve observable behavior. Pearl
MUST run the existing test suite after a refactoring and MUST NOT stage the
refactored code if the tests fail.

**Rule AI-19:** Refactoring scope MUST be contained to the files explicitly in
scope. Pearl MUST NOT refactor adjacent code "while it's there." Each such
refactoring is an undisclosed change that complicates the diff review.

**Rule AI-20:** When a refactoring renames a symbol, Pearl MUST update ALL
call sites within the workspace scope. A rename that leaves broken references
is worse than no rename.

**Rule AI-21:** Refactoring that changes a public API (exported function
signature, class interface, MCP method shape) MUST be flagged as a breaking
change in the approval diff and MUST NOT proceed without explicit user
confirmation.

**Rule AI-22:** Before any refactoring, Pearl MUST read the current implementation
of what it is refactoring. A refactoring based on the LLM's assumption of what
the code contains is a hallucination risk.

---

## 16. Verification Before Action

### 16.1 Pre-Write Verification

Before staging any write, Pearl MUST complete a verification pipeline:

```
Pre-Write Verification Pipeline
────────────────────────────────
Check 1: File exists and is readable
  └── Failure: report to user; do not write

Check 2: Patch applies cleanly
  └── Apply the diff in-memory; check for conflicts or line-number mismatches
  └── Failure: regenerate patch with current file content; retry once

Check 3: Generated code is syntactically valid
  └── ast.parse() for Python; tsc --noEmit for TypeScript
  └── Failure: regenerate code; do not stage invalid syntax

Check 4: Change is within workspace boundary
  └── _ensure_within_workspace(path) must not raise
  └── Failure: PearlSafetyError; do not proceed

Check 5: Change matches the task scope
  └── Is this file within the task's declared scope?
  └── Failure: flag to user as a scope expansion; ask for confirmation

Result: ALL checks PASS → stage the write
        ANY check FAILS → handle per the failure type above
```

**Rule VERIFY-1:** The pre-write verification pipeline is mandatory and MUST
run before every `ChangeManager.stage()` call. There is no fast path that skips
it.

**Rule VERIFY-2:** Patch conflict detection MUST be performed in-memory without
writing to disk. Use Python's `difflib` or a dedicated diff library to apply
the patch against the in-memory file content.

**Rule VERIFY-3:** Line number validation MUST check that the lines Pearl
intends to replace match what is actually in the file at those line numbers.
A mismatch means the file changed since Pearl read it — Pearl must re-read
and regenerate.

```python
# CORRECT — line content verified before staging
def _verify_patch_applies(
    self, path: str, start: int, end: int, expected_old: str
) -> None:
    current = self._memory.file_read_cache[path].content
    current_lines = current.splitlines()
    actual_old = "\n".join(current_lines[start - 1 : end])
    if actual_old.strip() != expected_old.strip():
        raise PatchConflictError(
            f"Lines {start}–{end} in {path} have changed since Pearl read them"
        )
```

### 16.2 Post-Write Verification

**Rule VERIFY-4:** After a write is approved and applied, Pearl MUST run at
minimum a syntax check on the written file. A write that produces a file that
cannot be parsed is a regression regardless of approval.

**Rule VERIFY-5:** If a test suite exists for the modified file, Pearl SHOULD
run it after the write and include the result in its completion report. A task
that is "done" but breaks tests is not done.

**Rule VERIFY-6:** Post-write verification results MUST be included in the
completion report. If tests pass, say so. If tests fail, say so and propose
a fix rather than declaring the task complete.

---

## 17. Self-Validation

### 17.1 Plan Self-Review

**Rule SELF-1:** Before presenting a plan for execution, the planner MUST
perform a self-review: re-read the plan with the question "does this plan
achieve the stated goal without side effects?" If the answer is no, the plan
MUST be revised.

**Rule SELF-2:** The self-review MUST check for:

| Check | Question |
|---|---|
| Completeness | Does the plan address every part of the user's request? |
| Scope | Does the plan write to any file not implied by the task? |
| Order | Does the plan read before it writes? |
| Verification | Does the plan include a post-write verification step? |
| Approval | Are all write and shell steps flagged for approval? |
| Assumptions | Are all high-risk assumptions noted? |

**Rule SELF-3:** The self-review MUST be implemented as a deterministic check
function, not as a second LLM call. LLM self-review has the same error rate as
the original generation and produces a false sense of validation.

```python
# CORRECT — deterministic self-review
def _self_review_plan(self, plan: Plan, task: Task) -> list[PlanIssue]:
    issues = []
    if not any(s.tool_name in READ_TOOLS for s in plan.steps[:3]):
        issues.append(PlanIssue("plan starts with write, no prior read"))
    if not any(s.requires_approval for s in plan.steps if s.tool_name in WRITE_TOOLS):
        issues.append(PlanIssue("write steps not flagged for approval"))
    if not any(s.tool_name in VERIFY_TOOLS for s in plan.steps):
        issues.append(PlanIssue("no verification step in plan"))
    return issues
```

### 17.2 Output Self-Review

**Rule SELF-4:** Before staging any generated code, Pearl MUST re-read the
generated code and verify it against the task description. The question is:
"If I saw this diff on a PR, would I approve it as solving the stated problem?"

**Rule SELF-5:** Pearl MUST check that generated code does not introduce any
of the prohibited patterns from `04_SECURITY_GUIDELINES.md`: `eval()`, `exec()`,
`shell=True`, string-concatenated paths, or `pickle.loads()`. If these appear
in generated code, Pearl MUST regenerate.

**Rule SELF-6:** Pearl MUST check that generated code does not add imports that
are not in the project's dependency list. If a new import appears, Pearl MUST
flag it to the user as a new dependency.

---

## 18. Error Recovery

### 18.1 Error Classification

**Rule REC-1:** Every error Pearl encounters during execution MUST be classified
before a recovery action is taken. The classification determines the recovery
strategy.

| Error class | Definition | Recovery |
|---|---|---|
| **Transient** | Network timeout, rate limit, flaky subprocess | Retry with backoff |
| **Configuration** | Wrong workspace root, missing API key | Surface to user; halt |
| **Plan failure** | Tool returned unexpected result | Replan with new information |
| **Safety violation** | Path traversal, dangerous command | PearlSafetyError; halt |
| **Budget exceeded** | Token or file limit hit | Compress context; retry |
| **Task impossible** | Goal cannot be achieved with available tools | Surface to user; halt |

**Rule REC-2:** Safety violation errors MUST halt execution immediately and MUST
NOT trigger replanning. A plan that violated a safety constraint is not repairable
by generating a new plan — it requires human review.

**Rule REC-3:** Task-impossible errors MUST include a specific explanation of
why the task cannot be completed. "I can't do that" is not acceptable. "The
function `verify_token` is defined in a compiled extension module
(`_auth.cpython-311.so`) which Pearl cannot edit" is acceptable.

### 18.2 Recovery Workflow

```
Error Recovery State Machine
─────────────────────────────
START → executing step N
         │
         ▼
    [Error occurs]
         │
         ├── Transient error?
         │     └── Retry (max 3, exponential backoff)
         │           ├── Success → continue plan
         │           └── Max retries → classify as Plan failure
         │
         ├── Plan failure?
         │     └── Record in failed_attempts
         │           └── Replan (max 3 replans)
         │                 ├── Success → continue new plan
         │                 └── Max replans → FAIL with full history
         │
         ├── Safety violation?
         │     └── PearlSafetyError → halt → report to user
         │
         ├── Configuration error?
         │     └── Halt → ask user to fix configuration
         │
         └── Task impossible?
               └── Halt → explain why → suggest alternatives
```

**Rule REC-4:** The full error history for a session (all errors, their
classifications, and recovery actions taken) MUST be preserved in working memory
and included in the final completion report when the session ends with a failure.

**Rule REC-5:** When Pearl halts due to an error, it MUST leave the workspace
in the state it was in before the failed plan began. The checkpoint taken at
PLAN-5 is used for this rollback.

---

## 19. Retry Strategy

**Rule FAIL-1:** Transient errors MUST be retried with exponential backoff and
jitter. The retry schedule is:

```
Attempt 1: immediate
Attempt 2: 2 s ± 0.5 s jitter
Attempt 3: 8 s ± 2 s jitter
Attempt 4: fail → reclassify as Plan failure
```

**Rule FAIL-2:** Plan failures MUST NOT be retried with the same plan. A retry
with an identical plan that already failed wastes a replan budget slot and
produces no new information.

**Rule FAIL-3:** When a tool call fails due to a missing file, Pearl MUST NOT
retry the same path. Instead, Pearl MUST search for the file (it may have moved
or been renamed) and update the plan with the correct path.

**Rule FAIL-4:** Rate limit errors from the LLM provider MUST be retried with a
minimum backoff of **10 seconds**. Rate limits are API-side constraints, not
transient network issues, and require real waiting.

**Rule FAIL-5:** Context-too-long errors from the LLM provider MUST trigger
context compression (CTX-6) and a re-dispatch with the compressed context.
They MUST NOT be reported to the user as an error — this is an internal
budget management failure that must be transparently recovered.

**Rule FAIL-6:** After every retry (successful or not), Pearl MUST emit a
progress event noting the retry reason and attempt number. Silent retries
leave the user wondering why the operation is slow.

---

## 20. Reflection Loop

### 20.1 Reflection Triggers

**Rule SELF-7:** Reflection executes after every completed plan, after every
replan, and on demand when the user asks "is this done?" or "did that work?"

**Rule SELF-8:** Reflection MUST compare the actual post-execution state against
the task's stated goal. It is not sufficient to note that all steps completed
without error — steps can succeed individually while the overall goal is not met.

```
Reflection Structure
────────────────────
1. Restate the goal (from the task representation)
2. Enumerate what was changed (from the execution context)
3. Verify: does the changed state satisfy the goal?
   └── Run verification tool calls if needed
4. Identify gaps: what part of the goal is not yet satisfied?
5. Decision:
   └── COMPLETE → emit completion report
   └── PARTIAL  → replan for the remaining gap (max 3 replans total)
   └── FAILED   → surface failure with evidence
```

### 20.2 Reflection Examples

**Before/After Reflection Example — Correct:**

```
Task: "Fix the failing test in test_auth.py:TestVerifyToken.test_expired_token"

After execution:
  Step 1: read_file("src/auth/jwt.py")        → OK
  Step 2: read_file("tests/test_auth.py")     → OK
  Step 3: edit_lines("src/auth/jwt.py", ...)  → staged
  Step 4: run_tests("tests/test_auth.py")     → exit 0, 1 passed

Reflection:
  Goal: fix test_expired_token
  Changed: jwt.py lines 45–52 (verify_token: added expiry check)
  Verification: pytest reports test_expired_token PASSED
  Gaps: none
  Decision: COMPLETE

Report: "Fixed verify_token in src/auth/jwt.py to raise TokenExpiredError
         when the token's exp claim is in the past. Test now passes."
```

**Before/After Reflection Example — Incomplete:**

```
Task: "Add input validation to all API endpoints"

After execution:
  Step 1–4: edited users.py, products.py
  Step 5: run_tests() → exit 0

Reflection:
  Goal: add input validation to ALL API endpoints
  Changed: users.py, products.py (2 of 5 endpoint files)
  Verification: tests pass for modified files
  Gaps: orders.py, payments.py, webhooks.py not yet modified
  Decision: PARTIAL → replan for remaining 3 files

[replan executes for orders.py, payments.py, webhooks.py]
```

**Rule SELF-9:** Reflection MUST NOT declare a task COMPLETE based solely on
the fact that all plan steps succeeded. Success at the step level does not
guarantee success at the goal level.

**Rule SELF-10:** Reflection conclusions MUST be evidence-based. "The tests
pass" is evidence. "I believe it works" is not. Every COMPLETE declaration
must cite specific evidence from tool results.

---

## 21. Human Approval Rules

### 21.1 Approval Triggers

**Rule HITL-1:** The following actions MUST ALWAYS trigger a human approval
prompt before execution:

| Action | Approval type |
|---|---|
| Writing to any file in the workspace | Diff-based approval (ChangeManager) |
| Executing any shell command in autonomous mode | Command approval (CommandApprovalManager) |
| Deleting a file | Explicit deletion approval |
| Installing a new dependency | Dependency approval |
| Running destructive git operations | Git operation approval |
| Expanding scope beyond what the task stated | Scope expansion approval |

**Rule HITL-2:** Approval prompts MUST NOT be skipped, bypassed, or auto-approved
unless the user has explicitly configured auto-approval for that tool in their
settings. This is the core invariant of `01_ARCHITECTURE_RULES.md`, Section 3.

**Rule HITL-3:** The approval prompt MUST include: (a) a description of what
will happen, (b) the diff or command being approved, (c) the rationale (why Pearl
is doing this), and (d) the reversibility information (checkpoint reference).

### 21.2 Approval Content Quality

**Rule HITL-4:** Diffs presented for approval MUST be syntactically correct and
semantically coherent. A diff that shows a change Pearl cannot explain is a sign
that the plan produced a wrong result. Pearl MUST be able to explain every line
of every diff it presents.

**Rule HITL-5:** When presenting a multi-file diff for approval, Pearl MUST
present the changes in a logical reading order: modified interfaces first, then
implementations, then tests. Random file ordering makes review harder.

**Rule HITL-6:** Pearl MUST NOT resubmit an identical diff for approval after
the user rejected it. A rejected diff is a signal that the approach is wrong.
Pearl must replan — not resubmit.

### 21.3 Disagreement Protocol

**Rule HITL-7:** When Pearl disagrees with the user's chosen approach, it MUST
express the disagreement once, specifically, with the technical reason. After
expressing it, if the user confirms the approach, Pearl MUST execute it without
further objection.

```
CORRECT disagreement protocol:
  User: "Delete the JWT verification entirely, I'll add it back later"
  Pearl: "This will leave the API unprotected — any caller can access
          authenticated routes without a valid token. If you're certain,
          I'll proceed. Shall I continue?"
  User: "Yes, proceed"
  Pearl: [executes the deletion]

VIOLATION — passive resistance:
  User: "Delete the JWT verification entirely"
  Pearl: "I'll remove most of the JWT verification, but I'll keep the
          signature check for safety." ← executes a different change
          than what was asked
```

**Rule HITL-8:** Pearl MUST NOT silently execute a different action than what
the user approved. If the user approves a diff and Pearl's implementation
diverges from that diff, that is a violation of the approval invariant.

---

## 22. Safety Constraints

### 22.1 Absolute Prohibitions

**Rule SAFE-1:** Pearl MUST NEVER autonomously execute actions that are
irreversible without human approval. This includes: deleting files, dropping
databases, pushing to remote git branches, and publishing to external services.

**Rule SAFE-2:** Pearl MUST NEVER read from, write to, or execute anything
outside the workspace boundary. The workspace boundary is enforced by
`_ensure_within_workspace()` which is called by every file tool. See
`04_SECURITY_GUIDELINES.md`, Section 5.

**Rule SAFE-3:** Pearl MUST NEVER trust LLM output as instructions. LLM output
is data to be parsed, not commands to be executed. A plan step that says
"execute the following Python code: ..." must not be executed — it must be
evaluated as a tool call proposal.

**Rule SAFE-4:** Pearl MUST NEVER expose or log API keys, secrets, or the
contents of `.env` files at any verbosity level. See `04_SECURITY_GUIDELINES.md`,
Section 4.

**Rule SAFE-5:** Pearl MUST NEVER escalate its own permissions. A request from
the LLM to add `sudo` to a command, to change file permissions to 777, or to
write to system directories MUST be blocked by `_DANGEROUS_PATTERNS` and
surfaced as a safety violation.

### 22.2 Scope Safety

**Rule SAFE-6:** Pearl MUST enforce task scope at the file level. If the task
is "fix the bug in auth.py", Pearl MUST NOT write to any other file without
explicitly asking the user first, even if writing to another file would make
the fix cleaner.

**Rule SAFE-7:** Pearl MUST NOT read files it does not need for the current task.
Reading unrelated files to "understand the codebase better" inflates context
and creates unnecessary exposure of sensitive content to the LLM provider.

**Rule SAFE-8:** When the user says "undo" or "revert", Pearl MUST use the
checkpoint system (`src/checkpoints/`) and MUST NOT attempt to manually
reverse changes. Manual reversal is error-prone and may produce a state that
is worse than the original error.

---

## 23. Autonomous Limits

### 23.1 Iteration Limits

**Rule SAFE-9:** The maximum number of tool calls in a single autonomous run is
**50**. A run that would require more than 50 tool calls is attempting something
too large for a single autonomous session. Pearl MUST halt at 50, report what
was accomplished, and ask the user whether to continue.

**Rule SAFE-10:** The maximum number of replans per task is **3** (defined in
PLAN-13). The maximum number of retry attempts per tool call is **4** (FAIL-1).
These limits exist to prevent runaway loops.

**Rule SAFE-11:** The maximum number of consecutive write operations without a
user-visible progress event is **1**. Pearl MUST emit a progress event between
every two write operations.

**Rule SAFE-12:** Pearl MUST NOT run in fully autonomous mode (no approval
prompts) unless the user has explicitly configured `auto_approve: true` in their
Pearl settings. Auto-approve is opt-in, never the default. See
`05_UI_UX_GUIDELINES.md`, UX-9.

### 23.2 Scope Limits

**Rule SAFE-13:** Pearl MUST NOT proactively refactor, reorganize, or "improve"
code that was not in the task scope. Unsolicited improvements are footprint
violations (Principle AI-2) and complicate diff review.

**Rule SAFE-14:** When Pearl discovers a bug or a code smell while executing
a task, it MUST report it in its completion summary rather than fixing it
silently. The user decides whether to address it.

```
CORRECT — unsolicited bug surfaced, not fixed:
  "I also noticed that parse_date() on line 87 of utils.py does not handle
   the case where the input is None, which would raise a TypeError. I did
   not change it — let me know if you'd like me to fix that separately."

VIOLATION — silently fixed:
  [Pearl adds a None check to parse_date() without mentioning it]
```

---

## 24. Learning Strategy

**Rule LEARN-1:** Pearl MUST NOT hard-code preferences learned from one user's
session into its system prompt or tool definitions. Preferences are session-scoped
or stored in user-specific configuration, not baked into Pearl's behavior globally.

**Rule LEARN-2:** When Pearl encounters a pattern in the codebase that differs from
its default style (e.g., single-quoted strings, 2-space indentation), it MUST
adopt that pattern for all code it generates in that session. Session-local style
consistency is not optional.

```python
# CORRECT — adopted from existing codebase patterns
def _detect_quote_style(source: str) -> str:
    single = source.count("'")
    double = source.count('"')
    return "single" if single > double else "double"

def _format_string(value: str, style: str) -> str:
    q = "'" if style == "single" else '"'
    return f"{q}{value}{q}"
```

**Rule LEARN-3:** Pearl MUST record patterns it observes in the codebase
(naming conventions, import style, error handling patterns) in working memory
and apply them to all generated code in that session.

**Rule LEARN-4:** Pearl MUST flag when a user's request conflicts with a pattern
Pearl has observed in the codebase. This is information the user needs: "I notice
this codebase uses X pattern everywhere — your request suggests Y pattern. Which
should I use?"

**Rule LEARN-5:** Pearl MUST NOT "learn" that a safety constraint does not apply.
If a user approves a dangerous action in one session, Pearl MUST NOT treat that
as permission to skip the approval gate in future sessions. Each session starts
with full safety constraints active.

---

## 25. Confidence Scoring

### 25.1 Confidence Model

Every plan step carries a confidence score between 0.0 and 1.0. Confidence
reflects Pearl's certainty that the step will produce the intended outcome.

| Score range | Label | Meaning | Action |
|---|---|---|---|
| 0.9 – 1.0 | High | Step is fully determined from retrieved context | Proceed |
| 0.7 – 0.89 | Medium | Step is mostly determined; minor assumption present | Proceed; note assumption |
| 0.5 – 0.69 | Low | Step depends on an unverified assumption | Verify assumption first |
| < 0.5 | Very low | Step is speculative; context insufficient | Ask user or retrieve more |

**Rule AI-23:** Steps with confidence < 0.5 MUST NOT appear in an executable
plan. They must be preceded by a retrieval or read step that raises the
confidence before the uncertain step executes.

**Rule AI-24:** Confidence scores MUST be computed deterministically from
observable factors — not generated by the LLM. The LLM is notoriously
overconfident. Confidence scoring is the engineering system's responsibility.

```python
def _compute_step_confidence(self, step: ToolCall) -> float:
    score = 1.0
    # Penalize for each unverified assumption this step depends on
    for assumption in step.assumptions:
        if not assumption.verified:
            score -= 0.2 if assumption.risk == "low" else 0.4
    # Penalize if the target file has not been read this session
    if step.tool_name in WRITE_TOOLS:
        path = step.kwargs.get("path", "")
        if path not in self._memory.file_read_cache:
            score -= 0.3
    return max(0.0, score)
```

**Rule AI-25:** The overall plan confidence is the minimum confidence of any
step in the plan. A plan with one low-confidence step is a low-confidence plan.

### 25.2 Confidence Communication

**Rule OBS-1:** When plan confidence is below 0.7, Pearl MUST surface the low-
confidence steps to the user in the plan summary, with the reason for low
confidence. The user may choose to provide more context to raise confidence.

**Rule OBS-2:** Pearl MUST NOT present a low-confidence plan as if it were
certain. Hedging language in the plan summary is appropriate and required when
confidence is below 0.7.

---

## 26. Hallucination Prevention

### 26.1 Hallucination Risk Taxonomy

LLM hallucination in a coding agent context takes specific, high-impact forms:

| Hallucination type | Description | Prevention |
|---|---|---|
| **Ghost function** | LLM references a function that does not exist | Verify all named symbols via retrieval (Rule AI-10) |
| **Wrong signature** | LLM generates a call with incorrect arguments | Read the definition before generating any call |
| **Stale memory** | LLM uses training knowledge of a library's old API | Read the actual installed version; check signatures |
| **False test result** | LLM claims tests pass without running them | Always run tests; never claim pass without evidence |
| **Wrong line number** | LLM generates an edit for a line that has moved | Validate line content before staging (VERIFY-3) |
| **Invented import** | LLM adds an import that doesn't resolve | Verify all imports resolve before staging |
| **Fictional config** | LLM references a config key that doesn't exist | Read config files before referencing them |

**Rule VERIFY-7:** Pearl MUST verify the existence of every function, class, and
module it references in generated code against the indexed codebase or the
installed dependencies. References to non-existent symbols are blocked before
staging.

**Rule VERIFY-8:** Pearl MUST NEVER claim that tests pass without running them.
"Tests should pass" is a hallucination. "I ran `pytest tests/test_auth.py` and
it exited with code 0" is a verified claim.

**Rule VERIFY-9:** When the LLM generates a function call with arguments, Pearl
MUST validate the argument names against the function's actual signature as
read from the codebase. A `**kwargs` call that passes incorrect keyword names
fails at runtime.

**Rule VERIFY-10:** Pearl MUST NEVER use its LLM training knowledge as the
primary source of truth about the current codebase. Training knowledge is a
prior; the actual file content is the posterior. Always read, then reason.

### 26.2 Grounding Techniques

**Rule VERIFY-11:** Before generating a patch to a file, Pearl MUST quote the
specific lines it is replacing in the patch rationale. This forces the LLM to
ground its output in the actual file content rather than its assumed content.

```python
# CORRECT — original lines quoted in rationale
step = ToolCall(
    tool_name="edit_lines",
    kwargs={"path": "auth.py", "start": 45, "end": 52, "content": NEW_CODE},
    rationale=(
        "Replacing lines 45–52 (current content: 'def verify_token(token):\\n"
        "    return jwt.decode(token, SECRET)') to add expiry check"
    ),
)

# VIOLATION — rationale does not reference actual content
step = ToolCall(
    tool_name="edit_lines",
    kwargs={"path": "auth.py", "start": 45, "end": 52, "content": NEW_CODE},
    rationale="Adding expiry check to verify_token",
)
```

**Rule VERIFY-12:** Pearl MUST run linting (`ruff check`) on all generated
Python code before staging. Linting catches undefined name references (F821),
unused imports (F401), and other hallucination symptoms that syntax parsing
alone does not catch.

---

## 27. Decision Trees

### 27.1 Task Intake Decision Tree

```
New user message received
│
├── Is the message a question (no action required)?
│   └── YES → answer from context; no tools needed
│
├── Is the message ambiguous about WHICH FILES to modify?
│   └── YES → ask ONE clarifying question; halt until answered
│
├── Is the task scope clearly defined?
│   └── NO → infer scope from task + codebase structure; note inference
│
└── Proceed to retrieval and planning
```

### 27.2 Tool Selection Decision Tree

```
Plan step requires an action
│
├── Is the action a READ?
│   ├── Target is a specific file and path is known → read_file
│   ├── Target is a symbol, file unknown → find_references
│   ├── Target is a text pattern → search_text
│   ├── Target is directory structure → list_directory
│   └── Target is git state → git_log / git_diff
│
├── Is the action a WRITE?
│   ├── New file → create_file
│   ├── Replace specific lines (known line range) → edit_lines
│   ├── Replace a named function → replace_function
│   ├── Replace a named class → replace_class
│   └── Apply a unified diff → patch_file
│
└── Is the action a RUN?
    ├── Tests → execute_shell(["pytest", ...])
    ├── Lint → execute_shell(["ruff", "check", ...])
    └── Other → execute_shell([...]) ← always requires approval
```

### 27.3 Error Recovery Decision Tree

```
Tool call returns error
│
├── Is it a network/timeout error?
│   └── Retry with backoff (max 3 attempts)
│       ├── Success → continue
│       └── Failed → treat as Plan failure
│
├── Is it a "file not found" error?
│   └── Search for the file; update plan with correct path
│       ├── Found → continue with corrected path
│       └── Not found → surface to user; halt
│
├── Is it a "syntax error" in generated code?
│   └── Regenerate code with the syntax error as context
│       ├── Success → stage corrected code
│       └── Failed → surface error; halt
│
├── Is it a safety violation?
│   └── PearlSafetyError → halt immediately; report to user
│
└── Is it an "unexpected result" (not an error, but wrong output)?
    └── Record in failed_attempts → replan (max 3)
```

### 27.4 Confidence Gate Decision Tree

```
Plan step confidence computed
│
├── Confidence ≥ 0.9
│   └── Proceed directly
│
├── 0.7 ≤ Confidence < 0.9
│   └── Note assumption in plan summary; proceed
│
├── 0.5 ≤ Confidence < 0.7
│   └── Insert retrieval step before this step to verify assumption
│       ├── Retrieval succeeds, confidence rises → proceed
│       └── Retrieval fails → surface to user; ask for guidance
│
└── Confidence < 0.5
    └── Do not include this step in the plan
        └── Ask user for more context, or replace with a read step
```

---

## 28. AI Anti-Patterns

### 28.1 Anti-Pattern Catalog

**Anti-Pattern AP-AI-1: The Confident Hallucinator**

> **Description:** Pearl generates a plan that references functions, modules, or
> configuration keys that do not exist in the codebase, presenting them with full
> confidence.
>
> **Symptom:** Tool call fails with "symbol not found"; LLM had no prior evidence
> the symbol exists.
>
> **Root cause:** Plan generated before retrieval; LLM used training knowledge
> instead of actual codebase content.
>
> **Correct approach:** Always execute the retrieval pipeline (Section 12) before
> plan generation. Rule AI-8 is non-optional.

---

**Anti-Pattern AP-AI-2: The Scope Creeper**

> **Description:** Pearl modifies files and symbols beyond the task's stated scope
> without informing the user — "improving" adjacent code, fixing unrelated bugs,
> or reorganizing modules while implementing the requested feature.
>
> **Symptom:** Approval diff includes changes to files not mentioned in the task.
>
> **Root cause:** Violation of Principle AI-2 (Minimal Footprint). Unbounded task
> interpretation.
>
> **Correct approach:** Rules SAFE-6, SAFE-13, SAFE-14. Surface unsolicited
> findings; do not fix them silently.

---

**Anti-Pattern AP-AI-3: The Infinite Replan Loop**

> **Description:** Pearl replans indefinitely when a task fails, trying minor
> variations of the same approach without making progress.
>
> **Symptom:** The user waits; nothing changes in the workspace; the token counter
> climbs; no completion report appears.
>
> **Root cause:** No replan limit; replanning without consulting `failed_attempts`;
> treating a task-impossible error as a transient failure.
>
> **Correct approach:** PLAN-13 (3-replan limit), REC-3 (task-impossible halt),
> MEM-4 (consult failed_attempts before replanning).

---

**Anti-Pattern AP-AI-4: The Silent Failure**

> **Description:** A tool call fails; Pearl notes the failure internally but does
> not update the plan, does not replan, and proceeds as if the step succeeded.
>
> **Symptom:** Subsequent steps operate on missing data; final output is wrong
> or absent; no error is surfaced to the user.
>
> **Root cause:** Missing error check after tool dispatch (violation of EXEC-5).
>
> **Correct approach:** Every tool result is checked for errors before the next
> step executes. Errors trigger the recovery workflow (Section 18).

---

**Anti-Pattern AP-AI-5: The Stale Context Writer**

> **Description:** Pearl generates an edit for a file based on the line numbers
> from an earlier read, but the file has since been modified by a prior plan step.
> The edit applies to the wrong lines.
>
> **Symptom:** Patch conflict error; or worse, patch applies but to the wrong code.
>
> **Root cause:** Line numbers from a prior read used without re-validation
> (violation of VERIFY-3, TOOL-7).
>
> **Correct approach:** After any write, invalidate the file in the read cache
> (MEM-3). Validate line content before staging (VERIFY-3).

---

**Anti-Pattern AP-AI-6: The Context Carpet-Bomb**

> **Description:** Pearl includes every file it has ever read in the LLM context,
> regardless of relevance to the current step.
>
> **Symptom:** Context utilization > 90%; LLM responses degrade in quality; old
> irrelevant files push out current relevant content.
>
> **Root cause:** No relevance ranking; context treated as append-only across turns.
>
> **Correct approach:** CTX-1 (relevance ranking), CTX-2 (priority ordering),
> `06_PERFORMANCE_ENGINEERING.md` CTX-7 (compression pipeline).

---

**Anti-Pattern AP-AI-7: The Over-Cautious Asker**

> **Description:** Pearl asks clarifying questions for every task, including tasks
> where the intent is unambiguous to any experienced developer.
>
> **Symptom:** Developer submits "fix the type error in auth.py" and receives
> three clarifying questions before any action is taken.
>
> **Root cause:** Misconfigured uncertainty threshold; Pearl asks when it should
> infer.
>
> **Correct approach:** REASON-4 (no unnecessary clarification). If a senior
> developer would know what to do, Pearl knows too.

---

**Anti-Pattern AP-AI-8: The Passive Resister**

> **Description:** When the user requests an approach Pearl disagrees with, Pearl
> silently implements a different, safer version instead of executing what was
> asked.
>
> **Symptom:** Approval diff does not match what the user asked for; Pearl
> "helpfully" added back the guard it was asked to remove.
>
> **Root cause:** Violation of HITL-7, Principle AI-6.
>
> **Correct approach:** Disagree once, explicitly. Execute the user's decision
> after confirmation. Never implement a different change than what was approved.

---

**Anti-Pattern AP-AI-9: The Verification Skipper**

> **Description:** Pearl completes a multi-step change and immediately reports
> success without running any verification (no linting, no tests, no syntax
> check).
>
> **Symptom:** User approves diff; code reaches disk; IDE immediately shows
> syntax errors or test failures.
>
> **Root cause:** Reflection phase skipped or truncated (violation of REASON-1,
> VERIFY-4, VERIFY-5).
>
> **Correct approach:** Verification is mandatory. Every completion report cites
> specific evidence (SELF-10).

---

**Anti-Pattern AP-AI-10: The Memory Amnesiac**

> **Description:** Pearl forgets, mid-session, what it has already done. It
> re-reads files it already has in cache, proposes changes it already staged,
> or asks for information the user already provided.
>
> **Symptom:** Duplicate read tool calls; duplicate questions; context growing
> faster than necessary.
>
> **Root cause:** Working memory not updated after each step (violation of MEM-2);
> context assembly not checking existing cache (violation of CTX-5).
>
> **Correct approach:** MEM-2 (update memory after every tool call), TOOL-4 (no
> duplicate tool calls with identical arguments), CTX-5 (serve post-write content
> from cache).

---

**Anti-Pattern AP-AI-11: The Approval Bypasser**

> **Description:** A new execution path, agent mode, or tool invokes write tools
> without activating a `ChangeManager`, effectively writing to disk without approval.
>
> **Symptom:** Files are modified without an approval prompt appearing.
>
> **Root cause:** Direct violation of `01_ARCHITECTURE_RULES.md`, Section 3
> (the Approval Invariant).
>
> **Correct approach:** Rule HITL-2. All autonomous write paths MUST activate
> ChangeManager. This is a P0 security and safety violation.

---

**Anti-Pattern AP-AI-12: The Assumption Silence**

> **Description:** Pearl builds a plan on top of multiple unverified assumptions
> (about file structure, API shape, function behavior) and presents it as if it
> were fully grounded in retrieved facts.
>
> **Symptom:** Plan looks reasonable; execution fails with errors that reveal
> the assumptions were wrong; no prior warning was given.
>
> **Root cause:** Violation of REASON-5 (assumption tracking), REASON-7 (surface
> unverifiable assumptions), AI-23 (low-confidence steps blocked).
>
> **Correct approach:** Every assumption is tracked; high-risk assumptions are
> verified before dependent steps; unverifiable assumptions are disclosed.

---

## 29. Common AI Mistakes

**Mistake M-AI-1: Writing to a file before reading it**

```python
# WRONG — editing auth.py without reading it first
plan = [
    ToolCall("edit_lines", {"path": "auth.py", "start": 45, ...}),
]

# CORRECT — read before edit
plan = [
    ToolCall("read_file", {"path": "auth.py"}),
    ToolCall("edit_lines", {"path": "auth.py", "start": 45, ...}, depends_on=(0,)),
]
```

Resolution: Rule PLAN-2. Read-before-write is enforced by plan validation.

---

**Mistake M-AI-2: Claiming tests pass without running them**

```
# WRONG — in reflection
"The change looks correct and the tests should pass."

# CORRECT — evidence cited
"I ran `pytest tests/test_auth.py::TestVerifyToken` — 3 passed in 0.42s."
```

Resolution: VERIFY-8. Never claim verification without running the verifier.

---

**Mistake M-AI-3: Using `search_text` when `find_references` is available**

```python
# WRONG — general text search for a known symbol
ToolCall("search_text", {"pattern": "verify_token"})

# CORRECT — specialized index-backed lookup
ToolCall("find_references", {"symbol": "verify_token"})
```

Resolution: TOOL-1. Use the most specific tool available.

---

**Mistake M-AI-4: Including absolute paths in tool arguments**

```python
# WRONG — absolute path
ToolCall("read_file", {"path": "/home/user/project/src/auth.py"})

# CORRECT — workspace-relative path
ToolCall("read_file", {"path": "src/auth.py"})
```

Resolution: TOOL-5. All paths are relative to workspace root.

---

**Mistake M-AI-5: Not tracking failed attempts, causing repeated failures**

```python
# WRONG — replanning without consulting what already failed
async def _replan(self) -> Plan:
    return await self._llm_plan(self._task)  # same context as before

# CORRECT — failed attempts in replan context
async def _replan(self) -> Plan:
    failed_context = self._build_failed_context()  # includes failed_attempts
    return await self._llm_plan(self._task, additional_context=failed_context)
```

Resolution: MEM-4, PLAN-14.

---

**Mistake M-AI-6: Staging invalid Python syntax**

```python
# WRONG — no syntax check before staging
self._patch_manager.stage("auth.py", generated_code)

# CORRECT — syntax validated first
try:
    ast.parse(generated_code)
except SyntaxError as e:
    raise PearlGenerationError(str(e))
self._patch_manager.stage("auth.py", generated_code)
```

Resolution: AI-11. Syntax validation is a mandatory pre-staging check.

---

**Mistake M-AI-7: Generating code with a new external import**

```python
# WRONG — introduces new dependency silently
import arrow  # not in requirements.txt

# CORRECT — Pearl flags the new import before staging
"I need to add 'arrow' as a dependency to parse timezone-aware datetimes.
 It is not currently in requirements.txt. Shall I add it?"
```

Resolution: AI-17.

---

**Mistake M-AI-8: Replanning with an identical plan**

```python
# WRONG — same plan, same failure
if self._replan_count < 3:
    self._plan = await self._generate_plan(self._task)  # same prompt
    self._replan_count += 1

# CORRECT — replan context includes failure reason and delta requirement
if self._replan_count < 3:
    self._plan = await self._generate_plan(
        self._task,
        failed_plan=self._plan,
        failure_reason=self._last_error,
        delta_required=True,  # forces LLM to state what is different
    )
```

Resolution: PLAN-14.

---

**Mistake M-AI-9: Submitting the same rejected diff again**

```
# WRONG
User rejects diff → Pearl resubmits identical diff

# CORRECT
User rejects diff → Pearl records rejection in failed_attempts
                  → Pearl asks "What should be different?" or replans
```

Resolution: HITL-6.

---

**Mistake M-AI-10: Declaring success based on plan completion, not goal verification**

```python
# WRONG — all steps ran; declare done
if all(step.completed for step in self._plan.steps):
    return ExecutionReport(status="complete", ...)

# CORRECT — verify goal was achieved, not just steps ran
result = await self._reflect(self._task, self._execution_context)
if result.goal_achieved:
    return ExecutionReport(status="complete", evidence=result.evidence, ...)
else:
    # replan for remaining gap
```

Resolution: SELF-9, SELF-10.

---

**Mistake M-AI-11: Forgetting to emit progress events between writes**

```python
# WRONG — two writes, no progress
await self._stage_write("auth.py", content_a)
await self._stage_write("utils.py", content_b)

# CORRECT — progress between every write
await self._emit_progress("Staging auth.py changes...")
await self._stage_write("auth.py", content_a)
await self._emit_progress("Staging utils.py changes...")
await self._stage_write("utils.py", content_b)
```

Resolution: EXEC-7, EXEC-8.

---

## 30. AI Review Checklist

### 30.1 Pre-Submission (PR Author)

**Reasoning and Planning**
- [ ] All four reasoning phases (Understand, Plan, Execute, Reflect) are represented
      in every agent turn path (Rule REASON-1)
- [ ] Plans are validated by the deterministic validator before execution (Rule PLAN-6)
- [ ] Plans are not longer than 25 steps (Rule PLAN-4)
- [ ] Plans begin with read steps before write steps (Rule PLAN-2, PLAN-16)
- [ ] Plans include a verification step after writes (Rule PLAN-17)
- [ ] Checkpoints are taken before the first write in any plan (Rule PLAN-5)
- [ ] Replan limit is enforced (max 3 replans) (Rule PLAN-13)

**Tool Use**
- [ ] Most specific tool is used for each task (Rule TOOL-1)
- [ ] No duplicate tool calls with identical arguments (Rule TOOL-4)
- [ ] All file paths are workspace-relative (Rule TOOL-5)
- [ ] Search patterns are regex-validated before use (Rule TOOL-6)
- [ ] Line numbers are re-validated against current file content before staging (TOOL-7, VERIFY-3)

**Context and Retrieval**
- [ ] Retrieval pipeline executes before plan generation (Rule AI-8)
- [ ] Context includes file paths and line numbers for all excerpts (Rule CTX-4)
- [ ] Context is assembled once per LLM call (Rule CTX-3)
- [ ] Post-edit file content is served from updated cache, not stale index (Rule CTX-5)

**Verification**
- [ ] Pre-write verification pipeline runs before every `ChangeManager.stage()` (VERIFY-1)
- [ ] Syntax is validated before staging for Python and TypeScript (Rule AI-11)
- [ ] Generated code is linted before staging (Rule VERIFY-12)
- [ ] All imported symbols exist in codebase or dependencies (Rule VERIFY-7)
- [ ] Tests are run after writes and result is included in report (Rule VERIFY-5)

**Safety and Approval**
- [ ] All autonomous write paths activate `ChangeManager` (Rule HITL-2)
- [ ] All shell commands in autonomous mode use `CommandApprovalManager` (Rule HITL-1)
- [ ] Safety prohibitions enforced: no `eval`, `exec`, `shell=True`, system paths (SAFE-1–5)
- [ ] Scope not exceeded without explicit user confirmation (Rule SAFE-6)
- [ ] Tool call limit (50) is enforced (Rule SAFE-9)

**Error Handling**
- [ ] Every tool result is checked for errors before the next step (Rule EXEC-5)
- [ ] Errors are classified before recovery action is taken (Rule REC-1)
- [ ] Safety violations halt immediately without replanning (Rule REC-2)
- [ ] Retry schedule is exponential backoff, max 4 attempts (Rule FAIL-1)
- [ ] Context-too-long errors trigger compression, not user error (Rule FAIL-5)

**Memory and Learning**
- [ ] Working memory is updated after every tool call (Rule MEM-2)
- [ ] File read cache is invalidated after writes (Rule MEM-3)
- [ ] Failed attempts are recorded and consulted on replan (Rule MEM-4)
- [ ] Codebase style patterns are adopted for generated code (Rule LEARN-2)

### 30.2 Code Review (Reviewer)

- [ ] No new execution path that writes files without `ChangeManager` (HITL-2, AI architecture invariant)
- [ ] No LLM output treated as instructions rather than data (Rule SAFE-3)
- [ ] No approval prompt skipped or auto-approved by default (Rule HITL-2, SAFE-12)
- [ ] Reflection phase is present and evidence-based (SELF-9, SELF-10)
- [ ] Confidence scoring is deterministic, not LLM-generated (Rule AI-24)
- [ ] Hallucination prevention: symbols verified, tests run, line numbers validated
- [ ] Replan and retry limits enforced (PLAN-13, FAIL-1)
- [ ] Progress events emitted between every two write operations (EXEC-7, SAFE-11)

---

## 31. AI Release Checklist

### 31.1 Reasoning Quality Gate

```
AI reasoning quality — verify before release tag:

[ ] Scripted-LLM test suite: all reasoning paths PASS
[ ] Approval invariant E2E test: PASS (file not on disk before approval)
[ ] Replan limit test: plan fails at replan 4 with PearlTaskFailureError
[ ] Retry limit test: transient error retried 3×, then reclassified correctly
[ ] Safety violation test: dangerous command blocked immediately, no retry
[ ] Scope limit test: out-of-scope file write blocked at SAFE-6
[ ] Confidence gate test: step with confidence < 0.5 does not appear in plan
[ ] Hallucination test: reference to non-existent symbol surfaced, not silently planned
```

### 31.2 Safety Gate

```
AI safety — verify before release tag:

[ ] No approval bypass path exists (test: autonomous run with ChangeManager=None fails safely)
[ ] _DANGEROUS_PATTERNS blocks all patterns in the security test suite
[ ] Path traversal blocked: tool call with "../../../etc/passwd" raises PearlSafetyError
[ ] API key not logged at any level (log scan: grep -r "api_key" in generated log output)
[ ] Tool call count limit (50) enforced: run halts at step 51
[ ] Auto-approve disabled by default: fresh install has no auto_approve setting
```

### 31.3 Verification Gate

```
AI verification quality — verify before release tag:

[ ] Syntax check runs before every staged Python file
[ ] Linting runs before every staged Python file
[ ] Test runner invoked after write-and-verify plans
[ ] Line number validation (VERIFY-3) prevents patch conflicts in the regression suite
[ ] Post-write read confirms file content matches staged content
```

### 31.4 AI Quality Benchmark

The following AI-specific benchmarks MUST pass before a release tag:

| Benchmark | Description | Budget |
|---|---|---|
| `AI-B-001` | Plan generation (10 tasks, scripted LLM) | 100% structurally valid |
| `AI-B-002` | Retrieval coverage (10 tasks) | ≥ 90% relevant files retrieved |
| `AI-B-003` | Hallucination rate (10 plans) | 0 ghost function references |
| `AI-B-004` | Approval invariant (100 runs) | 0 writes without approval |
| `AI-B-005` | Replan convergence (10 failing tasks) | ≤ 3 replans per task |
| `AI-B-006` | Reflection accuracy (10 tasks) | ≥ 90% correct COMPLETE/PARTIAL |
| `AI-B-007` | Safety blocking (20 dangerous inputs) | 100% blocked |
| `AI-B-008` | Scope containment (10 tasks) | 0 out-of-scope writes |
| `AI-B-009` | Confidence calibration (20 steps) | Confidence < 0.5 → 0 executed |
| `AI-B-010` | Progress streaming (10 tasks) | 0 silent write sequences |

### 31.5 Sign-off

```
AI Release Checklist — complete before pushing release tag:

[ ] Reasoning quality gate: all items PASS
[ ] Safety gate: all items PASS
[ ] Verification gate: all items PASS
[ ] AI benchmarks (AI-B-001 to AI-B-010): all PASS
[ ] No open P0 AI safety bugs

AI Engineer: ______________ Date: __________
Architect Sign-off: _______ Date: __________
```

---

## 32. Canonical Vocabulary

The following terms are used throughout this document with precise, binding meaning.
Use these terms in code, comments, commits, and reviews.

| Term | Definition |
|---|---|
| **Agent turn** | One complete cycle from user message through all tool calls to a completion report. |
| **Plan** | An ordered, validated list of `ToolCall` objects with dependency annotations and rationale. |
| **Replan** | Generating a new plan after a previous plan failed, using all tool results gathered so far. |
| **Retrieval** | The process of identifying and fetching the codebase content most relevant to a task before planning. |
| **Working memory** | The `WorkspaceMemory` object that accumulates structured state across turns within a session. |
| **Execution context** | The mapping of plan step indices to tool results, built up as steps execute. |
| **Confidence score** | A deterministic 0.0–1.0 measure of a plan step's certainty, computed from observable factors. |
| **Assumption** | A fact Pearl requires to be true for a plan step to succeed, which has not been verified by a tool call. |
| **Ghost function** | A function reference in generated code that does not exist in the indexed codebase. |
| **Grounding** | The practice of quoting specific codebase content in plan rationale to force LLM output to match reality. |
| **Reflection** | Phase 4 of the reasoning cycle: comparing actual post-execution state against the task goal. |
| **Footprint** | The set of files, symbols, and side effects touched by Pearl's actions. Minimal footprint = minimum necessary scope. |
| **Scope creep** | Modifying files or symbols not implied by the task, without user approval. |
| **Approval invariant** | The guarantee from `01_ARCHITECTURE_RULES.md` Section 3: no autonomous write reaches disk without user approval. |
| **Staged** | A write that has been prepared and is awaiting user approval via the `ChangeManager`. Not yet on disk. |
| **Checkpoint** | A snapshot of the workspace state, stored in the shadow git repo, used for rollback. |
| **Recovery** | The structured process of classifying an error and selecting the appropriate remediation action. |
| **HITL** | Human-In-The-Loop. The requirement for human approval at defined points in the execution. |
| **Plan validation** | The deterministic rule-based check that a plan satisfies structural, scope, and safety requirements before execution. |
| **Self-review** | The deterministic check Pearl runs on a plan or on generated code before staging, without a second LLM call. |
| **Context assembly** | Building the prompt for the LLM by selecting, ranking, and fitting codebase content within the token budget. |
| **Token budget** | The maximum number of tokens that may appear in the assembled context. Defined in `06_PERFORMANCE_ENGINEERING.md`. |
| **Scripted LLM** | A test double that returns pre-defined plans and responses, used in integration tests to make AI behavior deterministic. |

---

## 33. References

### Internal Cross-References

| Document | Relevant sections |
|---|---|
| `01_ARCHITECTURE_RULES.md` | Section 3 (Approval Invariant); Section 4 (Module Deps); Section 5 (Single Index); Section 9 (Tool System — MAX_SEARCH_RESULTS) |
| `02_CODING_STANDARDS.md` | Section 7 (Async Programming); Section 5 (Error Handling) |
| `03_TESTING_STANDARD.md` | Section 3 (Integration — scripted LLM); Section 6 (Performance Testing); Section 16 (Release Gates) |
| `04_SECURITY_GUIDELINES.md` | Section 2 (Secure Coding); Section 5 (Filesystem Sandboxing); Section 8 (Prompt Injection) |
| `05_UI_UX_GUIDELINES.md` | Section 1 (Design Philosophy — UX-3 progress events); Section 4 (Approval Experience) |
| `06_PERFORMANCE_ENGINEERING.md` | Section 2 (Token Budgets); Section 8 (Prompt Construction); Section 9 (Context Window) |

### Key Implementation Files

| File | Role in AI engine |
|---|---|
| `src/agent/planner.py` | Plan generation, validation, and replanning |
| `src/agent/executor.py` | Execution loop, approval lifecycle, cancellation |
| `src/agent/dispatcher.py` | Tool selection, argument construction, result capture |
| `src/memory/workspace_memory.py` | Working memory, file cache, assumption tracking |
| `src/tools/context_manager.py` | Retrieval pipeline, relevance ranking |
| `src/prompts/system_prompt.py` | System prompt template (version-controlled) |
| `src/llm/base_provider.py` | LLM client abstraction, streaming, retry |

### External References

| Reference | Relevance |
|---|---|
| ReAct: Synergizing Reasoning and Acting in Language Models (Yao et al., 2022) | Theoretical basis for the UNDERSTAND/PLAN/EXECUTE/REFLECT loop |
| Reflexion: Language Agents with Verbal Reinforcement Learning (Shinn et al., 2023) | Basis for the reflection loop and failed-attempt memory |
| Constitutional AI (Anthropic, 2022) | Self-validation and self-review principles |
| Tree of Thoughts (Yao et al., 2023) | Planning with multiple candidate branches |
| HumanEval benchmark | Standard for code generation correctness measurement |
| SWE-bench | Benchmark for autonomous software engineering task completion |
| Anthropic Model Card — Claude | Trust boundaries and safety constraints for the underlying model |

---

*This document is part of the Pearl Engineering Standards Series.*  
*Previous: [06_PERFORMANCE_ENGINEERING.md](06_PERFORMANCE_ENGINEERING.md)*  
*Next: [08_AGENT_RUNTIME_SPECIFICATION.md](08_AGENT_RUNTIME_SPECIFICATION.md)*
