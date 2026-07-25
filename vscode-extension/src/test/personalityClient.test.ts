import assert from "node:assert/strict";
import { test } from "node:test";
import { fetchTimelineLabels } from "../mcp/personalityClient";
import { RequestSender } from "../mcp/requestSender";

const VALID_LABELS = {
  planning: "🧠 Thinking...",
  plan_ready: "📋 Plan ready.",
  waiting_approval: "⏳ Awaiting approval.",
  running_tool: "⚙️ Running...",
  completed: "🎉 Completed!",
};

function fakeSender(response: unknown): {
  sender: RequestSender;
  calls: Array<{ method: string; params?: Record<string, unknown> }>;
} {
  const calls: Array<{ method: string; params?: Record<string, unknown> }> = [];

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

test("fetchTimelineLabels sends pearl/personality with no params", async () => {
  const { sender, calls } = fakeSender({ labels: VALID_LABELS });

  await fetchTimelineLabels(sender);

  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, "pearl/personality");
  assert.deepEqual(calls[0].params, {});
});

test("fetchTimelineLabels returns the labels map", async () => {
  const { sender } = fakeSender({ labels: VALID_LABELS });

  const labels = await fetchTimelineLabels(sender);

  assert.deepEqual(labels, VALID_LABELS);
});

test("fetchTimelineLabels rejects when a stage is missing", async () => {
  const { completed: _completed, ...incomplete } = VALID_LABELS;

  const { sender } = fakeSender({ labels: incomplete });

  await assert.rejects(() => fetchTimelineLabels(sender), /Malformed response/);
});

test("fetchTimelineLabels rejects when a stage isn't a string", async () => {
  const { sender } = fakeSender({ labels: { ...VALID_LABELS, planning: 42 } });

  await assert.rejects(() => fetchTimelineLabels(sender), /Malformed response/);
});

test("fetchTimelineLabels rejects on a completely malformed response", async () => {
  const { sender } = fakeSender({ notLabels: true });

  await assert.rejects(() => fetchTimelineLabels(sender), /Malformed response/);
});

test("fetchTimelineLabels propagates the underlying sendRequest rejection", async () => {
  const sender: RequestSender = {
    sendRequest: async () => {
      throw new Error("boom");
    },
  };

  await assert.rejects(() => fetchTimelineLabels(sender), /boom/);
});
