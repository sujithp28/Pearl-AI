/**
 * Presents a proposed execution plan in the chat webview — as
 * opposed to a native modal, like the per-tool approval in
 * `approval.ts` — and resolves once the user chooses "Execute Plan"
 * or "Cancel" there.
 *
 * `WebviewPlanApprover` only depends on an injected `post` callback
 * and a `resolveDecision` call fed back in by the caller (not a
 * real `vscode.Webview` directly), so the whole plan-approval
 * round trip is unit testable without a real webview.
 */

import { PlannedStep } from "../mcp/planClient";
import { FormattedPlanStep, formatPlanSteps } from "./planFormatting";

export type PlanDecision = "execute" | "cancel";

export type PlanApprover = (steps: PlannedStep[]) => Promise<PlanDecision>;

export type PostPlanMessage = (message: {
  type: "showPlan";
  steps: FormattedPlanStep[];
}) => void;

export class WebviewPlanApprover {
  private pending: { resolve: (decision: PlanDecision) => void } | null =
    null;

  constructor(private readonly post: PostPlanMessage) {}

  requestApproval: PlanApprover = (steps) => {
    return new Promise<PlanDecision>((resolve) => {
      this.pending = { resolve };
      this.post({ type: "showPlan", steps: formatPlanSteps(steps) });
    });
  };

  /**
   * Feed in the user's decision from the webview. A no-op if there
   * is no pending plan (e.g. a stray/duplicate message).
   */
  resolveDecision(decision: PlanDecision): void {
    const pending = this.pending;

    if (!pending) {
      return;
    }

    this.pending = null;
    pending.resolve(decision);
  }
}
