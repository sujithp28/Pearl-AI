/**
 * Client for Pearl's autonomous-execution-with-patch-preview
 * workflow (`AutonomousExecutor` + `PatchManager`; see
 * `src/agent/executor.py` and `src/tools/patch_manager.py`).
 *
 * Targets three MCP methods — `pearl/runAutonomous`,
 * `pearl/approvePatches`, `pearl/rejectPatches` — that mirror the
 * Python-side `AutonomousExecutor.run()` / `.approve()` / `.reject()`
 * API one-to-one (`ExecutionReportResult` mirrors `ExecutionReport`,
 * `PatchFileSummary` mirrors `PendingEdit`).
 *
 * These methods are registered server-side in `src/mcp/server.py`
 * (`_run_autonomous`/`_approve_patches`/`_reject_patches`). While a
 * call to any of them is in flight, the server also streams live
 * `pearl/progress` notifications — see `../mcp/progressClient.ts`
 * and `MCPConnection.onNotification`, not this module, since those
 * arrive as JSON-RPC notifications rather than part of the response
 * these functions return.
 */

import { LLM_CALL_TIMEOUT_MS } from "./timeouts";
import { RequestSender } from "./requestSender";

export type ExecutionStopReason =
  | "completed"
  | "max_iterations"
  | "fatal_error"
  | "cancelled"
  | "awaiting_approval"
  | "rejected";

export interface PatchFileSummary {
  path: string;
  diff: string;
  isNewFile: boolean;
}

export interface ExecutionStepResult {
  tool: string;
  arguments: Record<string, unknown>;
  succeeded: boolean;
  summary: string;
}

export interface ExecutionReportResult {
  stopReason: ExecutionStopReason;
  steps: ExecutionStepResult[];
  patches: PatchFileSummary[];
  replansUsed?: number;
}

function isPatchFileSummary(value: unknown): value is PatchFileSummary {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as PatchFileSummary).path === "string" &&
    typeof (value as PatchFileSummary).diff === "string" &&
    typeof (value as PatchFileSummary).isNewFile === "boolean"
  );
}

function isExecutionReportResult(
  value: unknown
): value is ExecutionReportResult {
  if (typeof value !== "object" || value === null) {
    return false;
  }

  const candidate = value as ExecutionReportResult;

  return (
    typeof candidate.stopReason === "string" &&
    Array.isArray(candidate.steps) &&
    Array.isArray(candidate.patches) &&
    candidate.patches.every(isPatchFileSummary)
  );
}

async function requestReport(
  sender: RequestSender,
  method: string,
  params: Record<string, unknown>
): Promise<ExecutionReportResult> {
  const result = await sender.sendRequest(
    method,
    params,
    LLM_CALL_TIMEOUT_MS
  );

  if (!isExecutionReportResult(result)) {
    throw new Error("Malformed response from Pearl MCP server.");
  }

  return result;
}

/**
 * Start (or continue planning for) an autonomous run. Returns
 * immediately with `stopReason: "awaiting_approval"` and the
 * pending `patches` batch if edits are staged, without writing
 * anything — `approvePatches`/`rejectPatches` decide what happens
 * next.
 */
export function runAutonomous(
  sender: RequestSender,
  prompt: string
): Promise<ExecutionReportResult> {
  return requestReport(sender, "pearl/runAutonomous", { prompt });
}

/**
 * Approve every currently pending patch: write it to disk and
 * resume execution exactly where it paused (no re-planning, no
 * re-running completed steps).
 */
export function approvePatches(
  sender: RequestSender
): Promise<ExecutionReportResult> {
  return requestReport(sender, "pearl/approvePatches", {});
}

/**
 * Discard every currently pending patch and stop cleanly.
 */
export function rejectPatches(
  sender: RequestSender
): Promise<ExecutionReportResult> {
  return requestReport(sender, "pearl/rejectPatches", {});
}
