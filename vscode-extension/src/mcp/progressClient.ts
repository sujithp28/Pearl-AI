/**
 * Types and parsing for `pearl/progress` notifications.
 *
 * Mirrors `AutonomousExecutor.ProgressEvent` (`src/agent/executor.py`)
 * as forwarded by `src/mcp/server.py`'s progress-notification
 * plumbing while an autonomous run (`pearl/runAutonomous` /
 * `pearl/approvePatches` / `pearl/rejectPatches`) is in flight.
 * Delivered as JSON-RPC *notifications*, not responses — see
 * `MCPConnection.onNotification`, not `RequestSender.sendRequest`.
 */

export type ProgressStatus =
  | "planning"
  | "executing_step"
  | "step_completed"
  | "step_failed"
  | "replanning"
  | "task_completed"
  | "cancelled"
  | "awaiting_approval"
  | "rejected";

export interface ProgressEvent {
  status: ProgressStatus;
  currentStep: number;
  totalSteps: number;
  currentAction: string;
}

const PROGRESS_STATUSES: readonly string[] = [
  "planning",
  "executing_step",
  "step_completed",
  "step_failed",
  "replanning",
  "task_completed",
  "cancelled",
  "awaiting_approval",
  "rejected",
];

function isProgressStatus(value: unknown): value is ProgressStatus {
  return typeof value === "string" && PROGRESS_STATUSES.includes(value);
}

/**
 * Validate and narrow a `pearl/progress` notification's raw `params`
 * into a `ProgressEvent`, or return null if the payload doesn't
 * match the expected shape (e.g. a future/older server version) —
 * malformed progress data should never crash the chat UI, it should
 * just be silently dropped.
 */
export function parseProgressEvent(
  params: Record<string, unknown>
): ProgressEvent | null {
  if (
    !isProgressStatus(params.status) ||
    typeof params.currentStep !== "number" ||
    typeof params.totalSteps !== "number" ||
    typeof params.currentAction !== "string"
  ) {
    return null;
  }

  return {
    status: params.status,
    currentStep: params.currentStep,
    totalSteps: params.totalSteps,
    currentAction: params.currentAction,
  };
}
