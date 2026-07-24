/**
 * Reads Pearl's Memory contents through the existing MCP connection,
 * via the `pearl/memory` MCP method (backed by the existing
 * `Memory.to_dict()`, see `src/mcp/server.py`). Read-only: this
 * module never mutates Memory.
 */

import { RequestSender } from "../mcp/requestSender";

export interface ConversationTurnSnapshot {
  role: string;
  content: string;
  timestamp: string;
}

export interface TaskSnapshot {
  id: string;
  description: string;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface ExecutionRecordSnapshot {
  tool_name: string;
  kwargs: Record<string, unknown>;
  result: unknown;
  error: string | null;
  timestamp: string;
}

export interface MemorySnapshot {
  conversation: ConversationTurnSnapshot[];
  tasks: TaskSnapshot[];
  project: Record<string, unknown>;
  execution_history: ExecutionRecordSnapshot[];
}

function isMemorySnapshot(value: unknown): value is MemorySnapshot {
  if (typeof value !== "object" || value === null) {
    return false;
  }

  const candidate = value as MemorySnapshot;

  return (
    Array.isArray(candidate.conversation) &&
    Array.isArray(candidate.tasks) &&
    typeof candidate.project === "object" &&
    candidate.project !== null &&
    Array.isArray(candidate.execution_history)
  );
}

export async function fetchMemory(
  sender: RequestSender
): Promise<MemorySnapshot> {
  const result = await sender.sendRequest("pearl/memory", {});

  if (!isMemorySnapshot(result)) {
    throw new Error("Malformed response from Pearl MCP server.");
  }

  return result;
}
