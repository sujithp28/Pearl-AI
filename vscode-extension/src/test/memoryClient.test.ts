import assert from "node:assert/strict";
import { test } from "node:test";
import { fetchMemory } from "../memory/memoryClient";
import { RequestSender } from "../mcp/requestSender";

function fakeSender(response: unknown): {
  sender: RequestSender;
  calls: Array<{ method: string; params?: Record<string, unknown> }>;
} {
  const calls: Array<{ method: string; params?: Record<string, unknown> }> =
    [];

  return {
    calls,
    sender: {
      sendRequest: async (method, params) => {
        calls.push({ method, params });
        return response;
      },
    },
  };
}

test("fetchMemory calls pearl/memory and returns the snapshot", async () => {
  const snapshot = {
    conversation: [{ role: "user", content: "hi", timestamp: "t1" }],
    tasks: [],
    project: { language: "python" },
    execution_history: [],
  };
  const { sender, calls } = fakeSender(snapshot);

  const result = await fetchMemory(sender);

  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, "pearl/memory");
  assert.deepEqual(result, snapshot);
});

test("fetchMemory rejects on a malformed response", async () => {
  const { sender } = fakeSender({ unexpected: true });

  await assert.rejects(() => fetchMemory(sender), /Malformed response/);
});

test("fetchMemory rejects when a required field is missing", async () => {
  const { sender } = fakeSender({
    conversation: [],
    tasks: [],
    execution_history: [],
    // "project" missing
  });

  await assert.rejects(() => fetchMemory(sender), /Malformed response/);
});

test("fetchMemory propagates the underlying sendRequest rejection", async () => {
  const sender: RequestSender = {
    sendRequest: async () => {
      throw new Error("Not connected to Pearl MCP server.");
    },
  };

  await assert.rejects(() => fetchMemory(sender), /Not connected/);
});
