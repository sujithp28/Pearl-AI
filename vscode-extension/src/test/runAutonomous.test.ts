import assert from "node:assert/strict";
import { test } from "node:test";
import {
  ChatController,
  ChatMessage,
  PostToWebview,
  WebviewMessage,
} from "../chat/chatController";
import { PatchApprover, PatchDecision } from "../chat/patchApproval";
import { PatchFileSummary } from "../mcp/patchClient";
import { RequestSender } from "../mcp/requestSender";

type Handler = (
  params?: Record<string, unknown>
) => unknown | Promise<unknown>;

function fakeSender(handlers: Record<string, Handler>): {
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

        const handler = handlers[method];

        if (!handler) {
          throw new Error(`Unexpected method call: ${method}`);
        }

        return handler(params);
      },
    },
  };
}

function collectingPost(): {
  messages: ChatMessage[];
  events: WebviewMessage[];
  post: PostToWebview;
} {
  const messages: ChatMessage[] = [];
  const events: WebviewMessage[] = [];

  return {
    messages,
    events,
    post: (m) => {
      events.push(m);
      if (m.type === "addMessage") {
        messages.push(m.message);
      }
    },
  };
}

function strip(messages: ChatMessage[]): Array<{ role: string; text: string }> {
  return messages.map((m) => ({ role: m.role, text: m.text }));
}

function fixedPatchApprover(decision: PatchDecision): {
  approvePatch: PatchApprover;
  calls: PatchFileSummary[][];
} {
  const calls: PatchFileSummary[][] = [];

  return {
    calls,
    approvePatch: async (files) => {
      calls.push(files);
      return decision;
    },
  };
}

// A no-op ToolApprover/PlanApprover — runAutonomous never uses them,
// but the ChatController constructor requires them.
const unusedToolApprover = async () => {
  throw new Error("ToolApprover should not be called by runAutonomous.");
};
const unusedPlanApprover = async () => {
  throw new Error("PlanApprover should not be called by runAutonomous.");
};

function singleFilePatch(): PatchFileSummary[] {
  return [
    {
      path: "src/auth.py",
      diff: "--- /dev/null\n+++ b/src/auth.py\n@@ -0,0 +1,2 @@\n+def login():\n+    return True",
      isNewFile: true,
    },
  ];
}

function multiFilePatch(): PatchFileSummary[] {
  return [
    {
      path: "src/main.py",
      diff: "--- /dev/null\n+++ b/src/main.py\n@@ -0,0 +1 @@\n+app = 1",
      isNewFile: true,
    },
    {
      path: "src/models.py",
      diff: "--- /dev/null\n+++ b/src/models.py\n@@ -0,0 +1 @@\n+class User: pass",
      isNewFile: true,
    },
    {
      path: "src/routes.py",
      diff: "--- /dev/null\n+++ b/src/routes.py\n@@ -0,0 +1 @@\n+routes = []",
      isNewFile: true,
    },
  ];
}

// ---------------------------------------------------------------------
// Single-file patch
// ---------------------------------------------------------------------

test("runAutonomous shows a single-file patch batch awaiting approval", async () => {
  const { sender, calls } = fakeSender({
    "pearl/runAutonomous": () => ({
      stopReason: "awaiting_approval",
      steps: [
        {
          tool: "create_file",
          arguments: { path: "src/auth.py" },
          succeeded: true,
          summary: "created src/auth.py",
        },
      ],
      patches: singleFilePatch(),
    }),
    "pearl/approvePatches": () => ({
      stopReason: "completed",
      steps: [
        {
          tool: "create_file",
          arguments: { path: "src/auth.py" },
          succeeded: true,
          summary: "created src/auth.py",
        },
      ],
      patches: [],
    }),
  });

  const { approvePatch, calls: approvalCalls } = fixedPatchApprover("approve");
  const { events } = collectingPost();

  const controller = new ChatController(
    sender,
    collectingPost().post,
    unusedToolApprover,
    unusedPlanApprover,
    approvePatch
  );

  await controller.runAutonomous("build a login function");

  assert.equal(approvalCalls.length, 1);
  assert.equal(approvalCalls[0].length, 1);
  assert.equal(approvalCalls[0][0].path, "src/auth.py");

  assert.equal(calls[0].method, "pearl/runAutonomous");
  assert.deepEqual(calls[0].params, { prompt: "build a login function" });
});

// ---------------------------------------------------------------------
// Multi-file patch
// ---------------------------------------------------------------------

test("runAutonomous shows every file from a multi-file patch batch in one round", async () => {
  const { sender } = fakeSender({
    "pearl/runAutonomous": () => ({
      stopReason: "awaiting_approval",
      steps: [],
      patches: multiFilePatch(),
    }),
    "pearl/approvePatches": () => ({
      stopReason: "completed",
      steps: [],
      patches: [],
    }),
  });

  const { approvePatch, calls } = fixedPatchApprover("approve");
  const { post } = collectingPost();

  const controller = new ChatController(
    sender,
    post,
    unusedToolApprover,
    unusedPlanApprover,
    approvePatch
  );

  await controller.runAutonomous("build a FastAPI project");

  // All three files arrive together, in a single approval round —
  // never split across multiple approval prompts.
  assert.equal(calls.length, 1);
  assert.equal(calls[0].length, 3);
  assert.deepEqual(
    calls[0].map((f) => f.path),
    ["src/main.py", "src/models.py", "src/routes.py"]
  );
});

// ---------------------------------------------------------------------
// Approval flow
// ---------------------------------------------------------------------

test("approving resumes execution via pearl/approvePatches and reports completion", async () => {
  const { sender, calls } = fakeSender({
    "pearl/runAutonomous": () => ({
      stopReason: "awaiting_approval",
      steps: [
        { tool: "create_file", arguments: {}, succeeded: true, summary: "" },
      ],
      patches: singleFilePatch(),
    }),
    "pearl/approvePatches": () => ({
      stopReason: "completed",
      steps: [
        { tool: "create_file", arguments: {}, succeeded: true, summary: "" },
      ],
      patches: [],
    }),
  });

  const { approvePatch } = fixedPatchApprover("approve");
  const { post, messages, events } = collectingPost();

  const controller = new ChatController(
    sender,
    post,
    unusedToolApprover,
    unusedPlanApprover,
    approvePatch
  );

  await controller.runAutonomous("create a.py");

  assert.deepEqual(
    calls.map((c) => c.method),
    ["pearl/runAutonomous", "pearl/approvePatches"]
  );

  const executionStates = events
    .filter((e): e is Extract<WebviewMessage, { type: "executionState" }> =>
      e.type === "executionState"
    )
    .map((e) => e.state);

  assert.deepEqual(executionStates, [
    "awaiting_approval",
    "applying_patches",
    "resuming",
    "completed",
    null,
  ]);

  const last = strip(messages).at(-1);
  assert.equal(last?.role, "assistant");
  assert.match(last?.text ?? "", /1\/1 step\(s\) completed successfully/);
});

test("a completed run with verification and reflection shows both in the summary", async () => {
  const { sender } = fakeSender({
    "pearl/runAutonomous": () => ({
      stopReason: "awaiting_approval",
      steps: [
        { tool: "create_file", arguments: {}, succeeded: true, summary: "" },
      ],
      patches: singleFilePatch(),
    }),
    "pearl/approvePatches": () => ({
      stopReason: "completed",
      steps: [
        { tool: "create_file", arguments: {}, succeeded: true, summary: "" },
      ],
      patches: [],
      verification: {
        status: "SUCCESS",
        testsRun: 5,
        testsPassed: 5,
        testsFailed: 0,
        changedFiles: ["src/auth.py"],
        unexpectedFiles: [],
      },
      reflection: {
        status: "complete",
        confidence: 0.92,
        reason: "The login function was added and all tests pass.",
        missingRequirements: [],
      },
    }),
  });

  const { approvePatch } = fixedPatchApprover("approve");
  const { post, messages } = collectingPost();

  const controller = new ChatController(
    sender,
    post,
    unusedToolApprover,
    unusedPlanApprover,
    approvePatch
  );

  await controller.runAutonomous("create a.py");

  const last = strip(messages).at(-1);
  assert.equal(last?.role, "assistant");
  assert.match(last?.text ?? "", /Verification:\*\*\s*SUCCESS/);
  assert.match(last?.text ?? "", /5\/5 passed/);
  assert.match(last?.text ?? "", /Reflection:\*\*\s*✓ complete/);
  assert.match(last?.text ?? "", /92% confidence/);
  assert.match(last?.text ?? "", /login function was added/);
});

test("a blocked reflection is shown as unfinished, not dressed up as success", async () => {
  const { sender } = fakeSender({
    "pearl/runAutonomous": () => ({
      stopReason: "awaiting_approval",
      steps: [],
      patches: singleFilePatch(),
    }),
    "pearl/approvePatches": () => ({
      stopReason: "completed",
      steps: [
        { tool: "replace_in_file", arguments: {}, succeeded: true, summary: "" },
      ],
      patches: [],
      verification: {
        status: "FAILED",
        testsRun: 8,
        testsPassed: 6,
        testsFailed: 2,
        changedFiles: ["src/auth.py"],
        unexpectedFiles: ["src/config.py"],
      },
      reflection: {
        status: "blocked",
        confidence: 0.25,
        reason: "Tests still fail and an unrelated file was modified.",
        missingRequirements: ["revert src/config.py", "fix test_auth failures"],
      },
    }),
  });

  const { approvePatch } = fixedPatchApprover("approve");
  const { post, messages } = collectingPost();

  const controller = new ChatController(
    sender,
    post,
    unusedToolApprover,
    unusedPlanApprover,
    approvePatch
  );

  await controller.runAutonomous("fix the bug");

  const last = strip(messages).at(-1);
  // A tool succeeding must not be conflated with the task succeeding —
  // this is the exact failure mode reflection exists to catch.
  assert.match(last?.text ?? "", /Reflection:\*\*\s*✗ blocked/);
  assert.match(last?.text ?? "", /Still needed:.*revert src\/config\.py/);
  assert.match(last?.text ?? "", /Unexpected changes:.*src\/config\.py/);
  assert.doesNotMatch(last?.text ?? "", /✓ complete/);
});

test("a run without verification or reflection shows neither section (never faked)", async () => {
  const { sender } = fakeSender({
    "pearl/runAutonomous": () => ({
      stopReason: "awaiting_approval",
      steps: [],
      patches: singleFilePatch(),
    }),
    "pearl/approvePatches": () => ({
      stopReason: "completed",
      steps: [
        { tool: "create_file", arguments: {}, succeeded: true, summary: "" },
      ],
      patches: [],
      // No verification/reflection fields — mirrors an MCP server run
      // without those components wired in.
    }),
  });

  const { approvePatch } = fixedPatchApprover("approve");
  const { post, messages } = collectingPost();

  const controller = new ChatController(
    sender,
    post,
    unusedToolApprover,
    unusedPlanApprover,
    approvePatch
  );

  await controller.runAutonomous("create a.py");

  const last = strip(messages).at(-1);
  assert.doesNotMatch(last?.text ?? "", /Verification:/);
  assert.doesNotMatch(last?.text ?? "", /Reflection:/);
});

test("a replan count is shown in the completion summary", async () => {
  const { sender } = fakeSender({
    "pearl/runAutonomous": () => ({
      stopReason: "awaiting_approval",
      steps: [],
      patches: singleFilePatch(),
    }),
    "pearl/approvePatches": () => ({
      stopReason: "completed",
      steps: [
        { tool: "create_file", arguments: {}, succeeded: true, summary: "" },
      ],
      patches: [],
      replansUsed: 2,
    }),
  });

  const { approvePatch } = fixedPatchApprover("approve");
  const { post, messages } = collectingPost();

  const controller = new ChatController(
    sender,
    post,
    unusedToolApprover,
    unusedPlanApprover,
    approvePatch
  );

  await controller.runAutonomous("create a.py");

  const last = strip(messages).at(-1);
  assert.match(last?.text ?? "", /Replanned 2 times/);
});

test("approval decision is never sent as a tool call or plan approval", async () => {
  const { sender } = fakeSender({
    "pearl/runAutonomous": () => ({
      stopReason: "awaiting_approval",
      steps: [],
      patches: singleFilePatch(),
    }),
    "pearl/approvePatches": () => ({
      stopReason: "completed",
      steps: [],
      patches: [],
    }),
  });

  const { approvePatch } = fixedPatchApprover("approve");
  const { post } = collectingPost();

  // unusedToolApprover/unusedPlanApprover would throw if called —
  // reaching completion without throwing proves runAutonomous never
  // routes through the tool/plan-approval machinery.
  const controller = new ChatController(
    sender,
    post,
    unusedToolApprover,
    unusedPlanApprover,
    approvePatch
  );

  await controller.runAutonomous("create a.py");
});

// ---------------------------------------------------------------------
// Rejection flow
// ---------------------------------------------------------------------

test("rejecting discards the patch via pearl/rejectPatches and shows a confirmation message", async () => {
  const { sender, calls } = fakeSender({
    "pearl/runAutonomous": () => ({
      stopReason: "awaiting_approval",
      steps: [],
      patches: singleFilePatch(),
    }),
    "pearl/rejectPatches": () => ({
      stopReason: "rejected",
      steps: [],
      patches: [],
    }),
  });

  const { approvePatch } = fixedPatchApprover("reject");
  const { post, messages, events } = collectingPost();

  const controller = new ChatController(
    sender,
    post,
    unusedToolApprover,
    unusedPlanApprover,
    approvePatch
  );

  await controller.runAutonomous("create a.py");

  assert.deepEqual(
    calls.map((c) => c.method),
    ["pearl/runAutonomous", "pearl/rejectPatches"]
  );

  // pearl/approvePatches must never be called on rejection.
  assert.ok(!calls.some((c) => c.method === "pearl/approvePatches"));

  const last = strip(messages).at(-1);
  assert.equal(last?.role, "assistant");
  assert.match(last?.text ?? "", /rejected/i);
  assert.match(last?.text ?? "", /no changes were written/i);

  const executionStates = events
    .filter((e): e is Extract<WebviewMessage, { type: "executionState" }> =>
      e.type === "executionState"
    )
    .map((e) => e.state);

  assert.deepEqual(executionStates, ["awaiting_approval", "cancelled", null]);
});

// ---------------------------------------------------------------------
// Large diff rendering (formatting integration)
// ---------------------------------------------------------------------

test("a large diff is still delivered to the approver in full for rendering", async () => {
  const bigDiffLines = Array.from({ length: 500 }, (_, i) => `+line ${i}`);
  const bigDiff = "--- /dev/null\n+++ b/huge.py\n" + bigDiffLines.join("\n");

  const { sender } = fakeSender({
    "pearl/runAutonomous": () => ({
      stopReason: "awaiting_approval",
      steps: [],
      patches: [{ path: "huge.py", diff: bigDiff, isNewFile: true }],
    }),
    "pearl/approvePatches": () => ({
      stopReason: "completed",
      steps: [],
      patches: [],
    }),
  });

  let receivedDiffLength = 0;

  const approvePatch: PatchApprover = async (files) => {
    receivedDiffLength = files[0].diff.length;
    return "approve";
  };

  const { post } = collectingPost();

  const controller = new ChatController(
    sender,
    post,
    unusedToolApprover,
    unusedPlanApprover,
    approvePatch
  );

  await controller.runAutonomous("generate a huge file");

  // The full diff reaches the approver untruncated; it's the
  // webview's `formatPatchFiles`/`collapsed` flag (tested in
  // patchFormatting.test.ts) that decides how it's *displayed*, not
  // whether the data is delivered.
  assert.equal(receivedDiffLength, bigDiff.length);
});

// ---------------------------------------------------------------------
// Resume after approval: no re-plan, multiple approval rounds
// ---------------------------------------------------------------------

test("resume after approval does not call pearl/runAutonomous again", async () => {
  const { sender, calls } = fakeSender({
    "pearl/runAutonomous": () => ({
      stopReason: "awaiting_approval",
      steps: [
        { tool: "create_file", arguments: {}, succeeded: true, summary: "" },
      ],
      patches: singleFilePatch(),
    }),
    "pearl/approvePatches": () => ({
      stopReason: "completed",
      steps: [
        { tool: "create_file", arguments: {}, succeeded: true, summary: "" },
        { tool: "add", arguments: {}, succeeded: true, summary: "" },
      ],
      patches: [],
    }),
  });

  const { approvePatch } = fixedPatchApprover("approve");
  const { post } = collectingPost();

  const controller = new ChatController(
    sender,
    post,
    unusedToolApprover,
    unusedPlanApprover,
    approvePatch
  );

  await controller.runAutonomous("create a.py then add");

  const runAutonomousCalls = calls.filter(
    (c) => c.method === "pearl/runAutonomous"
  );
  const approveCalls = calls.filter(
    (c) => c.method === "pearl/approvePatches"
  );

  assert.equal(runAutonomousCalls.length, 1);
  assert.equal(approveCalls.length, 1);
});

test("handles multiple sequential approval rounds in one run", async () => {
  let runCall = 0;
  let approveCall = 0;

  const { sender, calls } = fakeSender({
    "pearl/runAutonomous": () => {
      runCall += 1;
      return {
        stopReason: "awaiting_approval",
        steps: [],
        patches: [{ path: "a.py", diff: "+a", isNewFile: true }],
      };
    },
    "pearl/approvePatches": () => {
      approveCall += 1;

      if (approveCall === 1) {
        // A second batch of edits is staged after resuming once.
        return {
          stopReason: "awaiting_approval",
          steps: [],
          patches: [{ path: "b.py", diff: "+b", isNewFile: true }],
        };
      }

      return { stopReason: "completed", steps: [], patches: [] };
    },
  });

  const { approvePatch, calls: approvalCalls } = fixedPatchApprover("approve");
  const { post } = collectingPost();

  const controller = new ChatController(
    sender,
    post,
    unusedToolApprover,
    unusedPlanApprover,
    approvePatch
  );

  await controller.runAutonomous("build two files");

  assert.equal(runCall, 1);
  assert.equal(approveCall, 2);
  assert.equal(approvalCalls.length, 2);
  assert.equal(approvalCalls[0][0].path, "a.py");
  assert.equal(approvalCalls[1][0].path, "b.py");

  assert.deepEqual(
    calls.map((c) => c.method),
    ["pearl/runAutonomous", "pearl/approvePatches", "pearl/approvePatches"]
  );
});

// ---------------------------------------------------------------------
// Misc: no patches staged, transport failures, missing approver
// ---------------------------------------------------------------------

test("a run that completes with no staged patches never invokes the approver", async () => {
  const { sender } = fakeSender({
    "pearl/runAutonomous": () => ({
      stopReason: "completed",
      steps: [{ tool: "pwd", arguments: {}, succeeded: true, summary: "" }],
      patches: [],
    }),
  });

  const approvePatch: PatchApprover = async () => {
    throw new Error("approver should not be called");
  };

  const { post, messages } = collectingPost();

  const controller = new ChatController(
    sender,
    post,
    unusedToolApprover,
    unusedPlanApprover,
    approvePatch
  );

  await controller.runAutonomous("pwd");

  const last = strip(messages).at(-1);
  assert.match(last?.text ?? "", /completed successfully/);
});

test("without a configured patch approver, reports a clear error", async () => {
  const { sender, calls } = fakeSender({});
  const { post, messages } = collectingPost();

  const controller = new ChatController(
    sender,
    post,
    unusedToolApprover,
    unusedPlanApprover
    // approvePatch omitted
  );

  await controller.runAutonomous("create a.py");

  assert.equal(calls.length, 0);
  const last = strip(messages).at(-1);
  assert.equal(last?.role, "error");
  assert.match(last?.text ?? "", /not configured/);
});

test("a transport failure during the initial run is reported as an error", async () => {
  const sender: RequestSender = {
    sendRequest: async () => {
      throw new Error("connection lost");
    },
  };

  const { approvePatch } = fixedPatchApprover("approve");
  const { post, messages } = collectingPost();

  const controller = new ChatController(
    sender,
    post,
    unusedToolApprover,
    unusedPlanApprover,
    approvePatch
  );

  await controller.runAutonomous("create a.py");

  const last = strip(messages).at(-1);
  assert.equal(last?.role, "error");
  assert.match(last?.text ?? "", /connection lost/);
});

test("blank input is ignored", async () => {
  const { sender, calls } = fakeSender({});
  const { approvePatch } = fixedPatchApprover("approve");
  const { post, messages } = collectingPost();

  const controller = new ChatController(
    sender,
    post,
    unusedToolApprover,
    unusedPlanApprover,
    approvePatch
  );

  await controller.runAutonomous("   ");

  assert.equal(calls.length, 0);
  assert.equal(messages.length, 0);
});
