/**
 * Presents a pending patch batch (from `AutonomousExecutor`'s
 * patch-preview pause — see `src/agent/executor.py`) in the chat
 * webview, and resolves once the user chooses "Approve" or "Reject"
 * there.
 *
 * Mirrors `WebviewPlanApprover` (`planApproval.ts`) exactly: depends
 * only on an injected `post` callback and a `resolveDecision` call
 * fed back in by the caller (not a real `vscode.Webview` directly),
 * so the whole approval round trip is unit testable without a real
 * webview.
 */

import { PatchFileSummary } from "../mcp/patchClient";
import { FormattedPatchFile, formatPatchFiles } from "./patchFormatting";

export type PatchDecision = "approve" | "reject";

export type PatchApprover = (
  files: PatchFileSummary[]
) => Promise<PatchDecision>;

export type PostPatchMessage = (message: {
  type: "showPatchBatch";
  files: FormattedPatchFile[];
}) => void;

export class WebviewPatchApprover {
  private pending: { resolve: (decision: PatchDecision) => void } | null =
    null;

  constructor(private readonly post: PostPatchMessage) {}

  requestApproval: PatchApprover = (files) => {
    return new Promise<PatchDecision>((resolve) => {
      this.pending = { resolve };
      this.post({ type: "showPatchBatch", files: formatPatchFiles(files) });
    });
  };

  /**
   * Feed in the user's decision from the webview. A no-op if there
   * is no pending patch batch (e.g. a stray/duplicate message).
   */
  resolveDecision(decision: PatchDecision): void {
    const pending = this.pending;

    if (!pending) {
      return;
    }

    this.pending = null;
    pending.resolve(decision);
  }
}
