/**
 * VS Code command handlers for the checkpoint tree view — the only
 * file in the checkpoint feature that touches the real `vscode` API
 * for dialogs, mirroring `../chat/vscodeToolApprover.ts`'s role for
 * tool approval.
 */

import * as vscode from "vscode";
import {
  createCheckpoint,
  deleteCheckpoint,
  previewRestoreCheckpoint,
  renameCheckpoint,
  restoreCheckpoint,
} from "../mcp/checkpointClient";
import { RequestSender } from "../mcp/requestSender";
import { CheckpointTreeItem } from "./checkpointTreeProvider";

const RESTORE_LABEL = "Restore";
const DELETE_LABEL = "Delete";

function describeError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export async function runCreateCheckpointCommand(
  connection: RequestSender,
  refresh: () => Promise<void>
): Promise<void> {
  const label = await vscode.window.showInputBox({
    prompt: "Checkpoint label (optional)",
    placeHolder: "Manual checkpoint",
  });

  // Cancelling the input box (Escape) returns undefined; that must
  // abort the command entirely, not create a checkpoint with an
  // accidental empty label. An empty string (user pressed Enter on a
  // blank box) is a deliberate choice to accept the default label.
  if (label === undefined) {
    return;
  }

  try {
    const checkpoint = await createCheckpoint(
      connection,
      label.trim() || undefined
    );

    if (checkpoint === null) {
      vscode.window.showInformationMessage(
        "Nothing has changed since the last checkpoint."
      );
    } else {
      vscode.window.showInformationMessage(
        `Checkpoint saved: ${checkpoint.label}`
      );
    }
  } catch (error) {
    vscode.window.showErrorMessage(
      `Could not create checkpoint: ${describeError(error)}`
    );
  }

  await refresh();
}

export async function runRestoreCheckpointCommand(
  connection: RequestSender,
  refresh: () => Promise<void>,
  item: CheckpointTreeItem
): Promise<void> {
  const checkpoint = item.node.checkpoint;

  if (!checkpoint) {
    return;
  }

  try {
    const preview = await previewRestoreCheckpoint(connection, checkpoint.id);

    if (!preview.changedAnything) {
      vscode.window.showInformationMessage(
        "Nothing to restore — the workspace already matches this checkpoint."
      );
      return;
    }

    const changeLines = [
      ...preview.restored.map((path) => `Revert: ${path}`),
      ...preview.removed.map((path) => `Remove: ${path}`),
    ];

    // Modal, not a toast: restoring deletes files created since the
    // checkpoint, so this needs an explicit confirmation the user
    // can't miss — the same reasoning as showing a diff before
    // writing a patch.
    const choice = await vscode.window.showWarningMessage(
      `Restore "${checkpoint.label}"? This will:\n${changeLines.join("\n")}`,
      { modal: true },
      RESTORE_LABEL
    );

    if (choice !== RESTORE_LABEL) {
      return;
    }

    await restoreCheckpoint(connection, checkpoint.id);
    vscode.window.showInformationMessage(
      `Restored checkpoint: ${checkpoint.label}`
    );
  } catch (error) {
    vscode.window.showErrorMessage(
      `Could not restore checkpoint: ${describeError(error)}`
    );
  }

  await refresh();
}

export async function runDeleteCheckpointCommand(
  connection: RequestSender,
  refresh: () => Promise<void>,
  item: CheckpointTreeItem
): Promise<void> {
  const checkpoint = item.node.checkpoint;

  if (!checkpoint) {
    return;
  }

  const choice = await vscode.window.showWarningMessage(
    `Delete checkpoint "${checkpoint.label}"?`,
    { modal: true },
    DELETE_LABEL
  );

  if (choice !== DELETE_LABEL) {
    return;
  }

  try {
    await deleteCheckpoint(connection, checkpoint.id);
  } catch (error) {
    vscode.window.showErrorMessage(
      `Could not delete checkpoint: ${describeError(error)}`
    );
  }

  await refresh();
}

export async function runRenameCheckpointCommand(
  connection: RequestSender,
  refresh: () => Promise<void>,
  item: CheckpointTreeItem
): Promise<void> {
  const checkpoint = item.node.checkpoint;

  if (!checkpoint) {
    return;
  }

  const label = await vscode.window.showInputBox({
    prompt: "New checkpoint label",
    value: checkpoint.label,
  });

  if (!label || !label.trim()) {
    return;
  }

  try {
    await renameCheckpoint(connection, checkpoint.id, label.trim());
  } catch (error) {
    vscode.window.showErrorMessage(
      `Could not rename checkpoint: ${describeError(error)}`
    );
  }

  await refresh();
}
