/**
 * Execution-state labels shown in the chat while an autonomous run
 * is paused for patch approval, applying approved patches, or
 * resuming. Pure constants (no `vscode` dependency), mirroring
 * `timeline.ts` — `ChatController` decides when the state changes,
 * the webview only renders the given state.
 */

export type ExecutionState =
  | "awaiting_approval"
  | "applying_patches"
  | "resuming"
  | "completed"
  | "cancelled";

export const EXECUTION_STATE_LABELS: Record<ExecutionState, string> = {
  awaiting_approval: "Awaiting Approval",
  applying_patches: "Applying Patches...",
  resuming: "Resuming Execution...",
  completed: "Completed",
  cancelled: "Cancelled",
};
