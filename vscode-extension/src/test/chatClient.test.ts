import assert from "node:assert/strict";
import { test } from "node:test";
import { sendChatMessage } from "../mcp/chatClient";
import { LLM_CALL_TIMEOUT_MS } from "../mcp/timeouts";
import { RequestSender } from "../mcp/requestSender";

function fakeSender(response: unknown): {
  sender: RequestSender;
  calls: Array<{
    method: string;
    params?: Record<string, unknown>;
    timeoutMs?: number;
  }>;
} {
  const calls: Array<{
    method: string;
    params?: Record<string, unknown>;
    timeoutMs?: number;
  }> = [];

  return {
    calls,
    sender: {
      sendRequest: async (method, params, timeoutMs) => {
        calls.push({ method, params, timeoutMs });
        return response;
      },
    },
  };
}

test("sendChatMessage sends the message via pearl/chat and returns the reply", async () => {
  const { sender, calls } = fakeSender({ message: "Hello!" });

  const reply = await sendChatMessage(sender, "hi");

  assert.equal(reply, "Hello!");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, "pearl/chat");
  assert.deepEqual(calls[0].params, { message: "hi" });
});

test("sendChatMessage rejects on a malformed response", async () => {
  const { sender } = fakeSender({ notMessage: true });

  await assert.rejects(
    () => sendChatMessage(sender, "hi"),
    /Malformed response/
  );
});

test("sendChatMessage propagates the underlying sendRequest rejection", async () => {
  const sender: RequestSender = {
    sendRequest: async () => {
      throw new Error("boom");
    },
  };

  await assert.rejects(() => sendChatMessage(sender, "hi"), /boom/);
});

test("sendChatMessage uses the shared LLM call timeout, not the connection default", async () => {
  const { sender, calls } = fakeSender({ message: "Hello!" });

  await sendChatMessage(sender, "hi");

  assert.equal(calls[0].timeoutMs, LLM_CALL_TIMEOUT_MS);
});
