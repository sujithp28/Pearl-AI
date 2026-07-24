/**
 * Executes a single tool via the existing `tools/call` MCP method
 * (backed by the existing `ToolDispatcher`, see `src/mcp/server.py`).
 *
 * This is only ever called once a tool has been approved — see
 * `src/chat/chatController.ts`.
 */

import { RequestSender } from "./requestSender";

export interface ToolCallContentBlock {
  type: string;
  text: string;
}

export interface ToolCallResult {
  content: ToolCallContentBlock[];
  isError: boolean;
}

function isToolCallResult(value: unknown): value is ToolCallResult {
  return (
    typeof value === "object" &&
    value !== null &&
    Array.isArray((value as ToolCallResult).content) &&
    typeof (value as ToolCallResult).isError === "boolean"
  );
}

export async function callTool(
  sender: RequestSender,
  name: string,
  toolArguments: Record<string, unknown>
): Promise<ToolCallResult> {
  const result = await sender.sendRequest("tools/call", {
    name,
    arguments: toolArguments,
  });

  if (!isToolCallResult(result)) {
    throw new Error("Malformed response from Pearl MCP server.");
  }

  return result;
}
