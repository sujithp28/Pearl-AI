# Pearl's Checkpoint System

Undo for anything Pearl writes. Pearl's approval gate stops unwanted
changes reaching disk in the first place, but once a change is
approved, checkpoints are what let you get back the state from
before it happened — cheaply, without touching your own git history.

## Concepts

A **checkpoint** is a snapshot of your entire workspace at a point in
time, with a label and a timestamp. Checkpoints are created two ways:

- **Automatically**, right before Pearl writes any approved patch
  batch to disk (see [`AutonomousExecutor.approve()`](../src/agent/executor.py)),
  labelled `Before: <your prompt, truncated>`.
- **Manually**, whenever you want one — from the CLI, from the VS
  Code "Checkpoints" panel, or over MCP.

Both kinds live in the same store and show up in the same list.

## How it works

A checkpoint is a commit in a *shadow* git repository — completely
separate from your project's own `.git`. Pearl never runs a mutating
command against your real repository: your history, branches, and
staging area are exactly as you left them, always.

Storage lives **outside your workspace**, at
`~/.pearl/workspaces/<key>/`, keyed by a hash of your workspace's
absolute path — not inside your project directory. Nothing is ever
written into your project or your `.gitignore`. If an earlier version
of Pearl left a legacy in-workspace `.pearl/` store behind, it's
migrated to the new location automatically, once, the first time you
use checkpoints in that workspace.

Restoring a checkpoint changes files in your working tree; it does
not rewrite any commit history, yours or Pearl's.

## User guide

### CLI

```
Pearl > :checkpoints                      # list, newest first
Pearl > :checkpoint before big refactor    # create one, with a label
Pearl > :checkpoint                        # create one with a default label
Pearl > :restore <id>                      # preview + confirm, then restore
Pearl > :rename <id> new label here
Pearl > :delete <id>
```

`<id>` accepts the full checkpoint id or, for convenience, the short
id shown in `:checkpoints`' output (git resolves an unambiguous
prefix — pass more characters if you get an "ambiguous" error, which
is exceptionally unlikely for any workspace with a normal number of
checkpoints).

Restoring always shows you what will change first — which files will
be reverted, and which files (created since the checkpoint) will be
removed — before asking `Proceed? [y/N]`.

### VS Code

Open the **Checkpoints** panel in Pearl's activity bar view. From
there:

- **＋** (toolbar) — create a checkpoint, optionally naming it.
- **⟳** (toolbar) — refresh the list.
- Per-checkpoint inline actions — **restore** (shows a modal
  confirmation listing exactly what will change), **rename**, and
  **delete** (also confirmed).

The panel refreshes automatically whenever Pearl connects, and after
every action you take in it.

### MCP

Six methods, all under `pearl/checkpoint*`:

| Method | Params | Returns |
|---|---|---|
| `pearl/checkpointCreate` | `{label?: string}` | `{checkpoint: Checkpoint \| null}` — `null` means nothing had changed |
| `pearl/checkpoints` | `{limit?: number}` | `{checkpoints: Checkpoint[]}`, newest first |
| `pearl/checkpointRestorePreview` | `{id: string}` | `RestoreReport` — nothing is changed |
| `pearl/checkpointRestore` | `{id: string}` | `RestoreReport` |
| `pearl/checkpointDelete` | `{id: string}` | `{deleted: true}` |
| `pearl/checkpointRename` | `{id: string, label: string}` | `{checkpoint: Checkpoint}` |

```ts
interface Checkpoint {
  id: string;
  shortId: string;
  label: string;
  createdAt: string; // ISO 8601
}

interface RestoreReport {
  checkpointId: string;
  restored: string[];       // files reverted to the checkpoint's content
  removed: string[];        // files created since, and removed
  changedAnything: boolean;
}
```

All error cases (unknown id, deleted checkpoint, missing required
param) come back as a standard JSON-RPC error, not a malformed
success response.

## Configuration

Two environment variables, both optional, both auto-cleanup
(retention) knobs — see [Auto cleanup](#auto-cleanup) below:

```bash
PEARL_CHECKPOINT_MAX_COUNT=50      # keep at most this many; 0 disables
PEARL_CHECKPOINT_MAX_AGE_DAYS=30   # hide anything older than this; 0 disables
```

## Auto cleanup

After every `create()`, checkpoints beyond `max_count` (oldest first)
or older than `max_age_days` are hidden from listing and can no
longer be restored — the same mechanism as an explicit delete (see
[Why deletion never rewrites history](#why-deletion-never-rewrites-history)).
The checkpoint that was just created is never pruned by its own
creation. A retention failure never fails the checkpoint that
triggered it — it's logged and otherwise ignored.

## Recovery

- **A crash or kill mid-operation** can leave a stale `index.lock` in
  the store. The next checkpoint attempt fails with a clear,
  actionable error naming the lock file's path, rather than hanging
  or corrupting anything — delete the file (if no other Pearl process
  is actually running) and try again.
- **A missing or corrupted metadata file** (labels, deleted flags) is
  never fatal: it's rebuilt from `git log` on next use, exactly
  equivalent to a store where nothing has ever been renamed or
  deleted.
- **Restoring is idempotent** — restoring the same checkpoint twice in
  a row is safe; the second restore reports that nothing changed.

## Developer guide

Everything above is implemented by
[`CheckpointManager`](../src/tools/checkpoints.py) — a single class,
no dependency beyond the `git` binary Pearl already requires for
`git_tools.py`.

### Why deletion never rewrites history

`delete()` and `rename()` never touch git history. A git commit is
treated as permanent content, immutable by design; a small JSON
metadata sidecar (`metadata.json`, next to `shadow.git`) is the
mutable, user-facing layer on top of it — a label that can change,
and a `deleted` flag that hides a checkpoint from `list()` and refuses
`restore()`/`preview_restore()`. Actually stripping a commit out of
the middle of a linear history means rewriting every commit after it,
which is real corruption risk for a tool whose entire job is being a
safety net — for content this small (text diffs, not large binaries),
there's no real benefit to reclaiming the space.

### Why storage lives outside the workspace

The store used to live at `<workspace>/.pearl/`. That meant writing
into the user's own project — surprising enough that it required also
editing their `.gitignore` to compensate, which was itself a signal
the original design was wrong. The external, home-directory-keyed
layout (`~/.pearl/workspaces/<key>/`) never writes into the user's
project at all. A workspace that happens to be an *ancestor* of the
store (e.g. someone opens Pearl on `~` itself) is still handled
correctly: `_write_exclude()` excludes the store by its real resolved
path relative to the workspace, not only by the legacy directory name
— found and fixed by testing that exact scenario directly, not by
assuming the "store is always external" invariant made it moot.

### Extension points

- `CheckpointManager(workspace, pearl_home=..., max_count=..., max_age_days=...)` —
  all four are constructor-injectable for testing, defaulting to the
  real workspace/home/`Settings` values.
- `AutonomousExecutor(..., checkpoints=...)` and
  `MCPServer(..., checkpoints=...)` / `PearlAgent(..., checkpoints=...)`
  all accept an explicit `CheckpointManager`, so the automatic
  pre-write checkpoint and any manual checkpoint calls share exactly
  one store rather than each building its own.
- Timeline integration: `EventKind.CHECKPOINT` (see
  [`docs/personality.md`](personality.md)) is emitted as a
  `checkpoint_created` progress event whenever the automatic pre-write
  checkpoint actually captures something — not when there was nothing
  new to record, and not on a swallowed failure.

### Testing

- [`tests/test_checkpoints.py`](../tests/test_checkpoints.py) — the
  `CheckpointManager` unit suite, against a real `git` binary and a
  real temp workspace (faking git here would test nothing).
- [`tests/test_mcp_checkpoints.py`](../tests/test_mcp_checkpoints.py) —
  the `pearl/checkpoint*` MCP methods, including that manual and
  automatic checkpoints share one store.
- [`tests/test_executor.py`](../tests/test_executor.py) — the
  `checkpoint_created` progress event.
- `vscode-extension/src/test/checkpointClient.test.ts`,
  `checkpointTree.test.ts`, `checkpointTreeState.test.ts` — the VS
  Code side, behind the `RequestSender` abstraction (the same pattern
  as `personalityClient.test.ts`). `checkpointCommands.ts` itself
  (the one file that calls the real `vscode` API for dialogs) has no
  automated test, matching the existing convention for
  `vscodeToolApprover.ts` — verified instead by live/manual testing.
