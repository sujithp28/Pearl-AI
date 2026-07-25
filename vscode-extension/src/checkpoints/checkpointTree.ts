/**
 * Pure formatting: turns a `Checkpoint[]` (from `checkpointClient.ts`)
 * into a simple, renderable tree — one leaf per checkpoint, newest
 * first, exactly as the server returns them. No `vscode` dependency,
 * so this is directly unit testable independent of any tree view
 * widget — mirrors `../memory/memoryTree.ts`.
 */

import { Checkpoint } from "../mcp/checkpointClient";

export interface CheckpointTreeNode {
  label: string;
  description?: string;
  tooltip?: string;
  checkpoint?: Checkpoint;
}

function formatTimestamp(iso: string): string {
  const parsed = new Date(iso);

  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString();
}

export function buildCheckpointTree(
  checkpoints: Checkpoint[]
): CheckpointTreeNode[] {
  if (checkpoints.length === 0) {
    return [{ label: "No checkpoints yet." }];
  }

  return checkpoints.map((checkpoint) => ({
    label: checkpoint.label,
    description: formatTimestamp(checkpoint.createdAt),
    tooltip: `${checkpoint.shortId} — ${checkpoint.createdAt}\n${checkpoint.label}`,
    checkpoint,
  }));
}
