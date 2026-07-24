import * as vscode from "vscode";
import { ApprovalDecision, ToolApprovalRequest, ToolApprover } from "./approval";

const APPROVE_LABEL = "Approve";
const REJECT_LABEL = "Reject";

/**
 * Shows a modal dialog with the tool name and its arguments, and
 * resolves to the user's decision. The only file in the tool
 * approval feature that touches the real `vscode` API.
 */
export const showToolApprovalDialog: ToolApprover = async (
  request: ToolApprovalRequest
): Promise<ApprovalDecision> => {
  const argumentsText = JSON.stringify(request.arguments, null, 2);

  const choice = await vscode.window.showWarningMessage(
    `Pearl wants to run "${request.tool}" with arguments:\n${argumentsText}`,
    { modal: true },
    APPROVE_LABEL,
    REJECT_LABEL
  );

  return choice === APPROVE_LABEL ? "approved" : "rejected";
};
