/**
 * Client for Pearl's checkpoint system (Sprint 1) — targets the six
 * `pearl/checkpoint*` MCP methods registered in `src/mcp/server.py`
 * (`_checkpoint_create`, `_checkpoints_list`,
 * `_checkpoint_restore_preview`, `_checkpoint_restore`,
 * `_checkpoint_delete`, `_checkpoint_rename`), which are thin JSON
 * translations over `src/tools/checkpoints.py`'s `CheckpointManager`.
 *
 * No explicit timeout override: unlike `patchClient`/`chatClient`,
 * these never go through the LLM — they're git operations, fast
 * enough for `MCPConnection`'s own default.
 */

import { RequestSender } from "./requestSender";

export interface Checkpoint {
  id: string;
  shortId: string;
  label: string;
  createdAt: string;
}

export interface RestoreReport {
  checkpointId: string;
  restored: string[];
  removed: string[];
  changedAnything: boolean;
}

function isCheckpoint(value: unknown): value is Checkpoint {
  if (typeof value !== "object" || value === null) {
    return false;
  }

  const candidate = value as Checkpoint;

  return (
    typeof candidate.id === "string" &&
    typeof candidate.shortId === "string" &&
    typeof candidate.label === "string" &&
    typeof candidate.createdAt === "string"
  );
}

function isRestoreReport(value: unknown): value is RestoreReport {
  if (typeof value !== "object" || value === null) {
    return false;
  }

  const candidate = value as RestoreReport;

  return (
    typeof candidate.checkpointId === "string" &&
    Array.isArray(candidate.restored) &&
    Array.isArray(candidate.removed) &&
    typeof candidate.changedAnything === "boolean"
  );
}

/**
 * Create a checkpoint. Returns `null` when nothing has changed since
 * the previous one (nothing new to capture), not an error.
 */
export async function createCheckpoint(
  sender: RequestSender,
  label?: string
): Promise<Checkpoint | null> {
  const result = await sender.sendRequest(
    "pearl/checkpointCreate",
    label ? { label } : {}
  );

  const checkpoint = (result as { checkpoint?: unknown } | null)?.checkpoint ?? null;

  if (checkpoint === null) {
    return null;
  }

  if (!isCheckpoint(checkpoint)) {
    throw new Error("Malformed response from Pearl MCP server.");
  }

  return checkpoint;
}

/**
 * List checkpoints, newest first.
 */
export async function listCheckpoints(
  sender: RequestSender,
  limit?: number
): Promise<Checkpoint[]> {
  const result = await sender.sendRequest(
    "pearl/checkpoints",
    limit ? { limit } : {}
  );

  const checkpoints = (result as { checkpoints?: unknown } | null)?.checkpoints;

  if (!Array.isArray(checkpoints) || !checkpoints.every(isCheckpoint)) {
    throw new Error("Malformed response from Pearl MCP server.");
  }

  return checkpoints;
}

async function requestRestoreReport(
  sender: RequestSender,
  method: string,
  checkpointId: string
): Promise<RestoreReport> {
  const result = await sender.sendRequest(method, { id: checkpointId });

  if (!isRestoreReport(result)) {
    throw new Error("Malformed response from Pearl MCP server.");
  }

  return result;
}

/**
 * Report what restoring `checkpointId` would change, without
 * changing anything — restoring can delete files created since the
 * checkpoint, so callers should show this and get confirmation before
 * calling `restoreCheckpoint`, the same way Pearl shows a diff before
 * writing.
 */
export function previewRestoreCheckpoint(
  sender: RequestSender,
  checkpointId: string
): Promise<RestoreReport> {
  return requestRestoreReport(
    sender,
    "pearl/checkpointRestorePreview",
    checkpointId
  );
}

/**
 * Restore the workspace to `checkpointId`.
 */
export function restoreCheckpoint(
  sender: RequestSender,
  checkpointId: string
): Promise<RestoreReport> {
  return requestRestoreReport(sender, "pearl/checkpointRestore", checkpointId);
}

/**
 * Delete a checkpoint (hides it from listing and future restores —
 * the underlying git commit is never rewritten, see
 * `CheckpointManager.delete`).
 */
export async function deleteCheckpoint(
  sender: RequestSender,
  checkpointId: string
): Promise<void> {
  await sender.sendRequest("pearl/checkpointDelete", { id: checkpointId });
}

/**
 * Change a checkpoint's display label.
 */
export async function renameCheckpoint(
  sender: RequestSender,
  checkpointId: string,
  label: string
): Promise<Checkpoint> {
  const result = await sender.sendRequest("pearl/checkpointRename", {
    id: checkpointId,
    label,
  });

  const checkpoint = (result as { checkpoint?: unknown } | null)?.checkpoint;

  if (!isCheckpoint(checkpoint)) {
    throw new Error("Malformed response from Pearl MCP server.");
  }

  return checkpoint;
}
