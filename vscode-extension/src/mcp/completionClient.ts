/**
 * Requests a single inline completion for a cursor position, via the
 * `pearl/complete` MCP method (backed by `CompletionService` — see
 * `src/agent/completion.py`).
 *
 * Unlike every other client here, this sits on the typing path. It uses
 * its own short timeout rather than `LLM_CALL_TIMEOUT_MS`: a suggestion
 * that arrives after the developer has typed past it is worse than no
 * suggestion, so a slow completion is abandoned rather than awaited.
 */

import { RequestSender } from "./requestSender";

/**
 * Deliberately far below LLM_CALL_TIMEOUT_MS. The local autocomplete
 * model answers in ~200-350ms; anything beyond this budget has already
 * missed the keystroke it was meant for.
 */
export const COMPLETION_TIMEOUT_MS = 2000;

export interface CompletionResult {
  completion: string;
  cached: boolean;
  /** Why no completion was produced, when `completion` is empty. */
  declinedReason: string | null;
}

function isCompletionResult(value: unknown): value is CompletionResult {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as CompletionResult).completion === "string"
  );
}

/**
 * Fetch one completion. Resolves to `null` rather than throwing when the
 * backend is unavailable or slow — an editor's completion provider must
 * degrade to "no suggestion", never to an error popup mid-keystroke.
 */
export async function requestCompletion(
  sender: RequestSender,
  prefix: string,
  suffix: string,
  language: string
): Promise<CompletionResult | null> {
  try {
    const result = await sender.sendRequest(
      "pearl/complete",
      { prefix, suffix, language },
      COMPLETION_TIMEOUT_MS
    );

    return isCompletionResult(result) ? result : null;
  } catch {
    // Timeout, transport failure, or a disconnected server. All of them
    // mean the same thing to the editor: no suggestion this time.
    return null;
  }
}
