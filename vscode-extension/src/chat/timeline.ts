/**
 * Tool-execution timeline stages shown in the chat while a request
 * is being planned/approved/executed. Pure constants (no `vscode`
 * dependency) so the stage set and labels are directly testable and
 * shared between `ChatController` (which decides when a stage
 * changes) and the webview (which only renders the given stage).
 */

export type TimelineStage =
  | "planning"
  | "plan_ready"
  | "waiting_approval"
  | "running_tool"
  | "completed";

export const TIMELINE_STAGE_ORDER: readonly TimelineStage[] = [
  "planning",
  "plan_ready",
  "waiting_approval",
  "running_tool",
  "completed",
];

export const TIMELINE_STAGE_LABELS: Record<TimelineStage, string> = {
  planning: "Planning...",
  plan_ready: "Plan Ready",
  waiting_approval: "Waiting for Approval",
  running_tool: "Running Tool...",
  completed: "Completed",
};
