import assert from "node:assert/strict";
import { test } from "node:test";
import { planOnly } from "../mcp/planClient";
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

test("planOnly sends the prompt via pearl/planOnly and returns the steps", async () => {
  const { sender, calls } = fakeSender({
    steps: [{ tool: "read_file", arguments: { path: "a.txt" } }],
  });

  const steps = await planOnly(sender, "read a.txt");

  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, "pearl/planOnly");
  assert.deepEqual(calls[0].params, { prompt: "read a.txt" });
  assert.deepEqual(steps, [
    { tool: "read_file", arguments: { path: "a.txt" } },
  ]);
});

test("planOnly returns an empty array for an empty plan", async () => {
  const { sender } = fakeSender({ steps: [] });

  const steps = await planOnly(sender, "do nothing");

  assert.deepEqual(steps, []);
});

test("planOnly rejects on a malformed response", async () => {
  const { sender } = fakeSender({ notSteps: true });

  await assert.rejects(
    () => planOnly(sender, "read a.txt"),
    /Malformed response/
  );
});

test("planOnly propagates the underlying sendRequest rejection", async () => {
  const sender: RequestSender = {
    sendRequest: async () => {
      throw new Error("Planning is not enabled on this server.");
    },
  };

  await assert.rejects(
    () => planOnly(sender, "read a.txt"),
    /Planning is not enabled/
  );
});
