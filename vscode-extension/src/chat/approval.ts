/**
 * Tool-approval abstraction.
 *
 * Kept independent of `vscode` (and of any particular UI mechanism)
 * so the approval-gated chat flow in `chatController.ts` can be
 * unit tested with a fake, instantly-resolving approver instead of
 * a real modal dialog.
 */

export interface ToolApprovalRequest {
  tool: string;
  arguments: Record<string, unknown>;
}

export type ApprovalDecision = "approved" | "rejected";

export type ToolApprover = (
  request: ToolApprovalRequest
) => Promise<ApprovalDecision>;
