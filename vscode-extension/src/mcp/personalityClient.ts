/**
 * Fetches Pearl's configured-personality wording for the plan-preview
 * timeline stages (`pearl/personality` — see `src/mcp/server.py`'s
 * `_personality_labels`).
 *
 * Python (`src/personality/`) is the single source of truth for this
 * text — nothing here re-implements the personality/emoji tables. The
 * webview only ever renders whatever label this returns.
 */

import { RequestSender } from "./requestSender";
import { TimelineStage } from "../chat/timeline";

export type TimelineLabels = Record<TimelineStage, string>;

const TIMELINE_STAGES: readonly TimelineStage[] = [
  "planning",
  "plan_ready",
  "waiting_approval",
  "running_tool",
  "completed",
];

function isTimelineLabels(value: unknown): value is TimelineLabels {
  if (typeof value !== "object" || value === null) {
    return false;
  }

  const candidate = value as Record<string, unknown>;

  return TIMELINE_STAGES.every((stage) => typeof candidate[stage] === "string");
}

/**
 * Fetch the current personality's label for every timeline stage in
 * one round trip.
 */
export async function fetchTimelineLabels(
  sender: RequestSender
): Promise<TimelineLabels> {
  const result = await sender.sendRequest("pearl/personality", {});

  if (
    typeof result !== "object" ||
    result === null ||
    !isTimelineLabels((result as { labels?: unknown }).labels)
  ) {
    throw new Error("Malformed response from Pearl MCP server.");
  }

  return (result as { labels: TimelineLabels }).labels;
}
