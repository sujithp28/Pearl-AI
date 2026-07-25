/**
 * Asks Pearl's planner what it would do for a prompt, without
 * executing anything — via the `pearl/planOnly` MCP method (backed
 * by the existing `Planner.plan()`, see `src/mcp/server.py`).
 *
 * This is the client-side half of the tool-approval flow: the
 * proposed steps are returned so the caller can gate each one
 * behind an approval decision before ever calling `tools/call`.
 */

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

// Backed by `Planner.plan()` — one LLM call, same order of latency as
// `pearl/chat` (see `chatClient.ts`), so it needs the same budget:
// the connection's default 10s timeout is tuned for cheap, non-LLM
// round trips and was cutting this off before a real (sometimes slow,
// especially on a local/CPU-bound provider) model response arrived —
// and unlike a fast request, an abandoned client-side wait here left
// the request still running server-side (Pearl's MCP server handles
// one request at a time), silently delaying whatever the client sent
// next by however much longer the first call actually took.
const PLAN_TIMEOUT_MS = 60000;

export async function planOnly(
  sender: RequestSender,
  prompt: string
): Promise<PlannedStep[]> {
  const result = await sender.sendRequest(
    "pearl/planOnly",
    { prompt },
    PLAN_TIMEOUT_MS
  );

  if (!isPlanOnlyResult(result)) {
    throw new Error("Malformed response from Pearl MCP server.");
  }

  return result.steps;
}
