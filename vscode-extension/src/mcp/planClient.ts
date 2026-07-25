/**
 * Asks Pearl's planner what it would do for a prompt, without
 * executing anything — via the `pearl/planOnly` MCP method (backed
 * by the existing `Planner.plan()`, see `src/mcp/server.py`).
 *
 * This is the client-side half of the tool-approval flow: the
 * proposed steps are returned so the caller can gate each one
 * behind an approval decision before ever calling `tools/call`.
 */

import { LLM_CALL_TIMEOUT_MS } from "./timeouts";
import { RequestSender } from "./requestSender";

export interface PlannedStep {
  tool: string;
  arguments: Record<string, unknown>;
}

interface PlanOnlyResult {
  steps: PlannedStep[];
}

function isPlanOnlyResult(value: unknown): value is PlanOnlyResult {
  return (
    typeof value === "object" &&
    value !== null &&
    Array.isArray((value as PlanOnlyResult).steps)
  );
}

export async function planOnly(
  sender: RequestSender,
  prompt: string
): Promise<PlannedStep[]> {
  const result = await sender.sendRequest(
    "pearl/planOnly",
    { prompt },
    LLM_CALL_TIMEOUT_MS
  );

  if (!isPlanOnlyResult(result)) {
    throw new Error("Malformed response from Pearl MCP server.");
  }

  return result.steps;
}
