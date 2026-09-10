# Pearl — Claude Work Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the gap between the VS Code MCP surface and the browser HTTP surface so a web user can use checkpoints, memory, and honest plan preview — without changing planning, tools, or the approval gate except one optional `run()` argument.

**Architecture:** HTTP stays a thin adapter over `PearlSession`. Shared serialization moves out of `src/mcp/server.py` so MCP and HTTP cannot drift. The browser stays native ES modules with no bundler. `AutonomousExecutor.run` may skip its opening plan only when the server looks up a previously stored `planId`; clients never send a step list.

**Tech Stack:** Python 3.10+, FastAPI, pytest, vanilla JS (`type="module"`), `node --test`.

**Spec:** `docs/superpowers/specs/2026-09-10-web-app-parity-design.md`

## Global Constraints

- Read `CLAUDE.md` before every change. Layers depend downward only.
- Run `pytest tests/ -q --ignore=tests/test_e2e_mcp.py` before touching code. Do not inherit a red baseline.
- No autonomous write reaches disk without `ChangeManager`. Do not add a path that bypasses it.
- Do not change planner, dispatcher, tool bodies, or approval policy except the `run(prompt, steps=None)` signature in section 5 of the spec.
- Use `list[ToolCall] | None` for supplied steps. Do **not** invent a new `PlanStep` type. `ToolCall` is in `src/llm/parser.py`.
- Response JSON uses the same camelCase keys as MCP (`shortId`, `createdAt`, `changedAnything`, `planId`).
- No JavaScript bundler, no React, no new frontend framework this sprint.
- `ROADMAP.md` is stale (M2–M5 items already exist in code). Do not implement from the roadmap. This spec is the work.
- Unsolicited refactors belong in a separate PR. The frontend split is in-scope because the spec requires it, and it must be its own commit.
- Personality is last and is the cut if time runs out.

**Paste this to Claude to start:**

```
Implement docs/superpowers/plans/2026-09-10-claude-work-plan.md
using the spec docs/superpowers/specs/2026-09-10-web-app-parity-design.md.
Follow CLAUDE.md. One task at a time. TDD. Commit after each task.
Do not start later sprints.
```

---

## File map

| File | Responsibility |
|---|---|
| `src/tools/checkpoint_serialize.py` (new) | `_checkpoint_to_dict` and `_restore_report_to_dict` — one definition for MCP + HTTP |
| `src/personality/timeline.py` (new) | `TIMELINE_EVENT_KINDS` map moved off `MCPServer` |
| `src/mcp/server.py` | Import shared helpers; no duplicate dict builders |
| `src/api/server.py` | New endpoints; static mount for `pearl_ui/js/` |
| `src/api/session.py` | At most one pending plan per session (`planId`, steps, created_at) |
| `src/agent/executor.py` | `run(self, prompt: str, steps: list[ToolCall] \| None = None)` |
| `pearl_ui/index.html` | Shell + CSS only after the split |
| `pearl_ui/js/*.js` | Browser logic, one concern per file |
| `pearl_ui/js/*.test.js` | `node --test` for pure functions |
| `tests/test_api_parity.py` (new) | HTTP endpoint tests |
| `tests/test_executor_supplied_steps.py` (new) | Scripted-LLM: supplied steps execute; validator still runs |
| `.github/workflows/python.yml` | Add `node --test` for `pearl_ui/js` |

---

### Task 1: Shared serialization and timeline map

**Files:**
- Create: `src/tools/checkpoint_serialize.py`
- Create: `src/personality/timeline.py`
- Modify: `src/mcp/server.py` (replace local `_checkpoint_to_dict`, `_restore_report_to_dict`, `_TIMELINE_EVENT_KINDS`)
- Test: `tests/test_checkpoint_serialize.py`, `tests/test_personality_timeline.py`

**Interfaces:**
- Consumes: `Checkpoint`, `RestoreReport` from `src/tools/checkpoints.py`; `EventKind` from `src/personality`
- Produces:
  - `checkpoint_to_dict(checkpoint: Checkpoint) -> dict[str, Any]` with keys `id`, `shortId`, `label`, `createdAt`
  - `restore_report_to_dict(report: RestoreReport) -> dict[str, Any]` with keys `checkpointId`, `restored`, `removed`, `changedAnything`
  - `TIMELINE_EVENT_KINDS: dict[str, EventKind]` with keys `planning`, `plan_ready`, `waiting_approval`, `running_tool`, `completed`

- [ ] **Step 1: Write failing tests** for exact MCP key names and the five timeline keys.

```python
def test_checkpoint_to_dict_keys():
    # construct a Checkpoint the same way CheckpointManager.create does
    d = checkpoint_to_dict(cp)
    assert set(d) == {"id", "shortId", "label", "createdAt"}

def test_restore_report_to_dict_keys():
    d = restore_report_to_dict(report)
    assert set(d) == {"checkpointId", "restored", "removed", "changedAnything"}

def test_timeline_event_kinds_match_extension_stages():
    from src.personality.timeline import TIMELINE_EVENT_KINDS
    assert set(TIMELINE_EVENT_KINDS) == {
        "planning", "plan_ready", "waiting_approval", "running_tool", "completed",
    }
```

- [ ] **Step 2:** `pytest tests/test_checkpoint_serialize.py tests/test_personality_timeline.py -v` — FAIL (modules missing).
- [ ] **Step 3:** Move the functions verbatim from `src/mcp/server.py`. MCP imports them and aliases if needed so existing MCP tests keep passing without response-shape changes.
- [ ] **Step 4:** `pytest tests/test_checkpoint_serialize.py tests/test_personality_timeline.py tests/test_mcp_protocol.py -q` — PASS.
- [ ] **Step 5:** Commit `refactor: share checkpoint and timeline serialization between adapters`

---

### Task 2: Split `pearl_ui` into ES modules (behavior-neutral)

**Files:**
- Create: `pearl_ui/js/` modules extracted from the script in `pearl_ui/index.html` (chat/run/approve, status, workspace, complete). Do not add checkpoint/memory/plan UI yet.
- Modify: `pearl_ui/index.html` — keep CSS + markup; load with `<script type="module" src="/js/main.js">`
- Modify: `src/api/server.py` — mount static files for `pearl_ui/` so `/js/*.js` is served. Keep `GET /` serving `index.html`.
- Create: `pearl_ui/js/format.test.js` — at least one pure helper used by the existing UI (or a tiny extracted formatter).
- Modify: `.github/workflows/python.yml` — after Python tests, `node --test pearl_ui/js/*.test.js`

**Exit criterion (spec §6):** the split is not done until `node --test` covers at least one module's pure functions in CI.

- [ ] **Step 1:** Write `pearl_ui/js/format.test.js` that imports a pure function you will extract (e.g. patch summary or SSE event parse). Confirm `node --test pearl_ui/js/format.test.js` fails.
- [ ] **Step 2:** Extract JS without changing fetch URLs, event names, or DOM ids. No new features.
- [ ] **Step 3:** Add FastAPI `StaticFiles` for `pearl_ui` (or `pearl_ui/js`) without breaking `GET /`. Add a test that `GET /js/main.js` returns 200.
- [ ] **Step 4:** Run existing `tests/test_multiuser_api.py` plus the new static-file test. Run `node --test pearl_ui/js/*.test.js`.
- [ ] **Step 5:** Commit **only** this move: `refactor: split pearl_ui into ES modules`

---

### Task 3: Checkpoints, read-only HTTP + list panel

**Files:**
- Modify: `src/api/server.py` — `GET /api/checkpoints?limit=50`, `GET /api/checkpoints/{id}/restore-preview`
- Create: `pearl_ui/js/checkpoints.js` — list only
- Test: `tests/test_api_parity.py`

**Rules:**
- Call `session.checkpoints` the same way MCP does. No new `PearlSession` wrappers.
- `CheckpointError` → HTTP 400 with the exception message. Bad id → 400, not 404.
- Restore preview must not change disk. Test byte identity of the workspace after the call.
- No section-9 403 on these two routes.

- [ ] **Step 1:** Tests first: list empty, list after create (create via `CheckpointManager` in the test, not via HTTP yet), preview does not mutate, `CheckpointError` → 400.
- [ ] **Step 2:** Implement endpoints using `checkpoint_to_dict` / `restore_report_to_dict`.
- [ ] **Step 3:** UI: panel listing checkpoints (id/label/time). No restore/delete/create buttons yet.
- [ ] **Step 4:** `pytest tests/test_api_parity.py -v` PASS. Manual: `python -m src.api`, confirm list loads.
- [ ] **Step 5:** Commit `feat: expose read-only checkpoint list and restore preview over HTTP`

---

### Task 4: Checkpoints, mutating + preview-then-confirm

**Files:**
- Modify: `src/api/server.py` — `POST /api/checkpoints`, `PATCH /api/checkpoints/{id}`, `DELETE /api/checkpoints/{id}`, `POST /api/checkpoints/{id}/restore`
- Modify: `pearl_ui/js/checkpoints.js` — create/rename/delete; restore only after preview confirmation
- Test: `tests/test_api_parity.py`

**Rules (spec §8–9):**
- Mutating routes refuse **403** when `auth_required() or public_mode()`, same wording family as `POST /api/workspace` (shared instance / host filesystem). Reuse that condition exactly.
- Listing and restore-preview stay 200 under auth.
- Browser never POSTs restore from a list row. Preview first. If `changedAnything` is false, show that and offer no confirm.
- With auth configured, a checkpoint created in one user's session is visible to the other (shared store). Assert this so the constraint stays documented.

- [ ] **Step 1:** Tests for 403 on create/rename/delete/restore when auth or public mode; 200 on list and preview; shared visibility; restore actually restores after confirm path.
- [ ] **Step 2:** Implement endpoints.
- [ ] **Step 3:** UI confirmation flow.
- [ ] **Step 4:** `pytest tests/test_api_parity.py tests/test_multiuser_api.py -q` PASS.
- [ ] **Step 5:** Commit `feat: add gated checkpoint create, rename, delete, and restore`

---

### Task 5: Memory endpoint and viewer

**Files:**
- Modify: `src/api/server.py` — `GET /api/memory` returns `session.memory.to_dict()` unchanged (`conversation`, `tasks`, `project`, `execution_history`)
- Create: `pearl_ui/js/memory.js` — read-only viewer
- Test: `tests/test_api_parity.py`

- [ ] **Step 1:** Test keys and that GET mutates nothing.
- [ ] **Step 2:** Endpoint + UI.
- [ ] **Step 3:** Commit `feat: expose workspace memory to the web UI`

---

### Task 6: Honest plan preview (`steps` on `run`, `planId`, no client-supplied plans)

**Files:**
- Modify: `src/agent/executor.py` — `def run(self, prompt: str, steps: list[ToolCall] | None = None) -> ExecutionReport`
- Modify: `src/api/session.py` — store one pending plan: `{plan_id, steps, created_at}`; TTL 10 minutes; consume on first successful use; new plan replaces previous
- Modify: `src/api/server.py` — `POST /api/plan` body `{"prompt": str}` returns `{"planId", "steps"}`; `POST /api/run` optional `planId`
- Create: `pearl_ui/js/plan.js`
- Test: `tests/test_executor_supplied_steps.py`, `tests/test_api_parity.py`
- Check: `docs/engineering/07_AI_ENGINEERING_GUIDELINES.md` section 6 — update only if the planning-loop contract changed (supplied steps still go through `validate_plan`)

**Rules (spec §5):**
- `steps is None` → today's behavior. All existing executor tests must stay green.
- When `steps` is supplied, skip the opening `_plan_with_retry`. Still run `validate_plan` before execute.
- HTTP never accepts a step list. Only `planId`. Unknown / expired / consumed / replaced → **400**, never fall back to planning.
- Plan lives on `PearlSession` only, not disk, not a process-global map.
- `planId` from user A is 400 for user B.
- UI copy: **"planned steps"**. Replans from the progress stream must remain visible. Do not imply the preview is the whole run.
- Scripted LLM integration test: supplied steps execute; validator still rejects an illegal plan.

- [ ] **Step 1:** Executor tests with `ScriptedLLMClient`: `steps=None` still plans; supplied `[ToolCall(...)]` does not call plan; invalid supplied plan fails validation.
- [ ] **Step 2:** Implement `run(..., steps=None)` with default preserving current path.
- [ ] **Step 3:** Session store + `POST /api/plan` + `planId` on `POST /api/run` + tests for expiry (freeze time), consume-once, cross-user 400.
- [ ] **Step 4:** UI plan preview then run with `planId`.
- [ ] **Step 5:** Full `pytest tests/ -q --ignore=tests/test_e2e_mcp.py` plus `tests/test_e2e_mcp.py` if MCP tests still apply. Approval invariant test must pass.
- [ ] **Step 6:** Commit `feat: plan preview executes the stored plan, not a second plan`

---

### Task 7: Personality labels (cuttable)

**Files:**
- Modify: `src/api/server.py` — `GET /api/personality` returns `{"labels": {stage: wording}}` using `TIMELINE_EVENT_KINDS` and `PersonalityManager`
- Modify: `pearl_ui/js/plan.js` — stage names from this endpoint
- Test: `tests/test_api_parity.py`

If the sprint is short, **skip this task**. Preview can ship with plain English stage names.

- [ ] **Step 1:** Test labels keys match `TIMELINE_EVENT_KINDS`.
- [ ] **Step 2:** Endpoint + wire into preview.
- [ ] **Step 3:** Commit `feat: expose personality stage labels to the web UI`

---

## Stop here

Do not start token-usage tracking, ESLint, non-Python AST editors, multi-agent, or roadmap M6 in this pass.

## Later (not this plan)

Only after web/MCP parity is merged and green:

1. **Token usage** — surface LLM `usage` on CLI, HTTP, and MCP (README near-term).
2. **Extension lint** — ESLint on `vscode-extension/` (CI today is `tsc` only).
3. **Stronger default model** — routing/docs, not a new agent loop. The loop already exists; 1.5B is the quality ceiling.
4. **`ROADMAP.md` honesty pass** — mark shipped M2–M5 work so humans and agents stop rebuilding it.

## Verification before claiming done

```bash
pytest tests/ -q --ignore=tests/test_e2e_mcp.py
pytest -q tests/test_e2e_mcp.py
ruff check src/ tests/
node --test pearl_ui/js/*.test.js
```

Open `http://localhost:7474` and exercise: list checkpoints, preview restore, create/restore locally (no auth), memory viewer, plan preview then run, approve a staged write. Confirm a second plan replaces the first `planId` and the old id returns 400.
