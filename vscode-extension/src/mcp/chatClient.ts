/**
 * Sends chat messages through an existing MCP connection.
 *
 * Depends only on the `sendRequest` shape (not the concrete
 * `MCPConnection` class), so it can be unit tested with a plain
 * fake sender instead of a real connection/process.
 */

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
// LLM completions (plus the provider's own retry/backoff on transient
// errors) can comfortably exceed the connection's default 10s request
// timeout, especially for longer prompts.
const CHAT_TIMEOUT_MS = 60000;

export async function sendChatMessage(
  sender: RequestSender,
  message: string
): Promise<string> {
  const result = await sender.sendRequest(
    "pearl/chat",
    { message },
    CHAT_TIMEOUT_MS
  );

  if (!isChatResult(result)) {
    throw new Error("Malformed response from Pearl MCP server.");
  }

  return result.message;
}
