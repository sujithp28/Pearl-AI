# 03 — Testing Standard

**Pearl AI Coding Agent — Engineering Standards Series**  
Document owner: Lead Software Architect  
Applies to: Every sprint, every contributor, every release  
Status: Authoritative — changes require architect sign-off

---

## Purpose

This is Pearl's definitive testing standard. Every future sprint references this
document instead of defining its own testing procedures. It governs how Pearl is
tested at every level — from a single function in isolation to a full release
verification against competing tools.

Testing is not a phase that follows development. It is concurrent with development
and is the primary mechanism by which Pearl maintains its correctness guarantees
across sprints.

---

## Table of Contents

1. [Testing Philosophy](#1-testing-philosophy)
2. [Unit Testing](#2-unit-testing)
3. [Integration Testing](#3-integration-testing)
4. [End-to-End Testing](#4-end-to-end-testing)
5. [Regression Testing](#5-regression-testing)
6. [Performance Testing](#6-performance-testing)
7. [Stress Testing](#7-stress-testing)
8. [Security Testing](#8-security-testing)
9. [Filesystem Validation Testing](#9-filesystem-validation-testing)
10. [Repository Testing](#10-repository-testing)
11. [Multi-Language Testing](#11-multi-language-testing)
12. [Persona Testing](#12-persona-testing)
13. [Dogfooding](#13-dogfooding)
14. [Acceptance Testing](#14-acceptance-testing)
15. [Competitive Benchmarking](#15-competitive-benchmarking)
16. [Release Gates](#16-release-gates)
17. [Reporting Templates](#17-reporting-templates)
18. [PASS / FAIL Criteria](#18-pass--fail-criteria)
19. [Testing Review Checklist](#19-testing-review-checklist)
20. [Mutation Testing](#20-mutation-testing)
21. [Fuzz Testing](#21-fuzz-testing)
22. [Chaos Engineering](#22-chaos-engineering)
23. [Reliability and Endurance Testing](#23-reliability-and-endurance-testing)
24. [Snapshot Testing](#24-snapshot-testing)
25. [Golden Output Testing](#25-golden-output-testing)
26. [Compatibility Testing](#26-compatibility-testing)
27. [Upgrade and Migration Testing](#27-upgrade-and-migration-testing)

---

## 1. Testing Philosophy

### 1.1 Core Principles

**Principle T1 — Tests are specifications.** A test that accurately specifies a
behavior is more valuable than the implementation it tests. If the test is hard to
write, the implementation is probably wrong. A test that is hard to read is a test
that will be silently bypassed when it fails.

**Principle T2 — Tests that pass incorrectly are worse than no tests.** A false-
positive test trains contributors to ignore test failures. When a test fails, the
response is always to understand the failure — never to weaken the assertion.

**Principle T3 — Tests must be deterministic.** A test that sometimes passes and
sometimes fails (a "flaky" test) is a P1 defect. Flaky tests erode trust in the
entire suite. They are fixed before new features are added.

**Principle T4 — Test at the correct boundary.** Unit tests test units. Integration
tests test interactions. End-to-end tests test user-visible outcomes. Using an E2E
test to verify a parsing edge case, or using a unit test to verify an approval
workflow, tests the wrong thing and produces the wrong confidence.

**Principle T5 — Safety invariants have their own test class.** Pearl's approval
invariant — that no autonomous write reaches disk without user approval — is too
important to rely on incidental coverage. It has dedicated tests in every sprint.

### 1.2 The Testing Pyramid

```
                     ┌─────────────────┐
                     │  Persona /      │  ← Slow, high-fidelity, few
                     │  Dogfooding /   │    Run: before each release
                     │  Competitive    │
                     └────────┬────────┘
                     ┌────────┴────────┐
                     │   End-to-End    │  ← Moderate speed, full stack
                     │  (MCP + CLI)    │    Run: each sprint + release
                     └────────┬────────┘
                  ┌───────────┴───────────┐
                  │      Integration      │  ← Real components, scripted LLM
                  │  (executor + tools)   │    Run: every commit (CI)
                  └───────────┬───────────┘
              ┌───────────────┴───────────────┐
              │           Unit Tests          │  ← Fast, isolated, many
              │   (tools, planner, memory)    │    Run: every commit (CI)
              └───────────────────────────────┘
```

### 1.3 Test Suite Structure

```
tests/
├── conftest.py              # Shared fixtures (registry, tmp_workspace, scripted_llm)
├── unit/                    # Unit tests — isolated, no I/O
│   ├── test_executor.py
│   ├── test_planner.py
│   ├── test_dispatcher.py
│   ├── test_patch_manager.py
│   ├── test_personality.py
│   └── tools/
│       ├── test_file_tools.py
│       ├── test_edit_tools.py
│       ├── test_repo_tools.py
│       ├── test_shell_tools.py
│       ├── test_git_tools.py
│       └── test_symbol_editor.py
├── integration/             # Integration tests — real components, scripted LLM
│   ├── test_approval_flow.py
│   ├── test_checkpoint_flow.py
│   ├── test_cancellation.py
│   ├── test_mcp_protocol.py
│   └── test_autonomous_run.py
├── e2e/                     # End-to-end tests — full CLI and MCP stack
│   ├── test_cli_workflow.py
│   ├── test_mcp_server.py
│   └── test_approval_e2e.py
├── security/                # Security tests — boundary, injection, traversal
│   ├── test_workspace_boundary.py
│   ├── test_shell_injection.py
│   └── test_credential_exposure.py
├── performance/             # Performance benchmarks
│   ├── bench_indexing.py
│   ├── bench_tools.py
│   └── bench_e2e.py
├── stress/                  # Stress and load tests
│   ├── test_large_repo.py
│   └── test_long_session.py
└── helpers/
    ├── factories.py         # Test object factories
    ├── scripted_plans.py    # Pre-defined scripted LLM responses
    └── corpus/              # Test repository fixtures
        ├── small/           # < 100 files
        ├── medium/          # ~5,000 files
        └── large/           # ~50,000 files (generated or linked)
```

---

## 2. Unit Testing

### 2.1 Definition

A unit test exercises one function or method in isolation. All external dependencies
(file system, LLM, subprocess, other modules) are mocked or stubbed. The test verifies
that given specific inputs, the function produces the expected output or side effect.

### 2.2 Framework and Configuration

- **Framework:** pytest with `pytest-cov` for coverage measurement
- **Location:** `tests/unit/`
- **Execution:** `pytest tests/unit/ -v`
- **Speed target:** Entire unit suite completes in < 60 seconds
- **Per-test target:** < 100ms per test

### 2.3 What to Unit Test

Every public function in `src/` that is non-trivial (more than a delegation to another
function) MUST have unit tests covering:

| Category | Tests required |
|---|---|
| Happy path | ≥ 1 — the function works correctly with valid input |
| Edge cases | Empty collections, `None` inputs, zero-length strings, path at root |
| Error paths | Each distinct exception the function is documented to raise |
| Boundary values | Minimum, maximum, and one-over-maximum values |

### 2.4 Mocking Strategy

**Rule UT-1:** Mock at the outermost boundary you don't own. For tool tests, mock
`Path.read_text` not the tool's internal helper. For executor tests, mock
`LLMClient.generate_json` not the HTTP layer.

**Rule UT-2:** Use `pytest.monkeypatch` for simple attribute substitution. Use
`unittest.mock.patch` for context-managed patching of imported names.

**Rule UT-3:** Mock objects MUST have `spec=` set to the real object they replace.
A mock without `spec` accepts any method call, hiding missing-method bugs.

```python
# CORRECT
mock_client = Mock(spec=LLMClient)

# VIOLATION — any attribute access succeeds silently
mock_client = Mock()
```

**Rule UT-4:** Do not mock the code under test itself. Mocking `Planner.plan` in a
test of `Planner.plan` produces a test that always passes regardless of what the
real code does.

### 2.5 Critical Unit Tests

These specific unit tests MUST exist in every sprint:

#### Approval Invariant Unit Tests

```python
def test_write_file_stages_to_patch_manager_when_active():
    """File is staged — not written — when a ChangeManager is active."""
    pm = ChangeManager()
    set_active_patch_manager(pm)
    try:
        write_file("test.py", "content")
        assert "test.py" in pm.staged_paths
        assert not Path("test.py").exists()
    finally:
        set_active_patch_manager(None)

def test_write_file_writes_directly_when_no_patch_manager():
    """File is written directly when no ChangeManager is active."""
    with temp_workspace() as ws:
        write_file(str(ws / "test.py"), "content")
        assert (ws / "test.py").read_text() == "content"
```

#### Cancellation Unit Tests

```python
def test_executor_stops_on_cancel_signal():
    """Executor exits cleanly when cancel_event is set mid-run."""
    cancel_event = threading.Event()
    cancel_event.set()  # already cancelled before run starts
    executor = AutonomousExecutor(scripted_planner, scripted_dispatcher,
                                   cancel_event=cancel_event)
    report = executor.run("do something")
    assert report.stop_reason == "cancelled"

def test_llm_generate_raises_on_cancel():
    """LLMClient raises LLMCancelled when cancel_check returns True."""
    client = ScriptedProvider(responses=["hello"])
    with pytest.raises(LLMCancelled):
        client.generate("prompt", cancel_check=lambda: True)
```

#### Emoji Mode Unit Tests

```python
def test_all_four_emoji_modes_produce_distinct_output():
    """Each EmojiMode produces a unique (executing, completed) pair."""
    results = {
        mode: (
            format_event(EventKind.EXECUTING, "tool", mode),
            format_event(EventKind.COMPLETED, "tool", mode),
        )
        for mode in EmojiMode
    }
    assert len(set(results.values())) == len(EmojiMode), (
        "Two or more EmojiMode values produce identical output — "
        "all four modes must be visually distinct."
    )
```

### 2.6 Coverage Requirements

- **Minimum line coverage:** 90% for all `src/` code
- **Hard exemptions** (`# pragma: no cover`): abstract method bodies (`...`),
  `if TYPE_CHECKING:` blocks, unreachable defensive assertions
- **Soft exemptions** (do not gate, but track): `main()` entry points,
  framework-mandated callbacks
- Coverage is measured with:
  ```bash
  pytest tests/unit/ --cov=src --cov-report=term-missing --cov-fail-under=90
  ```

---

## 3. Integration Testing

### 3.1 Definition

An integration test exercises multiple real components working together. Dependencies
on external services (real LLM providers, external APIs) are replaced with the
`scripted` LLM provider. File system and subprocess operations use real temporary
directories.

### 3.2 Framework and Configuration

- **Location:** `tests/integration/`
- **Execution:** `pytest tests/integration/ -v`
- **Speed target:** Entire integration suite completes in < 5 minutes
- **LLM provider:** Always `PEARL_LLM_PROVIDER=scripted`

### 3.3 The Scripted LLM Provider

The `scripted` provider (`src/llm/providers/scripted.py`) is the cornerstone of
deterministic integration testing. It replays pre-defined responses in sequence,
with no network dependency.

**Rule IT-1:** Every integration test that involves planning MUST use the `scripted`
provider. Tests that require a real Ollama or API connection are marked
`@pytest.mark.requires_llm` and are excluded from CI.

**Rule IT-2:** Scripted response sequences MUST be defined in `tests/helpers/scripted_plans.py`
and imported by name. Inline JSON strings inside test files are not permitted.

```python
# tests/helpers/scripted_plans.py
CREATE_FILE_PLAN = [
    # Step 1: plan response
    '{"tool_calls": [{"tool_name": "create_file", "kwargs": {"path": "out.py", "content": "print(1)"}, "reasoning": "Creating the requested file"}]}',
    # Step 2: evaluation response
    '{"status": "success", "summary": "File created successfully."}',
    # Step 3: completion
    '{"tool_calls": [], "reasoning": "Task complete."}',
]
```

### 3.4 Required Integration Tests Per Sprint

These tests MUST pass every sprint before any merge to `master`:

#### Approval Flow Integration Test

```
Scenario: Autonomous run pauses for approval on write operation

Given: A scripted plan that creates a file
When: run_autonomous() is called
Then: stop_reason == "awaiting_approval"
AND: The file does NOT exist on disk
When: approve() is called
Then: stop_reason == "completed"
AND: The file EXISTS on disk with the expected content
```

#### Rejection Flow Integration Test

```
Scenario: Autonomous run discards changes on rejection

Given: A scripted plan that creates a file
When: run_autonomous() is called and the run pauses
And: reject() is called
Then: stop_reason == "rejected"
AND: The file does NOT exist on disk
```

#### Checkpoint Integration Test

```
Scenario: Checkpoint is created before write, restore reverts the write

Given: A file exists with content "original"
When: An autonomous run writes "modified" to the file
AND: The user restores the checkpoint taken before that write
Then: The file content is "original"
```

#### Cancellation Integration Test

```
Scenario: Cancellation mid-run leaves no partial state

Given: A scripted plan with multiple steps
When: cancel_event is set after step 1 completes
Then: stop_reason == "cancelled"
AND: No patches remain staged (ChangeManager is clean)
AND: context vars are cleared (set_active_patch_manager returns None)
```

#### Replan Integration Test

```
Scenario: Executor replans after a failed tool call

Given: A scripted plan whose first step raises ToolExecutionError
When: run_autonomous() is called
Then: The executor requests a replan
AND: The replan's steps are executed
AND: stop_reason != "fatal_error" (unless replan budget exhausted)
```

### 3.5 MCP Protocol Integration Tests

```
Scenario: initialize → tools/list → tools/call → shutdown

Given: MCPServer running with full registry
When: An initialize request is sent
Then: Response contains: protocolVersion, capabilities, serverInfo
When: A tools/list request is sent
Then: Response contains all 39 registered tools with correct schemas
When: A tools/call request is sent for "read_file"
Then: Response contains the file content
When: A shutdown request is sent
Then: Server terminates cleanly with no errors
```

---

## 4. End-to-End Testing

### 4.1 Definition

An end-to-end test exercises the entire stack as a user would experience it: the CLI
binary, the MCP server over real stdio, or the VS Code extension protocol. It does
not mock internals. It uses the `scripted` LLM provider to control the agent's
planning output while keeping every other component real.

### 4.2 CLI End-to-End Tests

**Test environment:**
- Real `pearl` binary (or `python -m src.main`)
- `PEARL_LLM_PROVIDER=scripted`
- A real temporary workspace directory

**Required E2E scenarios:**

#### CLI-E2E-01: Basic task execution

```
Steps:
1. Launch Pearl with a scripted plan that creates "hello.py"
2. Provide the prompt via stdin
3. When "Apply these changes? [y/N]" appears, send "y"
4. Verify "hello.py" exists in the workspace
5. Verify exit is clean (no traceback, no error message)

PASS criteria: File created, clean exit
FAIL criteria: Any exception traceback visible, file not created
```

#### CLI-E2E-02: Rejection workflow

```
Steps:
1. Launch Pearl with a scripted plan that creates "hello.py"
2. When "Apply these changes? [y/N]" appears, send "n"
3. Verify "hello.py" does NOT exist in the workspace
4. Verify "Cancelled" or "Rejected" confirmation is printed

PASS criteria: File not created, clean cancellation message
```

#### CLI-E2E-03: Checkpoint round-trip

```
Steps:
1. Create a file "original.py" in the workspace
2. Launch Pearl, run a task that overwrites "original.py"
3. Approve the write
4. Send ":checkpoints" — verify a checkpoint appears
5. Send ":restore <id>" and confirm
6. Verify "original.py" has reverted to its original content

PASS criteria: File content restored exactly
```

#### CLI-E2E-04: Cancellation via KeyboardInterrupt

```
Steps:
1. Launch Pearl with a scripted plan that has a slow step
2. Send SIGINT (Ctrl-C) during execution
3. Verify "Goodbye." is printed
4. Verify no files are left partially written in the workspace

PASS criteria: Clean "Goodbye.", workspace unchanged
```

#### CLI-E2E-05: Invalid command handling

```
Steps:
1. Launch Pearl
2. Enter ":unknown" — verify error message "Unknown command ':unknown'"
3. Enter an empty prompt — verify Pearl prompts again (no crash)
4. Enter "exit" — verify clean exit

PASS criteria: All three handled gracefully, no crash
```

### 4.3 MCP Server End-to-End Tests

These tests launch `python -m src.mcp` as a subprocess and communicate via its stdio.

**Required E2E scenarios:**

#### MCP-E2E-01: Full autonomous run with approval

```
Steps:
1. Start MCP server subprocess
2. Send initialize request
3. Send pearl/runAutonomous request
4. Wait for pearl/progress notifications; verify expected sequence
5. Verify final response has stop_reason="awaiting_approval"
6. Send pearl/approvePatches request
7. Verify response has stop_reason="completed"
8. Verify the file was written to disk
9. Send shutdown request

PASS criteria: Correct progress sequence, file written, clean shutdown
```

#### MCP-E2E-02: Concurrent request rejection

```
Steps:
1. Start MCP server subprocess
2. Send pearl/runAutonomous (async, do not wait)
3. Immediately send a second pearl/runAutonomous
4. Verify the second request returns an error response (not queued)

PASS criteria: Second request rejected with actionable error
```

#### MCP-E2E-03: Malformed request handling

```
Steps:
1. Start MCP server subprocess
2. Send a JSON-RPC request with missing "method" field
3. Verify the response is a valid JSON-RPC error with code -32600
4. Verify the server continues to process subsequent valid requests

PASS criteria: Error response received, server still functional
```

---

## 5. Regression Testing

### 5.1 Policy

**Rule REG-1:** Every bug fix MUST be accompanied by a regression test that:
1. Fails against the code before the fix
2. Passes against the code after the fix
3. Is labeled with the issue or sprint it addresses

**Rule REG-2:** Regression tests are permanent. They are never deleted unless the
feature they test is intentionally removed.

**Rule REG-3:** When a regression test cannot be written (the failure mode is
hardware-dependent, timing-dependent, or requires manual observation), this MUST be
documented in the PR with an explanation.

### 5.2 Known Regression Tests

These tests were added to prevent re-introduction of specific past bugs:

| Test name | Bug prevented | Sprint fixed |
|---|---|---|
| `test_planner_run_removed` | `Planner.run()` bypassed ChangeManager | Sprint 0 |
| `test_plan_and_run_removed` | `PearlAgent.plan_and_run()` bypassed ChangeManager | Sprint 0 |
| `test_pearl_plan_mcp_method_removed` | `pearl/plan` MCP method bypassed approval | Sprint 0 |
| `test_has_tool_removed_from_dispatcher` | `has_tool()` raised instead of returning False | Sprint 0 |
| `test_context_vars_cleared_on_cancel` | Active ChangeManager leaked into next run | Sprint 0 |
| `test_emoji_modes_all_distinct` | Three of four emoji modes were identical | Sprint 0 |
| `test_search_results_capped_at_200` | Unbounded search results crashed planner context | Sprint 0 |

### 5.3 Regression Test Template

```python
def test_<bug_name>_regression():
    """
    Regression: <brief description of the bug that was fixed>
    Fixed in Sprint N / Issue #NNN.
    """
    # Arrange: set up the conditions that triggered the bug
    ...

    # Act: perform the action that previously triggered the bug
    ...

    # Assert: verify the bug does not occur
    with pytest.raises(AttributeError):  # or assert X, or assert not Y
        ...
```

---

## 6. Performance Testing

### 6.1 What is Measured

Performance testing verifies that Pearl's core operations meet the latency targets
defined in `02_CODING_STANDARDS.md`, Section 15.1.

| Operation | Target | Measurement |
|---|---|---|
| Repository indexing (small corpus, < 100 files) | < 500ms | wall clock |
| Repository indexing (medium corpus, ~5,000 files) | < 5s | wall clock |
| `read_file` (100 KB file) | < 50ms | wall clock |
| `search_text` (medium corpus, common term) | < 1s | wall clock |
| `find_references` (medium corpus) | < 2s | wall clock |
| Patch staging + application (10-file diff) | < 100ms | wall clock |
| Checkpoint creation (clean state) | < 200ms | wall clock |

### 6.2 Benchmark Runner

```bash
pytest tests/performance/ -v --benchmark-only --benchmark-json=benchmarks/results/$(date +%F).json
```

Each benchmark result is saved to `benchmarks/results/` as a dated JSON file.
Results are compared against the prior sprint's baseline automatically.

### 6.3 Performance Test Structure

```python
def test_bench_indexing_small_corpus(benchmark, small_corpus_path):
    """Benchmark repository indexing on a small corpus."""
    result = benchmark(index_repository, str(small_corpus_path))
    # Functional assertion — the benchmark also verifies correctness
    assert result["file_count"] > 0

def test_bench_search_text_medium_corpus(benchmark, medium_corpus_path):
    """Benchmark text search on a medium corpus."""
    # Ensure index is warm
    index_repository(str(medium_corpus_path))
    result = benchmark(search_text, str(medium_corpus_path), "def ")
    assert len(result) > 0
```

### 6.4 Regression Detection

After each sprint benchmark run, the CI pipeline compares current results against the
prior sprint baseline:

```python
# scripts/check_perf_regression.py
THRESHOLDS = {
    "indexing_small": 0.10,   # 10% regression blocks sprint close
    "indexing_medium": 0.20,
    "search_text": 0.15,
    "patch_apply": 0.15,
}
```

If a threshold is exceeded, CI prints a detailed comparison and exits non-zero.

---

## 7. Stress Testing

### 7.1 Purpose

Stress tests verify that Pearl behaves correctly under conditions beyond its normal
design parameters: very large repositories, very long sessions, many consecutive
runs, and memory pressure.

### 7.2 Stress Test Corpus

The large test corpus (`tests/helpers/corpus/large/`) contains approximately 50,000
files generated to mimic a realistic monorepo structure. It is not committed to git —
it is generated by `scripts/generate_large_corpus.py` and cached locally.

```bash
python scripts/generate_large_corpus.py --files 50000 --output tests/helpers/corpus/large/
```

### 7.3 Required Stress Tests

#### ST-01: Large Repository Indexing

```
Given: A corpus of 50,000 files
When: index_repository() is called
Then: Completes within 120 seconds
AND: No MemoryError raised
AND: Peak RSS growth < 500 MB above baseline
AND: Result is functionally correct (spot-check 100 random files)
```

#### ST-02: Long Running Session

```
Given: Pearl running in CLI mode
When: 50 consecutive autonomous tasks are executed (scripted, no LLM)
AND: Each task creates 2–5 files
Then: No memory leak (RSS does not grow > 100 MB over the session)
AND: All 50 tasks complete with stop_reason="completed"
AND: All checkpoint IDs remain unique and resolvable
```

#### ST-03: Maximum Search Results

```
Given: A corpus where searching for a common pattern returns > 200 matches
When: search_text() is called with that pattern
Then: Exactly MAX_SEARCH_RESULTS results returned (not more)
AND: A truncation notice is appended as the last item
AND: No exception raised
AND: Completes in < 5 seconds
```

#### ST-04: Replan Exhaustion

```
Given: A scripted plan where every tool call fails
When: run_autonomous() is called
Then: Executor replans exactly DEFAULT_MAX_REPLANS times
AND: stop_reason == "fatal_error"
AND: No infinite loop; run terminates
AND: Context vars are cleaned up
```

#### ST-05: Context Variable Isolation Under Threads

```
Given: Two simultaneous AutonomousExecutor instances in different threads
(simulating a multi-client scenario)
When: Both run concurrently with scripted plans
Then: Each thread's ChangeManager affects only its own staged patches
AND: No cross-thread state contamination is observed
AND: Both runs complete without exceptions
```

---

## 8. Security Testing

### 8.1 Purpose

Security tests verify that Pearl's safety boundaries hold against adversarial inputs.
These tests are not hypothetical — they test against specific attack vectors that
autonomous coding agents are known to be vulnerable to.

### 8.2 Workspace Boundary Tests

Every test in this section MUST be in `tests/security/test_workspace_boundary.py`.

#### SEC-WB-01: Simple path traversal

```python
@pytest.mark.parametrize("path", [
    "../outside.py",
    "../../etc/passwd",
    "/absolute/path.py",
    "sub/../../../outside.py",
])
def test_write_file_rejects_path_traversal(path, tmp_workspace):
    with pytest.raises(WorkspaceBoundaryError):
        write_file(path, "content")
```

#### SEC-WB-02: Symlink traversal

```
Given: A symlink inside the workspace pointing to a file outside the workspace
When: read_file() is called on the symlink path
Then: WorkspaceBoundaryError is raised (symlink target is outside workspace)
```

#### SEC-WB-03: Unicode normalization attack

```
Given: A path using unicode characters that normalize to "../"
(e.g., using Unicode lookalike characters)
When: write_file() is called with this path
Then: WorkspaceBoundaryError is raised (path normalizes to outside workspace)
```

#### SEC-WB-04: All write tools enforce boundary

```
For each tool in [create_file, write_file, append_file, replace_in_file,
                  edit_lines, patch_file, replace_function, replace_class,
                  insert_after_symbol, insert_before_symbol]:
    Given: A path that escapes the workspace
    Then: WorkspaceBoundaryError is raised
```

### 8.3 Shell Injection Tests

Every test in this section MUST be in `tests/security/test_shell_injection.py`.

#### SEC-SI-01: Dangerous pattern rejection

```python
@pytest.mark.parametrize("command,reason", [
    ("rm -rf /", "root filesystem destruction"),
    ("rm -rf /*", "root filesystem destruction via glob"),
    (":(){ :|:& };:", "fork bomb"),
    ("sudo rm -rf /", "sudo escalation"),
    ("dd if=/dev/zero of=/dev/sda", "disk wipe"),
    ("shutdown -h now", "system shutdown"),
])
def test_execute_shell_blocks_dangerous_commands(command, reason):
    with pytest.raises((ValueError, PermissionError)):
        execute_shell(command)
```

#### SEC-SI-02: Dangerous pattern via concatenation

```
Given: A command that looks benign but constructs a dangerous string via eval
e.g.: "python -c 'import os; os.system(\"rm -rf /\")'"
Then: If the dangerous substring is present, it is blocked
```

#### SEC-SI-03: Allowed commands are not over-blocked

```python
@pytest.mark.parametrize("command", [
    "git status",
    "python --version",
    "ls -la",
    "cat README.md",
    "rm -rf ./build",  # safe: specific directory, not root
])
def test_execute_shell_allows_safe_commands(command, tmp_workspace):
    # Must not raise — only verify the command is accepted (not necessarily succeeds)
    result = execute_shell(command, cwd=str(tmp_workspace))
    assert "blocked" not in result.lower()
```

### 8.4 Credential Exposure Tests

`tests/security/test_credential_exposure.py`

#### SEC-CE-01: API keys do not appear in log output

```
Given: Settings with a non-empty OPENAI_API_KEY
When: The LLM client is initialized and a generate() call is made
Then: No log record at any level contains the raw API key value
```

#### SEC-CE-02: Credentials not staged in patches

```
Given: A file containing a credential string
When: write_file() stages the file in a ChangeManager
Then: The raw credential appears only in the diff (expected)
AND: It is not additionally logged anywhere
```

#### SEC-CE-03: Settings redacts secrets in repr/str

```
Given: Settings has a non-empty OPENAI_API_KEY
When: str(Settings) or repr(Settings) is called
Then: The output does not contain the raw key value
```

---

## 9. Filesystem Validation Testing

### 9.1 Purpose

Filesystem validation tests verify that Pearl's file operations are correct, atomic
where required, encoding-safe, and permission-aware.

### 9.2 Required Tests

#### FS-01: File encoding roundtrip

```python
@pytest.mark.parametrize("content", [
    "hello world",
    "αβγδ — unicode greek letters",
    "中文字符",
    "emoji: 🐍🦀",
    "\r\n mixed line endings \n",
    "null byte: \x00 (should be handled gracefully)",
])
def test_write_and_read_file_encoding_roundtrip(content, tmp_workspace):
    path = str(tmp_workspace / "test.txt")
    write_file(path, content)
    result = read_file(path)
    assert result == content
```

#### FS-02: Atomic patch application

```
Given: ChangeManager with a staged diff that modifies 3 files
When: apply_all() is called
Then: Either all 3 files are written or none are (no partial state)
AND: A simulated write failure mid-apply leaves the workspace unchanged
```

#### FS-03: Directory creation is idempotent

```
Given: A directory that already exists
When: make_directory() is called on that path
Then: No exception is raised
AND: Existing contents are undisturbed
```

#### FS-04: Large file truncation

```
Given: A file larger than MAX_FILE_SIZE_BYTES
When: read_file() is called
Then: The returned content is truncated at MAX_FILE_SIZE_BYTES
AND: The return value includes a truncation notice
AND: No MemoryError is raised
```

#### FS-05: Missing parent directory creation

```
Given: A path with non-existent parent directories
When: write_file() is called (or create_file() with create_parents=True)
Then: Parent directories are created
AND: The file is written successfully
```

#### FS-06: Concurrent write safety

```
Given: Two threads attempting to write to the same file via ChangeManager
When: Both call apply_all() concurrently
Then: One wins, one gets a conflict error
AND: The file is not corrupted
```

---

## 10. Repository Testing

### 10.1 Purpose

Repository tests verify that Pearl's `RepositoryIndex`, symbol search, and git
operations work correctly across different repository shapes and states.

### 10.2 Repository Fixtures

```python
# tests/helpers/corpus/
# small/     — a clean Python project with ~80 files, typical structure
# medium/    — a project with ~5,000 files, multiple packages, mixed languages
# large/     — generated 50,000-file corpus
# empty/     — an empty directory (not a git repo)
# git_repo/  — a git-initialized repo with commits, branches, stash
# dirty_git/ — a git repo with uncommitted changes and untracked files
```

### 10.3 Required Tests

#### REPO-01: Index consistency after writes

```
Given: A repository indexed at state A
When: A file is written (creating new symbols)
AND: refresh_indexed_file(path) is called
Then: find_symbol() returns the new symbol
AND: The prior state's symbols are no longer returned for deleted symbols
```

#### REPO-02: Index with IGNORED_DIRS

```
Given: A repository with a .git/, __pycache__/, and node_modules/ directory
When: index_repository() is called
Then: No files from those directories appear in the index
AND: Symbols from outside those directories are indexed correctly
```

#### REPO-03: find_references accuracy

```
Given: A file that defines a class Foo and another file that imports it
When: find_references("Foo") is called
Then: The importing file appears in the results
AND: The defining file does NOT appear (or is marked as definition, not reference)
```

#### REPO-04: Index on empty repository

```
Given: An empty directory (no Python files)
When: index_repository() is called
Then: No exception is raised
AND: file_count == 0, symbol_count == 0
```

#### REPO-05: Git status accuracy

```
Given: A git repo with one modified tracked file and one untracked file
When: git_status() is called
Then: The modified file appears in the modified list
AND: The untracked file appears in the untracked list
AND: No other files appear
```

#### REPO-06: git_restore reverts changes

```
Given: A file "foo.py" with content "original"
When: foo.py is overwritten with "modified"
AND: git_restore("foo.py") is called
Then: foo.py content is "original"
```

---

## 11. Multi-Language Testing

### 11.1 Purpose

Pearl is a two-language system: Python backend and TypeScript extension. Multi-language
tests verify that the protocol between them is correct and that both sides of the
boundary agree on the message format.

### 11.2 Python Test Suite

```bash
pytest tests/ -v --cov=src --cov-report=term-missing
```

Target: 694+ tests passing, 90%+ coverage (current baseline: 694 tests).

### 11.3 TypeScript Test Suite

```bash
cd vscode-extension && npm test
```

Target: 239+ tests passing (current baseline: 239 TypeScript tests).

### 11.4 Protocol Contract Tests

These tests run from the Python side and verify that the protocol shapes the extension
expects are exactly what the server produces.

#### ML-01: tools/list response shape

```python
def test_tools_list_matches_extension_schema():
    """Verify tools/list response matches the TypeScript ToolDefinition interface."""
    server = MCPServer(build_registry())
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    for tool in response["result"]["tools"]:
        assert "name" in tool
        assert "description" in tool
        assert "inputSchema" in tool
        assert tool["inputSchema"]["type"] == "object"
        assert "properties" in tool["inputSchema"]
```

#### ML-02: pearl/progress notification shape

```python
def test_progress_notification_has_no_id():
    """pearl/progress is a notification — it MUST NOT have an id field."""
    notifications = []
    server = MCPServer(build_registry(), on_notification=notifications.append)
    server.handle_run_autonomous({"prompt": "create hello.py"})
    for note in notifications:
        assert "id" not in note, "Notification must not have an id field"
        assert note.get("method") == "pearl/progress"
```

#### ML-03: Error response codes

```python
@pytest.mark.parametrize("request,expected_code", [
    ({"jsonrpc": "2.0", "id": 1},                   -32600),  # missing method
    ({"jsonrpc": "2.0", "id": 1, "method": "x/y"},  -32601),  # unknown method
    ({"jsonrpc": "2.0", "id": 1, "method": "tools/call"},  -32602),  # missing params
])
def test_mcp_error_codes(request, expected_code):
    server = MCPServer(build_registry())
    response = server.handle(request)
    assert response["error"]["code"] == expected_code
```

### 11.5 Cross-Language Schema Sync

**Rule ML-1:** Whenever a new field is added to a Pearl MCP response, the
corresponding TypeScript interface in `vscode-extension/src/types.ts` MUST be
updated in the same PR. The Python test that verifies the response shape and the
TypeScript interface definition are considered a single artifact.

---

## 12. Persona Testing

### 12.1 Purpose

Persona testing validates Pearl from the perspective of four distinct user archetypes.
It is conducted manually (with scripted steps) every sprint and before every release.
It simulates real-world use cases that automated tests cannot fully capture.

### 12.2 The Four Personas

| Persona | Profile | Primary concern |
|---|---|---|
| **New User** | First time using Pearl; no knowledge of architecture | Does it work? Is it clear? |
| **Daily Developer** | Uses Pearl every day for routine coding tasks | Is it fast? Does approval work? |
| **Power User** | Large repos, stress testing, edge cases | Does it scale? Does it stay correct? |
| **Failure Tester** | Deliberately attempts to break Pearl | Does it stay safe under adversarial use? |

### 12.3 Persona 1: New User

**Setup:** Fresh environment, no `.env`, `PEARL_LLM_PROVIDER=scripted`

| Step | Action | Expected result | PASS/FAIL |
|---|---|---|---|
| P1-01 | Clone repo, run `pip install -e .` | Installs with no errors | |
| P1-02 | Run `pearl` with no config | Clear error: provider not configured, with guidance | |
| P1-03 | Set `PEARL_LLM_PROVIDER=scripted`, run `pearl` | REPL starts, shows tool count, shows checkpoint help | |
| P1-04 | Type "hello" (invalid task for scripted) | Clear response or graceful failure — no traceback | |
| P1-05 | Type "exit" | Prints "Goodbye.", clean exit | |
| P1-06 | Read the first 10 lines of help output | Key commands visible; approval workflow mentioned | |

**Verdict:** PASS if all 6 steps pass.

### 12.4 Persona 2: Daily Developer

**Setup:** Configured environment, `PEARL_LLM_PROVIDER=scripted`, test workspace

| Step | Action | Expected result | PASS/FAIL |
|---|---|---|---|
| P2-01 | Run task that creates a file | Approval prompt appears | |
| P2-02 | Review the staged diff | Diff is readable; path and content are correct | |
| P2-03 | Approve the change | File appears on disk; "completed" confirmed | |
| P2-04 | Run task that modifies a file | Approval prompt appears with unified diff | |
| P2-05 | Reject the change | File unchanged; "Cancelled." printed | |
| P2-06 | Run `:checkpoint` manually | Checkpoint ID printed; `:checkpoints` shows it | |
| P2-07 | Modify a file, then `:restore <id>` | File reverted; confirmation printed | |
| P2-08 | Run a shell command task | `CommandApprovalManager` approval prompt appears | |
| P2-09 | Cancel mid-run with Ctrl-C | "Goodbye." printed; workspace unchanged | |

**Verdict:** PASS if all 9 steps pass.

### 12.5 Persona 3: Power User

**Setup:** Large corpus (~50,000 files), `PEARL_LLM_PROVIDER=scripted`

| Step | Action | Expected result | PASS/FAIL |
|---|---|---|---|
| P3-01 | `index_repository()` on large corpus | Completes in < 120s; no OOM | |
| P3-02 | `search_text()` for common term | Returns exactly 200 results + truncation notice | |
| P3-03 | Run 20 consecutive autonomous tasks | All complete; no memory leak (RSS < 200 MB growth) | |
| P3-04 | Create 100 checkpoints in one session | All unique IDs; `:checkpoints` lists all 100 | |
| P3-05 | Restore checkpoint #1 after 99 subsequent writes | Files restored to checkpoint #1 state exactly | |
| P3-06 | Run a task on a file with 10,000 lines | `read_file` truncates correctly; no crash | |
| P3-07 | Run a task with `max_iterations=1` | Run stops after 1 step; stop_reason="max_iterations" | |

**Verdict:** PASS if all 7 steps pass.

### 12.6 Persona 4: Failure Tester

**Setup:** Any environment. Goal: make Pearl do something unsafe or incorrect.

| Step | Action | Expected result | PASS/FAIL |
|---|---|---|---|
| P4-01 | Craft a prompt that tries to write to `../outside.py` | `WorkspaceBoundaryError`; file not written | |
| P4-02 | Attempt `rm -rf /` via execute_shell | Blocked by `_DANGEROUS_PATTERNS`; clear error | |
| P4-03 | Send malformed JSON to MCP server | JSON-RPC error response; server continues | |
| P4-04 | Call `pearl/approvePatches` with no pending run | Error response: "No run awaiting approval" | |
| P4-05 | Start a run, kill the process mid-approval | On restart, workspace is consistent (no partial write) | |
| P4-06 | Set `PEARL_LLM_PROVIDER=scripted`, provide a plan that calls an unknown tool | `ToolNotFoundError`; replanning triggered or fatal_error | |
| P4-07 | Stale lock file test | Clear, actionable error message guides resolution | |
| P4-08 | Send `pearl/runAutonomous` twice without waiting for completion | Second request rejected with a clear error | |

**Verdict:** PASS if all 8 steps produce the described safe behavior.

### 12.7 Persona Testing Report Template

See Section 17.3 for the persona testing report template.

---

## 13. Dogfooding

### 13.1 Definition

Dogfooding means using Pearl to develop Pearl. Contributors SHOULD use Pearl for
real coding tasks on the Pearl codebase itself — writing tests, refactoring, creating
new tools. This is the highest-fidelity test of Pearl's actual user experience.

### 13.2 Dogfooding Cadence

**Rule DOG-1:** At least one dogfooding session MUST be conducted before each sprint
closes. The session MUST be documented using the template in Section 17.4.

**Rule DOG-2:** Dogfooding sessions that surface bugs, incorrect behavior, or poor UX
MUST result in filed issues before the session is considered complete.

**Rule DOG-3:** Issues surfaced by dogfooding are classified P1 (must fix in current
or next sprint). Dogfooding is not a nice-to-have — it is a development workflow.

### 13.3 Dogfooding Session Protocol

```
Session start:
1. Choose a real task from the current sprint's backlog (not a synthetic task)
2. Note the workspace, Pearl version (git SHA), and LLM provider used
3. Use Pearl as you would any coding tool — don't baby it

During the session:
4. Note every moment of confusion, slowness, or incorrect behavior
5. Note every moment where Pearl works exactly as expected (positive signal)
6. When Pearl proposes a change: review the diff carefully before approving

Session end:
7. Complete the dogfooding report (Section 17.4)
8. File issues for every problem encountered
9. Note the task completion: completed / partially completed / abandoned
```

### 13.4 Prohibited in Dogfooding

- Using Pearl for a task you've already scripted deterministically — it must be a
  real task where you don't know the exact output in advance
- Skipping the approval review step (always read the diff before approving)
- Aborting the session when Pearl makes a mistake — the mistake is the data

---

## 14. Acceptance Testing

### 14.1 Sprint Acceptance Testing

Sprint acceptance testing is the verification that sprint deliverables meet their
stated acceptance criteria. It runs after all automated tests pass and before the
sprint is closed.

**Rule ACC-1:** Every story or task in a sprint MUST have at least one acceptance
criterion stated before implementation begins. Acceptance criteria are verified
manually or by automated test.

**Rule ACC-2:** Sprint acceptance testing is conducted by the architect or a
designated reviewer — not the author of the code being accepted.

### 14.2 Acceptance Criteria Format

```
Story: As a [user type], I want [capability] so that [outcome].

Acceptance Criteria:
AC-1: Given [state], when [action], then [observable outcome].
AC-2: Given [state], when [action], then [observable outcome].
...

Definition of Done:
- [ ] All ACs pass
- [ ] Regression tests added
- [ ] CHANGELOG updated
- [ ] Dogfooding session completed
```

### 14.3 Feature Acceptance Tests

Feature acceptance tests verify that a specific feature meets its acceptance criteria
in a controlled, reproducible scenario. They live in `tests/e2e/` and are labeled
with the story ID.

### 14.4 Release Acceptance Testing

Before every release, the full acceptance test suite MUST pass:

```bash
# Python tests
pytest tests/ -v --cov=src --cov-fail-under=90

# TypeScript tests
cd vscode-extension && npm test

# Security tests
pytest tests/security/ -v

# Persona tests (manual — use checklist from Section 12)
# All 4 personas: PASS verdict required

# Dogfooding session
# At least one session documented within the sprint
```

The release acceptance test report (Section 17.5) is completed before any release
tag is pushed.

---

## 15. Competitive Benchmarking

### 15.1 Purpose

Competitive benchmarking compares Pearl's performance and behavior against peer
tools. It is conducted every major release (not every sprint) and is never used as
a marketing artifact — it is an engineering input to guide priority decisions.

### 15.2 Comparison Methodology

**Rule CB-1:** All tools compared MUST be tested on identical hardware, with the same
LLM provider and model where possible, on the same corpus.

**Rule CB-2:** Tasks used for comparison MUST be real-world representative tasks, not
tasks that Pearl is known to excel at. The benchmark must test the tools fairly.

**Rule CB-3:** Results MUST note the version of each tool compared. Results from
different versions of the same tool are not comparable.

### 15.3 Benchmark Tasks

| Task | Metric | Notes |
|---|---|---|
| "Add a docstring to all public functions in file X" | Time to complete, correctness | Correctness: human review of output |
| "Create a new tool that searches for TODO comments" | Time to complete, correctness | Correctness: does the tool run and return correct results |
| "Fix the bug in function Y" | Time to first correct proposal | Correctness: does the fix actually fix the bug |
| "Index a 5,000-file repository" | Indexing time | Wall clock, same corpus for all tools |
| "Search for all usages of function Z" | Time + precision/recall | Count correct/incorrect/missing results |

### 15.4 Reporting

Competitive benchmark results are stored in `benchmarks/competitive/` as dated
markdown files. They MUST include:
- Tool versions
- Hardware and environment
- Raw results (not just summaries)
- Assessment: where Pearl is better, where it is worse, and why
- Action items: what Pearl should improve based on the comparison

Results are reviewed by the architect before any product communication references them.

---

## 16. Release Gates

### 16.1 Definition

A release gate is a mandatory check that must pass before a release is published.
Gates are either hard (automated, cannot be bypassed) or soft (manual verification
with sign-off).

### 16.2 Hard Gates (automated, CI-enforced)

| Gate | Check | Failure action |
|---|---|---|
| Python unit tests | `pytest tests/unit/ --cov-fail-under=90` | Block release |
| Python integration tests | `pytest tests/integration/` | Block release |
| Python security tests | `pytest tests/security/` | Block release |
| TypeScript tests | `npm test` in `vscode-extension/` | Block release |
| Ruff formatting | `ruff format --check src/` | Block release |
| Ruff lint | `ruff check src/` | Block release |
| Pyright type check | `pyright src/` (public interfaces) | Block release |
| Dependency audit | `pip-audit` + `npm audit --audit-level=high` | Block on critical/high |

### 16.3 Soft Gates (manual verification with sign-off)

| Gate | Verification | Sign-off required |
|---|---|---|
| Persona testing | All 4 personas: PASS verdict | Architect |
| Dogfooding session | Documented session, issues filed | Contributor + Architect |
| CHANGELOG complete | Every observable change documented | Architect |
| ADR coverage | Every architectural change has an ADR | Architect |
| Performance regression | No threshold exceeded from prior sprint | Architect |
| E2E tests | CLI and MCP E2E scenarios: all PASS | Reviewer |

### 16.4 Release Gate Checklist

```
Pre-release gate verification — complete before tagging any release:

Hard Gates (automated):
[ ] pytest tests/ — all passing, 90%+ coverage
[ ] pytest tests/security/ — all passing
[ ] npm test (vscode-extension) — all passing
[ ] ruff format --check — no violations
[ ] ruff check — no violations
[ ] pyright — no errors on public interfaces
[ ] pip-audit + npm audit — no critical/high vulnerabilities

Soft Gates (manual):
[ ] Persona 1 (New User) — PASS
[ ] Persona 2 (Daily Developer) — PASS
[ ] Persona 3 (Power User) — PASS
[ ] Persona 4 (Failure Tester) — PASS
[ ] Dogfooding session completed and documented
[ ] CHANGELOG reviewed — all changes present, user-perspective wording
[ ] ADR table in 01_ARCHITECTURE_RULES.md — all new ADRs added
[ ] Performance benchmarks compared — no threshold exceeded
[ ] E2E CLI and MCP scenarios — all PASS

Sign-off:
[ ] Architect: ______________ Date: __________
[ ] Reviewer: ______________ Date: __________
```

### 16.5 Zero Open P0/P1 Bugs

**Rule GATE-1:** No release MUST proceed with any open P0 (critical, data loss,
security) or P1 (major functionality broken) bugs. These MUST be resolved or
formally accepted as known issues with a documented workaround before the release
tag is applied.

**Rule GATE-2:** A bug discovered during persona testing or dogfooding within the
release window is classified P0 or P1 by default. The release is held until it is
resolved.

---

## 17. Reporting Templates

### 17.1 Sprint Test Report Template

```markdown
# Sprint N — Test Report
Date: YYYY-MM-DD
Author: [Name]
Branch: [branch name]
Python tests: [count passing] / [count total]
TypeScript tests: [count passing] / [count total]
Coverage: [X]%

## Automated Test Summary

| Suite | Tests | Pass | Fail | Skip | Duration |
|---|---|---|---|---|---|
| Unit | | | | | |
| Integration | | | | | |
| Security | | | | | |
| TypeScript | | | | | |

## New Regression Tests Added

- test_<name>: [what bug this prevents]

## Failing Tests (if any)

- test_<name>: [root cause], [resolution or deferral reason]

## Coverage Highlights

Areas with coverage < 80% (if any):
- src/[module].py: [X]% — [reason and plan]

## Notes

[Any observations about test quality, new gaps identified, or improvements made]
```

### 17.2 Performance Benchmark Report Template

```markdown
# Sprint N — Performance Benchmark Report
Date: YYYY-MM-DD
Hardware: [CPU, RAM, Storage]
OS: [OS and version]
Python: [version]
Key dependencies: [list]

## Results

| Operation | This sprint | Prior sprint | Change | Status |
|---|---|---|---|---|
| Indexing (small) | Xms | Xms | +/-Y% | ✓ / ⚠ / ✗ |
| Indexing (medium) | Xs | Xs | +/-Y% | ✓ / ⚠ / ✗ |
| search_text (medium) | Xms | Xms | +/-Y% | ✓ / ⚠ / ✗ |
| Tool: read_file (100KB) | Xms | Xms | +/-Y% | ✓ / ⚠ / ✗ |
| Patch apply (10 files) | Xms | Xms | +/-Y% | ✓ / ⚠ / ✗ |

Legend: ✓ within threshold ⚠ investigate ✗ threshold exceeded

## Regressions Investigated

[Any threshold exceedances and their root causes]

## Action Items

[Performance improvements planned for next sprint]
```

### 17.3 Persona Testing Report Template

```markdown
# Sprint N — Persona Testing Report
Date: YYYY-MM-DD
Tester: [Name]
Environment: [OS, Python version, LLM provider, commit SHA]

## Persona 1: New User

| Step | Action | Expected | Actual | PASS/FAIL |
|---|---|---|---|---|
| P1-01 | | | | |
...
**Overall verdict: PASS / FAIL**
Notes: [observations, issues filed]

## Persona 2: Daily Developer
[same table structure]
**Overall verdict: PASS / FAIL**

## Persona 3: Power User
[same table structure]
**Overall verdict: PASS / FAIL**

## Persona 4: Failure Tester
[same table structure]
**Overall verdict: PASS / FAIL**

## Release Recommendation

[ ] APPROVE FOR RELEASE — all personas PASS, no blocking issues
[ ] REJECT — [list of FAIL steps and filed issue numbers]

Sign-off: [Name] — [Date]
```

### 17.4 Dogfooding Session Report Template

```markdown
# Dogfooding Session — Sprint N
Date: YYYY-MM-DD
Contributor: [Name]
Task: [real task description — not a synthetic one]
Workspace: [project being worked on]
Pearl commit: [git SHA]
LLM provider: [provider and model]
Session duration: [minutes]

## Task Outcome
[ ] Completed successfully
[ ] Partially completed — [what was left]
[ ] Abandoned — [reason]

## Moments of Friction

1. [Describe what was confusing, slow, or incorrect]
   → Filed as: [issue # or "will file"]

2. [...]

## Moments That Worked Well

1. [Describe what worked exactly as expected or better]

## Issues Filed

- #NNN: [brief description]
- #NNN: [brief description]

## Recommendation

[ ] No changes needed from this session
[ ] Improvements identified — see issues above
```

### 17.5 Release Acceptance Test Report Template

```markdown
# Release [version] — Acceptance Test Report
Date: YYYY-MM-DD
Release candidate: [commit SHA]
Tested by: [Name(s)]

## Hard Gates

| Gate | Result | Notes |
|---|---|---|
| Python tests | PASS / FAIL | |
| Coverage | [X]% | |
| Integration tests | PASS / FAIL | |
| Security tests | PASS / FAIL | |
| TypeScript tests | PASS / FAIL | |
| Ruff format | PASS / FAIL | |
| Ruff lint | PASS / FAIL | |
| Pyright | PASS / FAIL | |
| Dependency audit | PASS / FAIL | |

## Soft Gates

| Gate | Result | Notes |
|---|---|---|
| Persona 1 (New User) | PASS / FAIL | |
| Persona 2 (Daily Developer) | PASS / FAIL | |
| Persona 3 (Power User) | PASS / FAIL | |
| Persona 4 (Failure Tester) | PASS / FAIL | |
| Dogfooding | Completed | [link to session report] |
| CHANGELOG | Complete | |
| ADR coverage | Complete | |
| Performance | No regressions | |

## Known Issues

| Priority | Issue | Mitigation |
|---|---|---|
| P2 | #NNN: [description] | [workaround] |

## Release Decision

[ ] APPROVED FOR RELEASE
[ ] HELD — [blocking issue(s)]

Architect sign-off: ______________ Date: __________
Reviewer sign-off: ______________ Date: __________
```

---

## 18. PASS / FAIL Criteria

### 18.1 Unit Tests

| Criterion | PASS | FAIL |
|---|---|---|
| All tests | Zero failures | Any failure |
| Coverage | ≥ 90% | < 90% |
| Flaky tests | 0 | Any test that fails intermittently |
| Suite duration | < 60 seconds | > 60 seconds (investigate) |
| Approval invariant tests | All pass | Any failure — P0 bug |

### 18.2 Integration Tests

| Criterion | PASS | FAIL |
|---|---|---|
| All tests | Zero failures | Any failure |
| Approval flow | File absent before approve; present after | Either assertion wrong |
| Rejection flow | File absent before and after rejection | File written |
| Cancellation | Context vars cleared; no staged patches | Any leak |
| Replan | Correct replan count; expected stop_reason | Wrong count or stop_reason |
| MCP protocol | All required response fields present | Any missing or malformed field |

### 18.3 End-to-End Tests

| Criterion | PASS | FAIL |
|---|---|---|
| CLI scenarios | All 5 E2E scenarios produce expected output | Any exception traceback visible |
| MCP scenarios | All 3 E2E scenarios produce expected behavior | Server crash or wrong response |
| Approval workflow | File state matches expected at each step | Any mismatch |
| Clean exit | No dangling processes, no error messages | Any process hang |

### 18.4 Security Tests

| Criterion | PASS | FAIL |
|---|---|---|
| Path traversal | `WorkspaceBoundaryError` raised for all traversal paths | Any traversal succeeds |
| Shell injection | All dangerous patterns blocked | Any dangerous command executes |
| Safe commands | Allowed commands not over-blocked | Legitimate commands rejected |
| Credential exposure | No keys in log output | Any raw key value in logs |

### 18.5 Performance Tests

| Criterion | PASS | FAIL |
|---|---|---|
| Indexing time | Within +10% of prior sprint | > +20% regression |
| Tool latency (p50) | Within +5% of prior sprint | > +15% regression |
| Tool latency (p95) | Within +10% of prior sprint | > +25% regression |
| Suite duration | < 5 minutes | > 5 minutes |

### 18.6 Stress Tests

| Criterion | PASS | FAIL |
|---|---|---|
| Large corpus indexing | Completes in < 120s, no OOM | Timeout or OOM |
| Long session (50 tasks) | RSS growth < 100 MB, all tasks complete | Memory leak or task failure |
| Search result cap | Exactly 200 results + truncation notice | More than 200 results |
| Replan exhaustion | Clean fatal_error after DEFAULT_MAX_REPLANS | Infinite loop or crash |

### 18.7 Persona Tests

| Criterion | PASS | FAIL |
|---|---|---|
| Persona 1 | All 6 steps produce expected results | Any step produces unexpected behavior |
| Persona 2 | All 9 steps produce expected results | Any step produces unexpected behavior |
| Persona 3 | All 7 steps produce expected results | Any step produces unexpected behavior |
| Persona 4 | All 8 steps produce safe behavior | Any step produces unsafe behavior |
| Overall recommendation | APPROVE FOR RELEASE | REJECT |

### 18.8 Release Gate PASS/FAIL

A release PASSES all gates when:
- All hard gates: PASS
- All four persona verdicts: PASS
- Dogfooding session: documented, all issues filed
- CHANGELOG: complete
- Zero open P0/P1 bugs
- Performance: no threshold exceeded
- Architect sign-off: obtained

A release FAILS if ANY of the above conditions is not met. There is no partial PASS.

---

## 19. Testing Review Checklist

Use this checklist when reviewing any PR that adds or modifies tests.

### Test Quality

- [ ] Tests are in the correct suite (unit/integration/e2e/security)
- [ ] Each test has a single, clear assertion target
- [ ] Test names follow `test_<what>_<condition>_<expected>` format
- [ ] `pytest.raises()` used for exception assertions (not bare `try/except`)
- [ ] Mocks have `spec=` set to the real object they replace
- [ ] Mocks applied at the import site (where the name is USED)

### Coverage

- [ ] New behavior covered by at least one test
- [ ] Bug fix has a regression test that would have caught the original bug
- [ ] Edge cases covered: empty input, None, boundary values
- [ ] Error paths covered: each documented exception has a test

### Safety Invariant Tests

- [ ] Approval invariant: file absent before approve, present after (integration test)
- [ ] Rejection: file absent before and after rejection (integration test)
- [ ] Cancellation: context vars cleared, no staged patches remain

### Determinism

- [ ] No `time.sleep()` in tests (use events or mocked time)
- [ ] No random values without a fixed seed
- [ ] No network calls (use scripted provider or mocks)
- [ ] No dependency on file system state outside `tmp_path`/`tmp_workspace`

### Performance

- [ ] Unit tests estimated to run in < 100ms each
- [ ] No unnecessary database or file I/O in unit tests
- [ ] Integration tests use `scripted` provider, not real LLM

### Security Tests

- [ ] Path traversal tests cover all write tools
- [ ] Shell injection tests cover known dangerous patterns
- [ ] Allowlisted commands verified not to be over-blocked

### Reports

- [ ] Performance benchmarks include hardware provenance
- [ ] Persona testing report completed using template in Section 17.3
- [ ] Dogfooding session documented using template in Section 17.4

### Mutation Testing

- [ ] Mutation score ≥ 80% for safety-critical modules (`executor`, `patch_manager`, `file_tools`)
- [ ] Surviving mutants documented; new tests written to kill each survivor
- [ ] `mutmut` run invoked before sprint close on changed modules

### Fuzz Testing

- [ ] Hypothesis fuzz targets cover all external boundary parsers (path inputs, MCP messages, shell commands)
- [ ] No uncaught exceptions from any fuzz run — only specific, expected exception types
- [ ] New fuzz-discovered edge case has a corresponding unit regression test

### Chaos Engineering

- [ ] LLM provider failure scenario tested (raises replanning or fatal_error correctly)
- [ ] Filesystem write failure tested (ChangeManager rolls back all writes)
- [ ] Subprocess timeout tested (no zombie processes; ToolExecutionError recorded)
- [ ] Context var state is clean after each chaos scenario

### Snapshot and Golden Output

- [ ] Snapshot files committed to git (not generated on the fly)
- [ ] PR that changes a snapshot explains why the output changed
- [ ] Golden files never auto-updated — changes require explicit PR justification
- [ ] All snapshot assertions use `syrupy` plugin, not hand-rolled string comparison

### Compatibility

- [ ] All unit and integration tests pass on the full Python version matrix (3.10, 3.11, 3.12)
- [ ] No Windows-only exclusions added without documented justification
- [ ] Path-related tests explicitly cover Windows separator behavior

### Upgrade and Migration

- [ ] Checkpoint format from prior release loads correctly under current code
- [ ] If any Settings key was renamed, migration path is tested
- [ ] Migration scripts are idempotent (running twice produces the same result)

---

## 20. Mutation Testing

### 20.1 Purpose

Mutation testing assesses the quality of the test suite itself. It works by introducing
small, controlled changes (mutations) into the source code — flipping a `<` to `<=`,
negating a condition, deleting a statement — and verifying that the test suite detects
each change. A mutation that is not caught by any test is a "surviving mutant," which
reveals a gap where a real bug could hide undetected.

### 20.2 Framework

- **Tool:** `mutmut` (Python)
- **Execution:**
  ```bash
  mutmut run --paths-to-mutate src/agent/executor.py --tests-dir tests/unit/
  mutmut results
  mutmut html  # detailed surviving-mutant report
  ```

### 20.3 Mutation Operators

| Operator | What it does | Example |
|---|---|---|
| AOR (Arithmetic) | Replace `+` with `-`, `*` with `//` | `len(steps) + 1` → `len(steps) - 1` |
| ROR (Relational) | Replace `<` with `<=`, `==` with `!=` | `if count >= MAX` → `if count > MAX` |
| COI (Condition) | Insert boolean negation | `if is_active` → `if not is_active` |
| SDL (Statement) | Delete a statement | Removes `set_active_patch_manager(None)` |
| CR (Constant) | Replace numeric constant | `200` → `201` |

### 20.4 Priority Modules

Not all code warrants mutation testing at the same frequency. Prioritize modules where
a surviving mutant represents a real safety risk:

| Priority | Modules | Target mutation score |
|---|---|---|
| **Critical** | `executor.py`, `patch_manager.py`, `file_tools.py`, `shell_tools.py` | ≥ 80% |
| **High** | `planner.py`, `dispatcher.py`, `agent.py`, `checkpoints.py` | ≥ 70% |
| **Standard** | All other `src/` modules | ≥ 60% |

**Rule MUT-1:** Mutation testing on critical-priority modules MUST be run before each
sprint close. Surviving mutants with a mutation score below threshold MUST be addressed
(either by adding a test or by documenting why the mutant is equivalent).

### 20.5 Equivalent Mutants

An equivalent mutant is a mutation that changes the code without changing observable
behavior (e.g., reordering two independent assignments). These are documented in
`tests/mutation/equivalent_mutants.md` and excluded from the score calculation.

**Rule MUT-2:** A mutant MAY be marked equivalent only after demonstrating — with a
written argument — that no observable behavior changes under the mutation. Marking a
mutant equivalent without justification is not permitted.

### 20.6 Interpreting Results

```
Mutation score = (killed mutants) / (total mutants - equivalent mutants) × 100
```

A module with a mutation score of 60% means 40% of introduced bugs would not be
caught by the current test suite. For `patch_manager.py` — the approval invariant's
chokepoint — this is unacceptable. For a logging utility, it may be acceptable.

### 20.7 Integration with CI

Mutation testing is NOT run on every commit (it is slow — typically 5–20 minutes per
module). It runs:
- **Before sprint close:** on all modules changed in the sprint
- **Before releases:** on all critical-priority modules
- **On demand:** via `make mutation-test MODULE=src/agent/executor.py`

---

## 21. Fuzz Testing

### 21.1 Purpose

Fuzz testing (fuzzing) sends random, malformed, or structurally unexpected inputs to
functions and verifies that they handle all inputs without crashing, producing security
failures, or violating their contracts. Fuzzing finds the inputs that unit tests didn't
think to write.

### 21.2 Two-Tier Approach

#### Tier 1: Property-Based Testing (Hypothesis) — runs in CI

`Hypothesis` generates structured random inputs guided by property specifications.
It is fast enough to run on every commit.

```python
from hypothesis import given, strategies as st

@given(path=st.text())
def test_ensure_within_workspace_never_crashes(path, tmp_workspace):
    """_ensure_within_workspace must raise WorkspaceBoundaryError or return a Path.
    It must never raise any other exception type."""
    try:
        _ensure_within_workspace(path, workspace_root=tmp_workspace)
    except WorkspaceBoundaryError:
        pass  # expected — path was outside workspace
    except Exception as exc:
        pytest.fail(f"Unexpected exception type: {type(exc).__name__}: {exc}")

@given(command=st.text())
def test_dangerous_pattern_check_never_crashes(command):
    """_check_dangerous_patterns must return bool for any string input."""
    result = _check_dangerous_patterns(command)
    assert isinstance(result, bool)

@given(data=st.binary())
def test_mcp_message_parser_handles_arbitrary_bytes(data):
    """MCP parser must not crash on arbitrary byte sequences."""
    try:
        _parse_mcp_message(data.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, MCPProtocolError):
        pass  # expected
    except Exception as exc:
        pytest.fail(f"Unexpected exception: {type(exc).__name__}: {exc}")
```

#### Tier 2: Coverage-Guided Fuzzing (atheris) — runs on-demand

`atheris` instruments the Python bytecode and drives the fuzzer toward new coverage
paths. Run before releases and on security-sensitive modules.

```bash
pip install atheris
python tests/fuzz/fuzz_mcp_parser.py corpus/mcp/
```

### 21.3 Fuzz Targets

| Target function | Input type | What a failure looks like |
|---|---|---|
| `_ensure_within_workspace(path)` | Arbitrary string | Unexpected exception (not WorkspaceBoundaryError) |
| `_check_dangerous_patterns(cmd)` | Arbitrary string | Exception or wrong return type |
| MCP message parser | Arbitrary byte sequences | Unhandled exception; server crash |
| `execute_shell(command)` | Arbitrary command string | Exception type other than ValueError/PermissionError |
| `_parse_tool_call(json_str)` | Malformed JSON | Crash instead of parse error |
| `Path(user_input).resolve()` | Adversarial path strings | Any exception other than `ValueError` |

### 21.4 Fuzz Corpus Management

Interesting inputs discovered by the fuzzer MUST be saved to the corpus directory
(`tests/fuzz/corpus/<target>/`) and committed to git. These inputs seed future fuzz
runs, preserving coverage gains across sessions.

**Rule FUZZ-1:** A crashing input found by the fuzzer is a P0 or P1 bug. Fix it before
continuing any other sprint work.

**Rule FUZZ-2:** Every fuzz-discovered edge case MUST have a corresponding, named unit
regression test that is deterministic and runs in CI. The fuzz run found it; the unit
test prevents it from regressing.

### 21.5 PASS / FAIL Criteria

| Criterion | PASS | FAIL |
|---|---|---|
| Hypothesis suite | All property tests pass | Any property test fails or finds a crash |
| atheris run (30 min) | No new crashes; corpus grows | Any crash found |
| Regression unit tests | All pass | Any failure |

---

## 22. Chaos Engineering

### 22.1 Purpose

Chaos engineering deliberately injects failures into the live system to verify that
recovery paths work correctly under real failure conditions. Unlike unit tests that
mock failures at a single call site, chaos tests inject failures at the system level
and verify the full recovery chain.

### 22.2 Pearl-Specific Chaos Scenarios

#### CHAOS-01: LLM Provider Failure Mid-Run

```
Setup:    Scripted plan with 3 steps; inject LLMProviderError after step 1 completes.
Inject:   Monkeypatch LLMClient.generate_json to raise LLMProviderError on the 2nd call.
Expected:
  - Executor catches the error and attempts a replan
  - Replan budget (DEFAULT_MAX_REPLANS) is respected
  - If replanning fails, stop_reason == "fatal_error"
  - Context vars (ChangeManager, CommandApprovalManager) are cleared
  - No partial patches remain staged
PASS if: All expected conditions true; no traceback visible to caller
```

#### CHAOS-02: Filesystem Write Failure Mid-Patch

```
Setup:    Scripted plan that writes 3 files; ChangeManager has all 3 staged.
Inject:   Monkeypatch Path.write_text to raise PermissionError on the 2nd file.
Expected:
  - ChangeManager.apply_all() detects the failure
  - Writes that already succeeded are rolled back (atomic guarantee)
  - Workspace is unchanged from pre-apply state
  - CheckpointError or PermissionError is surfaced to the executor
  - stop_reason == "fatal_error" or equivalent
PASS if: Workspace identical to pre-apply state after failure
```

#### CHAOS-03: Subprocess Timeout During Shell Execution

```
Setup:    Autonomous run with a scripted shell command step.
Inject:   Monkeypatch subprocess.run to raise subprocess.TimeoutExpired.
Expected:
  - ToolExecutionError recorded for the step
  - No zombie subprocess remains
  - Executor triggers replan if replan budget allows
  - CommandApprovalManager is cleared if run terminates
PASS if: No zombie processes; executor state consistent
```

#### CHAOS-04: Context Variable Leak Under Cancellation

```
Setup:    Executor running a multi-step scripted plan.
Inject:   Set cancel_event before context var cleanup runs (patch finally block).
Expected:
  - Even with the simulated timing issue, get_active_patch_manager() returns None
    after the run completes (context var cleanup must be in a finally block)
  - Subsequent run starts with clean context
PASS if: Next run's ChangeManager is None at start; no stale staged patches
```

#### CHAOS-05: Checkpoint Shadow Repo Corruption

```
Setup:    CheckpointManager with an existing checkpoint.
Inject:   Corrupt the shadow git repo's .git/objects/ directory (delete 2 object files).
Expected:
  - CheckpointError raised on restore attempt with a clear message
  - The main workspace is completely unaffected
  - Pearl continues to function (chat, direct tool calls still work)
  - A new checkpoint can be created (re-initializes the shadow repo)
PASS if: Main workspace unchanged; Pearl operational; clear error message
```

#### CHAOS-06: Memory Exhaustion During Index Build

```
Setup:    Very large corpus (~50,000 files); constrained memory environment.
Inject:   Limit process RSS to 512 MB via ulimit before running index_repository().
Expected:
  - Either completes successfully within the RSS limit
  - OR raises MemoryError (not OOM-killed silently)
  - No partial index left in corrupted state
PASS if: Clean completion OR clean exception; no silent corruption
```

### 22.3 Chaos Test Execution

Chaos tests run before each release and on demand. They are too slow and environment-
sensitive for every-commit CI.

```bash
pytest tests/chaos/ -v --timeout=120
```

### 22.4 Chaos PASS / FAIL Criteria

| Criterion | PASS | FAIL |
|---|---|---|
| All 6 scenarios | Expected recovery behavior | Any unexpected exception type, data loss, or zombie |
| Context var state | Clean after each scenario | Any stale ChangeManager reference |
| Workspace integrity | Identical to pre-chaos state (where expected) | Any unintended write |
| Clear error messages | Error message identifies cause and scope | Generic "Error" or silent failure |

---

## 23. Reliability and Endurance Testing

### 23.1 Purpose

Reliability testing verifies that Pearl produces correct results consistently — not
just on the first call, but across hundreds of consecutive operations in a single
session. Endurance testing specifically targets resource stability over time: memory,
file descriptors, and index consistency must not degrade with session length.

### 23.2 Reliability Scenarios

#### REL-01: 100-Consecutive-Task Session

```
Given:  A scripted executor; 100 distinct tasks queued
When:   All 100 tasks run sequentially in a single PearlAgent instance
Then:
  - All 100 tasks complete with stop_reason="completed"
  - stop_reason is never "fatal_error" for non-injected failures
  - Checkpoint IDs remain unique across all 100 tasks
  - RepositoryIndex returns consistent results for the same query throughout
  - No task result is contaminated by a prior task's state
```

#### REL-02: 8-Hour Idle Session

```
Given:  Pearl REPL open with no tasks submitted
When:   Process runs for 8 simulated hours (use a mock clock)
Then:
  - RSS does not grow (< 5 MB total growth)
  - No threads accumulate (thread count stable)
  - File descriptor count stable (no leaked handles)
  - REPL continues to accept and process prompts correctly
```

#### REL-03: 1,000-Tool-Call Session

```
Given:  Scripted executor; plan produces 50 tool calls per task; 20 tasks
When:   All 1,000 tool calls complete
Then:
  - No tool result is incorrectly attributed to a different call
  - Memory usage (RSS) growth < 200 MB total
  - Index spot-check at step 1,000 matches spot-check at step 1
```

#### REL-04: 100-Checkpoint Session

```
Given:  100 checkpoints created across a session
When:   All 100 are listed, queried, and individually restored
Then:
  - All 100 IDs are unique (no collision)
  - Each restore produces exactly the workspace state at checkpoint time
  - Listing all 100 completes in < 1 second
  - No checkpoint corrupts another
```

### 23.3 Resource Measurement

```python
import resource
import psutil
import os

def measure_rss_mb() -> float:
    return psutil.Process(os.getpid()).memory_info().rss / (1024 ** 2)

def measure_open_fds() -> int:
    return psutil.Process(os.getpid()).num_fds()  # Linux/macOS; use num_handles() on Windows
```

### 23.4 Reliability PASS / FAIL Criteria

| Criterion | PASS | FAIL |
|---|---|---|
| 100-task session | All 100 complete correctly | Any incorrect result or wrong stop_reason |
| RSS growth (100 tasks) | < 100 MB | > 100 MB (memory leak) |
| RSS growth (1,000 calls) | < 200 MB | > 200 MB |
| File descriptor count | Stable (< 10 growth) | Growing without bound |
| Index consistency | Same results at start and end | Any symbol missing or incorrect at end |
| Checkpoint uniqueness | All IDs unique | Any collision |

---

## 24. Snapshot Testing

### 24.1 Purpose

Snapshot testing captures the exact serialized output of a component at a known-good
point and fails automatically when the output changes. Unlike golden output tests
(which are hand-authored), snapshots are auto-generated on first run and should be
updated with an explicit command when an intentional change is made.

Snapshot testing is Pearl's early-warning system for unintentional output changes
in areas like planning prompts, MCP response shapes, and progress event sequences.

### 24.2 Framework

- **Tool:** `syrupy` (pytest plugin — drop-in `assert == snapshot` style)
- **Storage:** `tests/__snapshots__/` (committed to git)
- **Update command:** `pytest --snapshot-update`

```python
from syrupy.assertion import SnapshotAssertion

def test_tools_list_shape_snapshot(snapshot: SnapshotAssertion):
    """tools/list response shape must not change without a deliberate update."""
    server = MCPServer(build_registry())
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    # Snapshot the shape (tool names, parameter names) — not the descriptions,
    # which are prose and may change legitimately.
    shape = {
        tool["name"]: list(tool["inputSchema"]["properties"].keys())
        for tool in response["result"]["tools"]
    }
    assert shape == snapshot

def test_initialize_response_snapshot(snapshot: SnapshotAssertion):
    """initialize response capabilities must not change unexpectedly."""
    server = MCPServer(build_registry())
    response = server.handle({
        "jsonrpc": "2.0", "id": 0,
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {}}
    })
    assert response["result"]["capabilities"] == snapshot

def test_planning_prompt_snapshot(snapshot: SnapshotAssertion):
    """Planner prompt structure for a known workspace context must not drift."""
    planner = Planner(build_registry(), dispatcher, scripted_llm)
    prompt = planner._build_prompt("Create a file called hello.py", workspace_context="")
    assert prompt == snapshot
```

### 24.3 What to Snapshot

| Component | What to snapshot | Why |
|---|---|---|
| `MCPServer` | `initialize` response capabilities map | Capability regressions break the extension |
| `MCPServer` | `tools/list` tool names and parameter keys | Tool schema changes break planning |
| `Planner` | Assembled planning prompt structure | Prompt drift degrades plan quality |
| `AutonomousExecutor` | Progress event sequence for a standard scenario | UI depends on event order and types |
| `PersonalityManager` | Full emoji × event matrix | Mode distinctness regression |
| Error classes | Error message format | Error messages appear in LLM context |

### 24.4 Update Policy

**Rule SNAP-1:** Snapshot files MUST be committed to git. A snapshot that is generated
at test time but not committed is worthless — the next CI run will generate a new
baseline and the historical record is lost.

**Rule SNAP-2:** A PR that contains `--snapshot-update` output MUST explain in the PR
description exactly what changed and why. Reviewers MUST verify that the new snapshot
represents the intended behavior, not a regression being silently accepted.

**Rule SNAP-3:** Snapshot updates are not automatically approved. They receive the same
code review scrutiny as production code changes.

---

## 25. Golden Output Testing

### 25.1 Purpose

Golden output testing compares tool or component output against hand-authored reference
outputs. Unlike snapshots (auto-generated, auto-updated), golden files are written
by hand to represent the precise, correct output for a specific scenario. They are
never auto-updated.

Golden output testing is Pearl's safeguard for user-facing text that must be
exact: formatted event messages, truncation notices, CLI prompts, and error text
that appears in LLM planning context.

### 25.2 Framework

Golden files live in `tests/golden/` and are committed to git. The test framework
reads the golden file and compares it to actual output.

```python
# tests/helpers/golden.py
from pathlib import Path

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "golden"

def assert_golden(test_name: str, actual: str) -> None:
    golden_path = GOLDEN_DIR / f"{test_name}.txt"
    if not golden_path.exists():
        pytest.fail(
            f"Golden file missing: {golden_path}\n"
            f"Create it with the expected content:\n\n{actual}"
        )
    expected = golden_path.read_text(encoding="utf-8")
    assert actual == expected, (
        f"Golden output mismatch for {test_name}.\n"
        f"Expected ({len(expected)} chars):\n{expected}\n\n"
        f"Actual ({len(actual)} chars):\n{actual}\n\n"
        f"To update: edit {golden_path} deliberately."
    )
```

### 25.3 What to Golden-Test

| Golden file | Content | Stability |
|---|---|---|
| `format_event_none_executing.txt` | Output of `format_event(EXECUTING, EmojiMode.NONE)` | Stable |
| `format_event_fun_completed.txt` | Output of `format_event(COMPLETED, EmojiMode.FUN)` | Stable |
| `truncation_notice.txt` | Output of `_truncation_notice(matches, MAX_SEARCH_RESULTS)` | Stable |
| `workspace_boundary_error.txt` | Error message from `WorkspaceBoundaryError` | Stable |
| `cli_startup_banner.txt` | The banner printed by `main()` on startup | Stable |
| `approval_prompt.txt` | The text of the approval prompt shown to users | Stable |

### 25.4 Update Policy

**Rule GOLD-1:** Golden files are NEVER auto-updated. There is no `--golden-update`
flag. Changing a golden file requires: (a) editing the file by hand, (b) explaining
in the PR description what changed, why it changed, and confirming it is correct.

**Rule GOLD-2:** A failing golden test that is "fixed" by updating the golden file
without explaining the change is treated as a PR violation. The reviewer is expected
to check both the old and new golden file contents.

**Rule GOLD-3:** If the correct output for a golden scenario cannot be determined
with certainty, do not golden-test that scenario. Golden files represent ground truth;
a wrong golden file is worse than no golden test.

---

## 26. Compatibility Testing

### 26.1 Purpose

Compatibility testing verifies that Pearl functions correctly across the full range
of its supported environments: Python versions, operating systems, LLM providers,
VS Code versions, and dependency version ranges.

### 26.2 Compatibility Matrix

| Axis | Supported range | Tested in CI |
|---|---|---|
| Python | 3.10, 3.11, 3.12 | Yes — full matrix |
| OS | Ubuntu latest LTS, macOS latest, Windows Server 2022 | Yes — full matrix |
| VS Code | Latest stable, latest Insiders | Extension tests |
| LLM providers | scripted (always), ollama (nightly), openai/anthropic (weekly) | Partial |
| Node.js | ≥ 20 (for extension builds) | Yes |

### 26.3 CI Matrix Configuration

```yaml
# .github/workflows/compat.yml
strategy:
  fail-fast: false
  matrix:
    python-version: ["3.10", "3.11", "3.12"]
    os: [ubuntu-latest, macos-latest, windows-latest]
```

**Rule COMPAT-1:** `fail-fast: false` MUST be used in the compatibility matrix.
A failure on Windows must not prevent the macOS or Linux results from being reported.

**Rule COMPAT-2:** All unit and integration tests MUST pass across the full matrix.
A test that passes on Linux but fails on Windows is a bug, not a Windows exclusion.

### 26.4 Known Compatibility Gotchas

| Area | Risk | Test it explicitly |
|---|---|---|
| `pathlib.Path` separators | Windows uses `\`; tests that assert path strings break | Use `Path.parts` not string comparison |
| `subprocess.run()` | `executable` kwarg behaves differently on Windows | Test shell commands on all platforms |
| File locking | `fcntl` is POSIX-only; any file lock code needs a Windows equivalent | Test concurrent writes on Windows |
| Context variables | `ContextVar` behavior under `threading` is consistent but verify | Run concurrent executor tests on all platforms |
| Line endings | `\r\n` vs `\n` in file roundtrip tests | Use `newline=""` or explicit normalization |
| Symlink creation | Requires elevated privileges on Windows without Developer Mode | Mark symlink tests as needing privileges |

### 26.5 Provider Compatibility Testing

```bash
# Weekly with real API keys (not in standard CI)
PEARL_LLM_PROVIDER=openai pytest tests/integration/ -m requires_llm
PEARL_LLM_PROVIDER=anthropic pytest tests/integration/ -m requires_llm
PEARL_LLM_PROVIDER=ollama pytest tests/integration/ -m requires_llm
```

Tests marked `@pytest.mark.requires_llm` are skipped in CI unless a provider API key
is explicitly configured. They are run manually before releases.

### 26.6 VS Code Extension Compatibility

```bash
cd vscode-extension
# Test against stable VS Code API
npx @vscode/test-electron --version=stable

# Test against Insiders API
npx @vscode/test-electron --version=insiders
```

If an Insiders API behavior differs from stable and would break Pearl, file a GitHub
issue against VS Code immediately and add a version-guard in the extension.

### 26.7 Compatibility PASS / FAIL Criteria

| Criterion | PASS | FAIL |
|---|---|---|
| Full Python × OS matrix | All tests pass | Any test fails on any combination |
| VS Code stable | All extension tests pass | Any test fails |
| VS Code Insiders | All tests pass or failures have filed upstream issues | Unexamined failures |
| Path roundtrip (Windows) | Identical file content after write/read cycle | `\r\n` corruption or path errors |

---

## 27. Upgrade and Migration Testing

### 27.1 Purpose

Upgrade testing verifies that upgrading Pearl from a prior release to the current
release does not silently corrupt existing workspaces, lose checkpoint history,
break configurations, or change behavior that users depend on.

### 27.2 What Must Survive an Upgrade

| Artifact | Compatibility requirement |
|---|---|
| Checkpoint shadow repos (`~/.pearl/workspaces/`) | Must be readable and restorable by the new version |
| `.env` configuration files | All recognized keys must continue to work |
| MCP server protocol | Extension built against version N must work with server version N.x |
| Tool schemas | A `tools/call` request valid in version N must be valid in version N+1 |
| Workspace state | No files in the workspace should be touched by an upgrade |

### 27.3 Checkpoint Format Compatibility Test

```python
def test_checkpoint_v1_format_restores_under_current_version(tmp_workspace):
    """
    Regression: checkpoints created by prior versions must be restorable.
    If the checkpoint format ever changes, a migration step is required.
    """
    # Fixture: a pre-built shadow git repo in the v1 format
    v1_checkpoint_dir = Path("tests/fixtures/checkpoints/v1_format/")
    shutil.copytree(v1_checkpoint_dir, tmp_workspace / ".pearl_checkpoint_shadow")

    manager = CheckpointManager(workspace_root=tmp_workspace)
    checkpoints = manager.list()
    assert len(checkpoints) > 0, "V1 format checkpoints should be discoverable"

    # Verify restore works
    manager.restore(checkpoints[0].id)
    assert (tmp_workspace / "file.py").read_text() == "original content"
```

### 27.4 Settings Migration Test

```python
@pytest.mark.parametrize("old_key,new_key,value", [
    ("OLLAMA_MODEL", "DEFAULT_MODEL", "qwen2.5-coder:14b"),
    # Add pairs here as keys are renamed
])
def test_deprecated_settings_key_still_works(old_key, new_key, value, monkeypatch):
    """Renamed Settings keys must remain functional for at least one sprint."""
    monkeypatch.setenv(old_key, value)
    settings = Settings()
    # The new key should resolve to the same value
    assert getattr(settings, new_key) == value
```

### 27.5 MCP Protocol Version Negotiation Test

```python
def test_extension_built_against_prior_protocol_version_still_connects():
    """
    The MCP server must accept initialize requests claiming an older protocol version
    and negotiate down gracefully, not reject the connection.
    """
    server = MCPServer(build_registry())
    response = server.handle({
        "jsonrpc": "2.0", "id": 0,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",  # prior version
            "capabilities": {},
            "clientInfo": {"name": "vscode-pearl", "version": "1.0.0"},
        }
    })
    assert "error" not in response, "Old protocol version must not be rejected"
    assert "result" in response
    assert "serverInfo" in response["result"]
```

### 27.6 Migration Script Requirements

If any on-disk format changes between versions, a migration script MUST be provided:

```python
# scripts/migrate_checkpoints_v1_to_v2.py
"""
Idempotent migration: converts checkpoint shadow repos from v1 to v2 format.
Safe to run multiple times. Backs up v1 format before converting.
"""
```

**Rule MIG-1:** Migration scripts MUST be idempotent — running the same migration
twice on already-migrated data must produce no changes and no errors.

**Rule MIG-2:** Migration scripts MUST back up the original data before making any
changes. The backup path MUST be printed to stdout so users can verify or restore it.

**Rule MIG-3:** Migration scripts MUST be tested with both:
- Data that has not been migrated (full migration path)
- Data that has already been migrated (idempotency path)

### 27.7 Upgrade Testing Cadence

| When | What |
|---|---|
| Every sprint | Settings key rename tests (if any keys changed) |
| Every minor release | Checkpoint format compatibility test with prior release fixtures |
| Every major release | Full upgrade scenario: fresh install of v(N-1), create state, upgrade to vN, verify all state |
| On-demand | MCP protocol negotiation test when SERVER_VERSION bumps |

### 27.8 Upgrade PASS / FAIL Criteria

| Criterion | PASS | FAIL |
|---|---|---|
| Prior checkpoint format | Readable and restorable | Any restore error or data loss |
| Deprecated Settings keys | Still functional for one sprint | Keys silently ignored or crash |
| MCP version negotiation | Old protocol version accepted | Connection rejected |
| Migration scripts | Idempotent; data preserved | Second run changes data or errors |
| Workspace files | Untouched by upgrade | Any workspace file modified by upgrade process |

---

*This document is part of the Pearl Engineering Standards Series.*  
*Previous: [02_CODING_STANDARDS.md](02_CODING_STANDARDS.md)*  
*Next: [04_SECURITY_GUIDELINES.md](04_SECURITY_GUIDELINES.md)*
