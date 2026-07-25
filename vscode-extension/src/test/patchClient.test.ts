import assert from "node:assert/strict";
import { test } from "node:test";
import {
  approvePatches,
  rejectPatches,
  runAutonomous,
} from "../mcp/patchClient";
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

const SAMPLE_REPORT = {
  stopReason: "awaiting_approval",
  steps: [
    { tool: "create_file", arguments: { path: "a.py" }, succeeded: true, summary: "created a.py" },
  ],
  patches: [
    { path: "a.py", diff: "--- /dev/null\n+++ b/a.py\n@@ -0,0 +1 @@\n+x = 1", isNewFile: true },
  ],
};

test("runAutonomous sends the prompt via pearl/runAutonomous and returns the report", async () => {
  const { sender, calls } = fakeSender(SAMPLE_REPORT);

  const report = await runAutonomous(sender, "create a.py");

  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, "pearl/runAutonomous");
  assert.deepEqual(calls[0].params, { prompt: "create a.py" });
  assert.equal(calls[0].timeoutMs, LLM_CALL_TIMEOUT_MS);
  assert.deepEqual(report, SAMPLE_REPORT);
});

test("runAutonomous rejects on a malformed response", async () => {
  const { sender } = fakeSender({ notAReport: true });

  await assert.rejects(
    () => runAutonomous(sender, "create a.py"),
    /Malformed response/
  );
});

test("approvePatches sends pearl/approvePatches with no params", async () => {
  const completed = { ...SAMPLE_REPORT, stopReason: "completed", patches: [] };
  const { sender, calls } = fakeSender(completed);

  const report = await approvePatches(sender);

  assert.equal(calls[0].method, "pearl/approvePatches");
  assert.deepEqual(calls[0].params, {});
  assert.equal(report.stopReason, "completed");
});

test("rejectPatches sends pearl/rejectPatches with no params", async () => {
  const rejected = { ...SAMPLE_REPORT, stopReason: "rejected", patches: [] };
  const { sender, calls } = fakeSender(rejected);

  const report = await rejectPatches(sender);

  assert.equal(calls[0].method, "pearl/rejectPatches");
  assert.deepEqual(calls[0].params, {});
  assert.equal(report.stopReason, "rejected");
});

test("runAutonomous propagates the underlying sendRequest rejection", async () => {
  const sender: RequestSender = {
    sendRequest: async () => {
      throw new Error("Autonomous execution is not enabled on this server.");
    },
  };

  await assert.rejects(
    () => runAutonomous(sender, "do something"),
    /Autonomous execution is not enabled/
  );
});

test("approvePatches rejects on a response missing patches", async () => {
  const { sender } = fakeSender({ stopReason: "completed", steps: [] });

  await assert.rejects(() => approvePatches(sender), /Malformed response/);
});
