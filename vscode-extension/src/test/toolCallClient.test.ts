import assert from "node:assert/strict";
import { test } from "node:test";
import { callTool } from "../mcp/toolCallClient";
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

test("callTool sends the tool name and arguments via tools/call", async () => {
  const { sender, calls } = fakeSender({
    content: [{ type: "text", text: "3" }],
    isError: false,
  });

  const result = await callTool(sender, "add", { a: 1, b: 2 });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, "tools/call");
  assert.deepEqual(calls[0].params, {
    name: "add",
    arguments: { a: 1, b: 2 },
  });
  assert.deepEqual(result, {
    content: [{ type: "text", text: "3" }],
    isError: false,
  });
});

test("callTool returns isError results as-is (not thrown)", async () => {
  const { sender } = fakeSender({
    content: [{ type: "text", text: "kaboom" }],
    isError: true,
  });

  const result = await callTool(sender, "boom", {});

  assert.equal(result.isError, true);
  assert.equal(result.content[0].text, "kaboom");
});

test("callTool rejects on a malformed response", async () => {
  const { sender } = fakeSender({ unexpected: true });

  await assert.rejects(
    () => callTool(sender, "add", {}),
    /Malformed response/
  );
});

test("callTool propagates the underlying sendRequest rejection", async () => {
  const sender: RequestSender = {
    sendRequest: async () => {
      throw new Error("Not connected to Pearl MCP server.");
    },
  };

  await assert.rejects(
    () => callTool(sender, "add", {}),
    /Not connected/
  );
});
