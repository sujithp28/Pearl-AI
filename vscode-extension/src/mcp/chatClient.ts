/**
 * Sends chat messages through an existing MCP connection.
 *
 * Depends only on the `sendRequest` / `onNotification` shape (not the
 * concrete `MCPConnection` class), so it can be unit tested with a
 * plain fake sender instead of a real connection/process.
 */

import { LLM_CALL_TIMEOUT_MS } from "./timeouts";
import { RequestSender } from "./requestSender";

interface ChatResult {
  message: string;
}

function isChatResult(value: unknown): value is ChatResult {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as ChatResult).message === "string"
  );
}

/**
 * Send `message` to Pearl's `pearl/chat` MCP method and return the
 * assistant's reply text.
 */
export async function sendChatMessage(
  sender: RequestSender,
  message: string
): Promise<string> {
  const result = await sender.sendRequest(
    "pearl/chat",
    { message },
    LLM_CALL_TIMEOUT_MS
  );

  if (!isChatResult(result)) {
    throw new Error("Malformed response from Pearl MCP server.");
  }

  return result.message;
}

/**
 * Send `message` to Pearl's `pearl/chat` MCP method and stream the
 * response, calling `onChunk` with each text fragment as it arrives.
 *
 * Returns the full accumulated reply (same as `sendChatMessage`) once
 * the stream is complete. Falls back gracefully when `onNotification`
 * is not available on the sender (e.g. unit-test fakes): in that case
 * `onChunk` is never called and the full reply is returned at the end.
 */
export async function sendChatMessageStream(
  sender: RequestSender,
  message: string,
  onChunk: (chunk: string) => void
): Promise<string> {
  let unsubscribe: (() => void) | undefined;

  if (sender.onNotification) {
    unsubscribe = sender.onNotification("pearl/chatChunk", (params) => {
      const chunk = (params as { chunk?: string }).chunk;
      if (typeof chunk === "string" && chunk) {
        onChunk(chunk);
      }
    });
  }

  try {
    const result = await sender.sendRequest(
      "pearl/chat",
      { message },
      LLM_CALL_TIMEOUT_MS
    );

    if (!isChatResult(result)) {
      throw new Error("Malformed response from Pearl MCP server.");
    }

    return result.message;
  } finally {
    unsubscribe?.();
  }
}
