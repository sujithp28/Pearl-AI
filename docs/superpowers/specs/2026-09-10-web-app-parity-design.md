# Web App Parity With the VS Code Extension

**Status:** Revised after review. Ready to plan.
**Date:** 2026-09-10
**Scope:** `src/api/`, `pearl_ui/`, one signature change in `src/agent/executor.py`

Review raised five items. Four are incorporated: the plan id lifecycle
is fully specified in section 5, checkpoint behaviour on a shared
instance gets its own section 9, the no-bundler decision is scoped to
this sprint in section 6, and the checkpoint work is split into a
read-only and a mutating step in section 13. The fifth, scheduling a
personality bugfix, is withdrawn: the bug does not exist, and section 7
records why the earlier claim was wrong.

---

## 1. Why

Pearl has two protocol adapters over one agent. The MCP server in
`src/mcp/server.py` exposes fourteen `pearl/*` methods to the VS Code
extension. The HTTP server in `src/api/server.py` exposes eleven
endpoints to the browser. They were built at different times and the
HTTP surface never caught up.

Nine of the fourteen protocol methods have no HTTP equivalent:

| Protocol method | Reachable from browser today |
|---|---|
| `pearl/checkpoints` | No |
| `pearl/checkpointCreate` | No |
| `pearl/checkpointRename` | No |
| `pearl/checkpointDelete` | No |
| `pearl/checkpointRestorePreview` | No |
| `pearl/checkpointRestore` | No |
| `pearl/memory` | No |
| `pearl/personality` | No |
| `pearl/planOnly` | No |

The checkpoint system is the whole of the unreleased Sprint 1 in
`CHANGELOG.md`, including the storage relocation recorded in ADR-005. A
browser user of Pearl cannot create a restore point, cannot see the ones
Pearl takes automatically before every approved write, and cannot roll
one back.

Nothing here adds agent capability. Every feature already works. This
closes the gap between one adapter and the other.

## 2. Scope

**In scope**

* Nine HTTP endpoints, each a thin adapter over objects `PearlSession`
  already holds.
* Browser UI for checkpoints, memory, and plan preview.
* A personality labels endpoint, so the plan preview can name its
  stages in the configured voice.
* Moving `pearl_ui` browser logic out of one file into ES modules.
* Tests for every endpoint and every new module.

**Out of scope**

* Any change to planning, tool execution, or the approval gate beyond
  the single executor signature change in section 5.
* A JavaScript build step, bundler, or framework. See section 6.
* Timeline as an API concept. There is no `pearl/timeline` method. The
  extension builds its timeline client side from progress events, and
  `pearl_ui` already renders those as step cards.
* An ownership model for checkpoints. Section 9 explains why one cannot
  exist while all users share a workspace, and what is done instead.

## 3. Architecture

The HTTP layer stays an adapter and gains no business logic, matching
the rule that layer 5 holds protocol concerns only.

`PearlSession.__init__` already builds both managers the new endpoints
need:

```python
self.memory = Memory()
self.checkpoints = CheckpointManager()
```

So endpoints reach them the same way `MCPServer` does, directly, with no
new `PearlSession` methods to wrap a single call. The one exception is
personality, covered in section 7.

Every endpoint follows the shape the existing ones use:

```python
@app.get("/api/checkpoints")
async def checkpoints_list(request: Request) -> JSONResponse:
    session = get_session(request)
    ...
```

Response field names match the protocol exactly, reusing the same camel
case keys the extension already consumes, so both clients read one
vocabulary:

```python
{"id": ..., "shortId": ..., "label": ..., "createdAt": ...}
```

The two serialization helpers in `src/mcp/server.py`,
`_checkpoint_to_dict` and `_restore_report_to_dict`, are currently
module private to the MCP adapter. They move to a shared location so
both adapters import one definition rather than drifting apart, which is
how the gap this document closes came to exist in the first place.

## 4. HTTP surface

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/checkpoints` | `limit` query, default 50 | `{"checkpoints": [...]}` |
| POST | `/api/checkpoints` | `{"label"?: str}` | `{"checkpoint": {...}}` |
| PATCH | `/api/checkpoints/{id}` | `{"label": str}` | `{"checkpoint": {...}}` |
| DELETE | `/api/checkpoints/{id}` | none | `{"deleted": true}` |
| GET | `/api/checkpoints/{id}/restore-preview` | none | restore report |
| POST | `/api/checkpoints/{id}/restore` | none | restore report |
| GET | `/api/memory` | none | `Memory.to_dict()` |
| GET | `/api/personality` | none | `{"labels": {...}}` |
| POST | `/api/plan` | `{"prompt": str}` | `{"planId": str, "steps": [...]}` |

One existing endpoint also changes. `POST /api/run` gains an optional
`planId` field on its request body. Omitted, it behaves exactly as it
does today.

A restore report is `{"checkpointId", "restored", "removed",
"changedAnything"}`, where `restored` and `removed` are file lists.

`GET /api/memory` returns the four keys `Memory.to_dict()` produces:
`conversation`, `tasks`, `project`, `execution_history`. Read only. It
reuses the dictionary as is and mutates nothing.

Method choice is deliberate. Create, rename, and delete map onto POST,
PATCH, and DELETE rather than the protocol's verb-in-the-name style,
because this is HTTP and the browser and any future client get correct
cache and retry semantics for free.

## 5. Plan preview, and the honesty problem

This is the only part of the design that changes existing behaviour, and
it needs care.

`AutonomousExecutor.run(prompt)` plans internally. So the naive
implementation of plan preview calls `Planner.plan(prompt)` to show the
user some steps, then calls `run(prompt)`, which plans a second time.
The user approves one plan and Pearl executes another. They will usually
match. Usually is not a guarantee, and a preview that can silently
diverge from what runs is worse than no preview, because it manufactures
confidence rather than informing it.

It also costs a second planning round trip on every run.

**Decision.** `AutonomousExecutor.run` gains an optional parameter:

```python
def run(self, prompt: str, steps: list[PlanStep] | None = None) -> ExecutionReport:
```

`None` preserves today's behaviour exactly, so every existing caller and
every existing test is unaffected. When steps are supplied, the executor
skips its opening planning call and begins with them.

**The steps never come from the client.** `POST /api/plan` stores the
plan on the session against a generated `planId` and returns that id
alongside the steps for display. `POST /api/run` optionally carries the
`planId`, and the server looks the steps up. Accepting a step list over
HTTP would let a caller hand the executor an arbitrary tool sequence,
which is a path from untrusted input straight to execution. A plan is
only ever produced by the planner, server side, and only ever validated
by the plan validator. That validation runs on the stored plan before
execution regardless of which route reached it.

**Plan lifetime.** Fully specified, because the stored plan is the only
new state this design introduces and therefore the only new surface for
confusion or abuse.

* **Where it lives.** On the `PearlSession` instance, in memory, never
  on disk and never in a process-global map. Sessions are already one
  per user, so a plan is unreachable from another user's session by
  construction rather than by a check that could be forgotten. It does
  not survive a restart.
* **How many.** At most one pending plan per session. Producing a new
  one replaces it, because a user who re-plans has abandoned the
  previous preview.
* **How long.** Ten minutes, and consumed on first successful use. A
  plan older than that is discarded on lookup. The bound exists because
  a plan reflects the workspace as it was when the planner read it, and
  a preview the user walked away from an hour ago should not be one
  click from executing.
* **What failure looks like.** A `planId` that is unknown, expired,
  already consumed, or replaced returns 400. It never falls back to
  planning again, because a silent fallback would run something the
  user never saw. The browser's recovery is to re-plan, which is one
  request and shows the user the new steps.
* **What it is not.** The id is a lookup key in one session's memory,
  not a capability token. It grants nothing on its own and is useless
  in another session, so it needs no signing or entropy guarantees
  beyond being unguessable within a session.

A plan can also go stale: the workspace may change between preview and
run, whether by the user editing a file or by another tool. The design
does not attempt to detect this. The plan validator runs against current
state immediately before execution, which is the check that matters, and
a plan invalidated by a change fails there rather than executing on
outdated assumptions.

**What the preview does and does not promise.** The executor replans on
failure by design, bounded by `max_replans`. So an approved plan is the
opening plan, not a contract for the whole run. The UI must say
"planned steps" and surface replans as they arrive on the progress
stream. It must not imply the approved list is what will execute start
to finish.

This touches the planning loop, so per the contributor guide it needs a
scripted-LLM integration test proving that supplied steps are executed
and that the validator still runs, and section 6 of
`07_AI_ENGINEERING_GUIDELINES.md` should be checked for a required
update before merge.

## 6. Front end structure

`pearl_ui/index.html` is 1,946 lines: styles to line 701, markup to 895,
then roughly a thousand lines of browser JavaScript. Parity adds a
checkpoint panel, a memory viewer, and a plan preview, which would push
one file toward 2,800 lines.

**Decision.** Native ES modules, no build step.

`index.html` keeps the shell and the styles. Existing logic moves to
`pearl_ui/js/` split by concern, loaded with `<script type="module">`.
Each new feature gets its own module: `checkpoints.js`, `memory.js`,
`plan.js`.

The server gains a static mount beside the existing `serve_ui` handler,
which today reads `index.html` and returns it with no way to serve
anything alongside it.

A bundler is rejected **for this sprint**, and that scope is deliberate.
Browsers have shipped modules since 2018, the repository's CI runs
pytest and `tsc`, adding a third toolchain buys nothing a user can see,
and a thousand lines of working code would be rewritten for zero
user-visible gain. None of that is an argument that Pearl's front end
should never have a build. It is an argument that this body of work does
not justify introducing one. A dependency graph worth tree-shaking, a
compile-to-JS language, or reactive state for the checkpoint list are
each a reason to revisit, and revisiting costs nothing this design
forecloses.

**Exit criterion for this step.** The split is not done when the files
move. It is done when at least one module's pure functions are covered
by a `node --test` suite that runs in CI, the same runner the extension
already uses. Moving a thousand lines out of one file and leaving them
as untestable as they were is the failure mode this step exists to
prevent, and without that criterion the step can stall half-finished
and still look complete.

## 7. Personality

An earlier draft of this document claimed the web session runs the wrong
personality because it constructs its executor without a
`PersonalityManager`. That was wrong, and the correction is recorded
here rather than deleted because the reasoning is the useful part.

`AutonomousExecutor.__init__` resolves `personality or
PersonalityManager()`, and the no-argument constructor reads
`Settings.PERSONALITY`. The fallback is therefore the configured
personality, not a hardcoded default. The MCP path does not pass one
into its executor either. Both surfaces resolve identically, and there
is no divergence to fix.

Personality is consequently a pure addition: an endpoint, not a bugfix.

`GET /api/personality` returns stage wording, mirroring
`pearl/personality`. The stage to event kind map currently lives as
`MCPServer._TIMELINE_EVENT_KINDS`, private to one adapter. Two adapters
now need it, so it moves to `src/personality/` and both import it. A
copy in the HTTP layer would be a second source of truth for wording
that must match across clients.

The labels name the stages of the plan preview from section 5. Without
that preview they would be dead configuration, which is why the two
features ship together or not at all.

## 8. Restore is two-step

Restoring a checkpoint can delete files created since the snapshot.
`preview_restore` exists separately from `restore` for exactly this
reason, and the protocol docstring is explicit that a client should show
the report before the user confirms, the same way Pearl shows a diff
before writing.

The browser therefore never calls restore directly from a list row. It
calls the preview endpoint, renders what would be restored and what
would be removed, and calls restore only on explicit confirmation. When
`changedAnything` is false, it says so and offers nothing to confirm.

This is the approval invariant applied to a destructive path that
happens not to be a write.

## 9. Checkpoints on a shared instance

Per-user sessions separate conversations, workspaces as a concept,
memory, and approval queues. They do not separate the checkpoint store,
and the reason is structural rather than an oversight.

`CheckpointManager` keys its store by the absolute path of the
workspace. `POST /api/workspace` already refuses with 403 whenever auth
is configured or public mode is set, because letting a caller choose a
path would expose the host filesystem. So on any shared instance every
user is pinned to the one workspace the operator started Pearl against,
and therefore to one checkpoint store.

The consequence is worth stating plainly. There are no per-user
snapshots. Every checkpoint is a snapshot of the single shared
workspace, every user sees every checkpoint, and one user restoring
rolls back the files everyone else is working in. An ownership model
would be theatre: tagging a snapshot with its creator does not make the
restore it performs any less global.

This is the same class of problem as `execute_shell`, which the
deployment document already identifies as the constraint that decides
everything. Pearl executes as the server process and ships no sandbox.

**Decision.** The mutating checkpoint endpoints, create, rename, delete,
and restore, refuse with 403 under exactly the condition
`/api/workspace` already uses, `auth_required() or public_mode()`, and
reuse that wording. Listing and restore preview stay available, because
both are read-only and seeing what exists is how a user understands why
the others are refused.

This is deliberately the conservative choice. It costs a shared
deployment a feature it cannot safely have, and it costs a local
single-user run, which is what almost every Pearl user has, nothing at
all. If per-user sandboxes ever arrive, this gate is one condition to
revisit and the endpoints underneath it do not change.

## 10. Error handling

`CheckpointError` maps to HTTP 400 with the exception message, matching
how the protocol adapter maps it to `INVALID_PARAMS`. A missing or
malformed checkpoint id is 400, not 404, because the id is a parameter
the caller supplied rather than a resource path Pearl publishes.

An uninitialised session already raises 503 inside `get_session`, and a
failed authorization already raises 401. New endpoints inherit both by
calling it.

No new endpoint catches a bare exception. A checkpoint failure surfaces.
The one place failure is deliberately swallowed today, checkpoint
creation before a write, stays as it is, because a broken checkpoint
must never block a write.

## 11. Testing

Python, using the `TestClient` fixture pattern already in
`tests/test_multiuser_api.py`, which points the workspace at `tmp_path`
and clears the session registry:

* One test per endpoint for the success path.
* `CheckpointError` returns 400 and does not leak a traceback.
* Restore preview changes nothing on disk. Assert the workspace is byte
  identical after calling it.
* With auth configured, create, rename, delete, and restore each return
  403, and listing and restore preview still return 200. This is the
  section 9 gate, and it is the test that would fail if someone later
  removes the gate believing per-user sessions already isolate the
  store.
* A test asserting the shared store explicitly: with auth configured,
  a checkpoint visible to one user is visible to the other. It
  documents the constraint rather than pretending it away, and it will
  fail loudly if per-user workspaces ever arrive, which is exactly when
  the gate should be revisited.
* A plan id from one user's session is not usable from another's.
* An expired plan id returns 400 and does not fall back to planning.
* A scripted-LLM test that `run` with supplied steps executes those
  steps, and that the plan validator still runs on them.
* `run` with `steps=None` behaves exactly as before.

JavaScript, using `node --test`:

* Each new module's pure functions, in particular restore report
  formatting and the empty-state case.

The approval invariant test in the contributor guide is untouched and
must stay green.

## 12. Risks

* **The executor signature change is the one real risk.** It touches
  layer 4 from a layer 5 feature. Mitigated by the default of `None` and
  by tests asserting unchanged behaviour on that path.
* **The front end move is a large diff with no behaviour change**, which
  makes review hard and regressions easy to miss. It should land as its
  own commit, separate from every feature commit, so a bisect can tell a
  move apart from a feature.
* **Scope creep toward a front end framework.** The moment someone wants
  reactive state for the checkpoint list, the no-build decision will be
  questioned. It should be questioned then, on evidence, not pre-empted
  now.
* **The section 9 gate will look like an overreaction to someone who
  only runs Pearl locally**, because locally it never fires. The test
  asserting the shared store exists partly so the reason survives
  contact with a future contributor who does not remember this
  document.
* **A claim in an earlier draft of this document was wrong**, asserting
  a personality bug that does not exist. It was caught in review. The
  correction is kept in section 7 rather than deleted, because the same
  reasoning error, inferring behaviour from a call site without reading
  the constructor, is easy to repeat.

## 13. Sequence

1. Shared serialization helpers and the stage map move out of
   `src/mcp/server.py`. No behaviour change.
2. Front end splits into modules. No behaviour change.
3. **Checkpoints, read-only.** `GET /api/checkpoints` and
   `GET /api/checkpoints/{id}/restore-preview`, plus the panel that
   lists them. Nothing here mutates anything, so it carries no shared
   instance gate and no confirmation flow. It is also the step that
   makes the automatic pre-write checkpoints visible for the first
   time, which is most of the user-facing value on its own.
4. **Checkpoints, mutating.** Create, rename, delete, and restore,
   behind the section 9 gate, with the preview-then-confirm flow on
   restore.
5. Memory endpoint and viewer.
6. Executor signature change, plan endpoint, and plan preview UI.
7. Personality endpoint, wired into the preview's stage labels.

Steps 1 and 2 are pure refactors and land first so every feature after
them is a small, readable diff.

Checkpoints are split across steps 3 and 4 rather than shipped as one
piece. Six endpoints, a panel, a destructive confirmation flow, and a
403 gate in a single change is the step most likely to slip, and the
read-only half is independently useful the day it lands. The split also
puts every risky operation in one reviewable commit instead of diluting
it among four safe ones.

Personality is its own step rather than riding along with the preview.
It is a pure addition with no dependency beyond the preview existing, so
it is the natural thing to cut if the sprint runs short.
