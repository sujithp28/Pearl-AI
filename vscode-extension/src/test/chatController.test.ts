import assert from "node:assert/strict";
import { test } from "node:test";
import { ApprovalDecision, ToolApprovalRequest, ToolApprover } from "../chat/approval";
import {
  ChatController,
  ChatMessage,
  PostToWebview,
  WebviewMessage,
} from "../chat/chatController";
import { PlanApprover, PlanDecision } from "../chat/planApproval";
import { PatchApprover, PatchDecision } from "../chat/patchApproval";
import { RequestSender } from "../mcp/requestSender";
import { PlannedStep } from "../mcp/planClient";
import { PatchFileSummary } from "../mcp/patchClient";

type Handler = (
  params?: Record<string, unknown>
) => unknown | Promise<unknown>;

type NotificationHandler = (params: Record<string, unknown>) => void;

/**
 * A minimal server-side notification bus for tests: `emit` simulates
 * the server pushing a notification while a `sendRequest` handler is
 * still "in flight" (called from inside that handler, before it
 * returns), and `onNotification` is wired into `fakeSender`'s
 * `RequestSender` the same way `MCPConnection.onNotification` really
 * behaves (multiple subscribers, returns an unsubscribe function).
 */
function notificationBus(): {
  onNotification: (method: string, handler: NotificationHandler) => () => void;
  emit: (method: string, params: Record<string, unknown>) => void;
} {
  const handlers = new Map<string, Set<NotificationHandler>>();

  return {
    onNotification: (method, handler) => {
      let set = handlers.get(method);

      if (!set) {
        set = new Set();
        handlers.set(method, set);
      }

      set.add(handler);

      return () => {
        set?.delete(handler);
      };
    },
    emit: (method, params) => {
      const set = handlers.get(method);

      if (!set) {
        return;
      }

      for (const handler of set) {
        handler(params);
      }
    },
  };
}

function fakeSender(
  handlers: Record<string, Handler>,
  notifications?: ReturnType<typeof notificationBus>
): {
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
      onNotification: notifications?.onNotification,
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

/** Strip host-generated `html`/`timestamp` fields for behavior assertions. */
function strip(messages: ChatMessage[]): Array<{ role: string; text: string }> {
  return messages.map((m) => ({ role: m.role, text: m.text }));
}

function fixedApprover(decision: ApprovalDecision): {
  approve: ToolApprover;
  requests: ToolApprovalRequest[];
} {
  const requests: ToolApprovalRequest[] = [];

  return {
    requests,
    approve: async (request) => {
      requests.push(request);
      return decision;
    },
  };
}

function fixedPlanApprover(decision: PlanDecision): {
  approvePlan: PlanApprover;
  calls: PlannedStep[][];
} {
  const calls: PlannedStep[][] = [];

  return {
    calls,
    approvePlan: async (steps) => {
      calls.push(steps);
      return decision;
    },
  };
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

// ---------------------------------------------------------------------
// No tool needed -- falls back to the existing conversational chat,
// and the plan preview / plan approval is never involved.
// ---------------------------------------------------------------------

test("falls back to pearl/chat when the plan needs no tool", async () => {
  const { sender, calls } = fakeSender({
    "pearl/planOnly": () => ({ steps: [{ tool: "none", arguments: {} }] }),
    "pearl/chat": () => ({ message: "Hello there." }),
  });
  const { messages, post } = collectingPost();
  const { approve, requests } = fixedApprover("approved");
  const { approvePlan, calls: planCalls } = fixedPlanApprover("execute");

  const controller = new ChatController(sender, post, approve, approvePlan);
  await controller.handleUserMessage("hi");

  assert.deepEqual(strip(messages), [
    { role: "user", text: "hi" },
    { role: "assistant", text: "Hello there." },
  ]);
  assert.equal(requests.length, 0, "no tool approval should be requested");
  assert.equal(planCalls.length, 0, "no plan approval should be requested");
  assert.deepEqual(
    calls.map((c) => c.method),
    ["pearl/planOnly", "pearl/chat"]
  );
});

test("falls back to pearl/chat when planning itself fails", async () => {
  const { sender } = fakeSender({
    "pearl/planOnly": () => {
      throw new Error("Planning is not enabled on this server.");
    },
    "pearl/chat": () => ({ message: "Plain reply." }),
  });
  const { messages, post } = collectingPost();
  const { approve } = fixedApprover("approved");
  const { approvePlan } = fixedPlanApprover("execute");

  const controller = new ChatController(sender, post, approve, approvePlan);
  await controller.handleUserMessage("hi");

  assert.deepEqual(strip(messages)[1], {
    role: "assistant",
    text: "Plain reply.",
  });
});

// ---------------------------------------------------------------------
// Plan visualization / plan-level Execute vs Cancel
// ---------------------------------------------------------------------

test("shows the full plan for approval before requesting any tool approval", async () => {
  const { sender, calls } = fakeSender({
    "pearl/planOnly": () => ({
      steps: [
        { tool: "read_file", arguments: { path: "a.txt" } },
        { tool: "write_file", arguments: { path: "b.txt", content: "x" } },
      ],
    }),
    "tools/call": () => ({
      content: [{ type: "text", text: "ok" }],
      isError: false,
    }),
  });
  const { post } = collectingPost();
  const { approve, requests: toolRequests } = fixedApprover("approved");

  const planCalls: PlannedStep[][] = [];
  const approvePlan: PlanApprover = async (steps) => {
    planCalls.push(steps);
    // At the moment the plan is shown, no tool approval must have
    // happened yet, and no tools/call must have been sent yet.
    assert.equal(toolRequests.length, 0);
    assert.equal(calls.some((c) => c.method === "tools/call"), false);
    return "execute";
  };

  const controller = new ChatController(sender, post, approve, approvePlan);
  await controller.handleUserMessage("read a.txt then write b.txt");

  assert.equal(planCalls.length, 1);
  assert.deepEqual(planCalls[0], [
    { tool: "read_file", arguments: { path: "a.txt" } },
    { tool: "write_file", arguments: { path: "b.txt", content: "x" } },
  ]);
  // Once "execute" is chosen, the existing per-tool flow proceeds
  // for every step, unchanged.
  assert.equal(toolRequests.length, 2);
  assert.equal(
    calls.filter((c) => c.method === "tools/call").length,
    2
  );
});

test("cancelling the plan performs no tool approvals or tool executions", async () => {
  const { sender, calls } = fakeSender({
    "pearl/planOnly": () => ({
      steps: [{ tool: "delete_file", arguments: { path: "a.txt" } }],
    }),
    "tools/call": () => {
      throw new Error("tools/call should never be called after cancel");
    },
  });
  const { messages, post } = collectingPost();
  const { approve, requests: toolRequests } = fixedApprover("approved");
  const { approvePlan, calls: planCalls } = fixedPlanApprover("cancel");

  const controller = new ChatController(sender, post, approve, approvePlan);
  await controller.handleUserMessage("delete a.txt");

  assert.equal(planCalls.length, 1);
  assert.equal(toolRequests.length, 0, "no tool approval should be requested");
  assert.equal(
    calls.some((c) => c.method === "tools/call"),
    false,
    "tools/call must never be sent after cancelling the plan"
  );
  assert.deepEqual(strip(messages)[1], {
    role: "assistant",
    text: "Plan cancelled. No changes were made.",
  });
});

test("a single-step plan still goes through the plan preview gate", async () => {
  const { sender } = fakeSender({
    "pearl/planOnly": () => ({
      steps: [{ tool: "read_file", arguments: { path: "a.txt" } }],
    }),
  });
  const { messages, post } = collectingPost();
  const { approve } = fixedApprover("approved");
  const { approvePlan, calls: planCalls } = fixedPlanApprover("cancel");

  const controller = new ChatController(sender, post, approve, approvePlan);
  await controller.handleUserMessage("read a.txt");

  assert.equal(planCalls.length, 1);
  assert.deepEqual(strip(messages)[1], {
    role: "assistant",
    text: "Plan cancelled. No changes were made.",
  });
});

// ---------------------------------------------------------------------
// Tool approval (unchanged behavior once the plan is executed)
// ---------------------------------------------------------------------

test("requests approval with the tool name and arguments before calling tools/call", async () => {
  const { sender, calls } = fakeSender({
    "pearl/planOnly": () => ({
      steps: [{ tool: "read_file", arguments: { path: "a.txt" } }],
    }),
    "tools/call": () => ({
      content: [{ type: "text", text: "file contents" }],
      isError: false,
    }),
  });
  const { messages, post } = collectingPost();
  const { approve, requests } = fixedApprover("approved");
  const { approvePlan } = fixedPlanApprover("execute");

  const controller = new ChatController(sender, post, approve, approvePlan);
  await controller.handleUserMessage("read a.txt");

  assert.deepEqual(requests, [
    { tool: "read_file", arguments: { path: "a.txt" } },
  ]);

  const toolCallCall = calls.find((c) => c.method === "tools/call");
  assert.deepEqual(toolCallCall?.params, {
    name: "read_file",
    arguments: { path: "a.txt" },
  });

  assert.deepEqual(strip(messages)[1], {
    role: "assistant",
    text: 'Ran "read_file":\nfile contents',
  });
});

test("rejecting a tool cancels execution cleanly and reports it to chat", async () => {
  const { sender, calls } = fakeSender({
    "pearl/planOnly": () => ({
      steps: [{ tool: "delete_file", arguments: { path: "a.txt" } }],
    }),
    "tools/call": () => {
      throw new Error("tools/call should never be called when rejected");
    },
  });
  const { messages, post } = collectingPost();
  const { approve, requests } = fixedApprover("rejected");
  const { approvePlan } = fixedPlanApprover("execute");

  const controller = new ChatController(sender, post, approve, approvePlan);
  await controller.handleUserMessage("delete a.txt");

  assert.equal(requests.length, 1);
  assert.equal(
    calls.some((c) => c.method === "tools/call"),
    false,
    "tools/call must not be sent for a rejected tool"
  );

  assert.deepEqual(strip(messages)[1], {
    role: "assistant",
    text: 'Tool "delete_file" was rejected. No changes were made.',
  });
});

test("a rejected step stops any remaining steps in the plan", async () => {
  const { sender, calls } = fakeSender({
    "pearl/planOnly": () => ({
      steps: [
        { tool: "read_file", arguments: { path: "a.txt" } },
        { tool: "delete_file", arguments: { path: "a.txt" } },
        { tool: "write_file", arguments: { path: "b.txt", content: "x" } },
      ],
    }),
    "tools/call": (params) => {
      if (params?.name === "read_file") {
        return { content: [{ type: "text", text: "ok" }], isError: false };
      }
      throw new Error("should not reach a second tools/call");
    },
  });
  const { messages, post } = collectingPost();
  const { approvePlan } = fixedPlanApprover("execute");

  let call = 0;
  const requests: ToolApprovalRequest[] = [];
  const approve: ToolApprover = async (request) => {
    requests.push(request);
    call += 1;
    return call === 1 ? "approved" : "rejected";
  };

  const controller = new ChatController(sender, post, approve, approvePlan);
  await controller.handleUserMessage("do three things");

  assert.equal(requests.length, 2, "should stop requesting after the rejection");
  assert.deepEqual(
    requests.map((r) => r.tool),
    ["read_file", "delete_file"]
  );
  assert.equal(
    calls.filter((c) => c.method === "tools/call").length,
    1,
    "only the approved step should have been executed"
  );
  assert.equal(messages.length, 3); // user + read_file result + rejection notice
  assert.match(messages[2].text, /"delete_file" was rejected/);
});

test("a failed tool execution stops the remaining plan", async () => {
  const { sender, calls } = fakeSender({
    "pearl/planOnly": () => ({
      steps: [
        { tool: "boom", arguments: {} },
        { tool: "read_file", arguments: { path: "a.txt" } },
      ],
    }),
    "tools/call": () => ({
      content: [{ type: "text", text: "kaboom" }],
      isError: true,
    }),
  });
  const { messages, post } = collectingPost();
  const { approve } = fixedApprover("approved");
  const { approvePlan } = fixedPlanApprover("execute");

  const controller = new ChatController(sender, post, approve, approvePlan);
  await controller.handleUserMessage("do something that fails");

  assert.equal(messages[1].role, "error");
  assert.match(messages[1].text, /kaboom/);
  assert.equal(
    calls.filter((c) => c.method === "tools/call").length,
    1,
    "should not attempt the second step after a tool failure"
  );
});

test("a transport-level failure calling tools/call is reported as an error", async () => {
  const { sender } = fakeSender({
    "pearl/planOnly": () => ({
      steps: [{ tool: "read_file", arguments: { path: "a.txt" } }],
    }),
    "tools/call": () => {
      throw new Error("Not connected to Pearl MCP server.");
    },
  });
  const { messages, post } = collectingPost();
  const { approve } = fixedApprover("approved");
  const { approvePlan } = fixedPlanApprover("execute");

  const controller = new ChatController(sender, post, approve, approvePlan);
  await controller.handleUserMessage("read a.txt");

  assert.equal(messages[1].role, "error");
  assert.match(messages[1].text, /Not connected to Pearl MCP server\./);
});

// ---------------------------------------------------------------------
// Input handling
// ---------------------------------------------------------------------

test("ignores blank input", async () => {
  const { sender, calls } = fakeSender({});
  const { messages, post } = collectingPost();
  const { approve } = fixedApprover("approved");
  const { approvePlan } = fixedPlanApprover("execute");

  const controller = new ChatController(sender, post, approve, approvePlan);
  await controller.handleUserMessage("   ");

  assert.deepEqual(messages, []);
  assert.equal(calls.length, 0);
});

test("trims whitespace from the user message", async () => {
  const { sender } = fakeSender({
    "pearl/planOnly": () => ({ steps: [{ tool: "none", arguments: {} }] }),
    "pearl/chat": () => ({ message: "ok" }),
  });
  const { messages, post } = collectingPost();
  const { approve } = fixedApprover("approved");
  const { approvePlan } = fixedPlanApprover("execute");

  const controller = new ChatController(sender, post, approve, approvePlan);
  await controller.handleUserMessage("  hi there  ");

  assert.equal(messages[0].text, "hi there");
});

// ---------------------------------------------------------------------
// Message metadata (timestamp / rendered html)
// ---------------------------------------------------------------------

test("every posted message carries an ISO timestamp and rendered html", async () => {
  const { sender } = fakeSender({
    "pearl/planOnly": () => ({ steps: [{ tool: "none", arguments: {} }] }),
    "pearl/chat": () => ({ message: "**bold** reply" }),
  });
  const { messages, post } = collectingPost();
  const { approve } = fixedApprover("approved");
  const { approvePlan } = fixedPlanApprover("execute");

  const controller = new ChatController(sender, post, approve, approvePlan);
  await controller.handleUserMessage("hi");

  for (const message of messages) {
    assert.match(
      message.timestamp,
      /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/
    );
    assert.equal(typeof message.html, "string");
    assert.ok(message.html.length > 0);
  }

  assert.match(messages[1].html, /<strong>bold<\/strong>/);
});

// ---------------------------------------------------------------------
// Loading indicator / timeline events
// ---------------------------------------------------------------------

test("loading is shown while waiting and hidden once the conversational reply arrives", async () => {
  const { sender } = fakeSender({
    "pearl/planOnly": () => ({ steps: [{ tool: "none", arguments: {} }] }),
    "pearl/chat": () => ({ message: "hi" }),
  });
  const { events, post } = collectingPost();
  const { approve } = fixedApprover("approved");
  const { approvePlan } = fixedPlanApprover("execute");

  const controller = new ChatController(sender, post, approve, approvePlan);
  await controller.handleUserMessage("hi");

  const loadingEvents = events.filter((e) => e.type === "loading");
  assert.deepEqual(
    loadingEvents.map((e) => (e as { show: boolean }).show),
    [true, false]
  );
});

test("timeline progresses through planning, plan_ready, and waiting_approval before a plan decision", async () => {
  const { sender } = fakeSender({
    "pearl/planOnly": () => ({
      steps: [{ tool: "read_file", arguments: { path: "a.txt" } }],
    }),
  });
  const { events, post } = collectingPost();
  const { approve } = fixedApprover("approved");
  const { approvePlan } = fixedPlanApprover("cancel");

  const controller = new ChatController(sender, post, approve, approvePlan);
  await controller.handleUserMessage("read a.txt");

  const timelineStages = events
    .filter((e) => e.type === "timeline")
    .map((e) => (e as { stage: string | null }).stage);

  assert.deepEqual(timelineStages, [
    "planning",
    "plan_ready",
    "waiting_approval",
    null, // cleared after cancel
  ]);
});

test("timeline reaches 'completed' after every step in an executed plan succeeds", async () => {
  const { sender } = fakeSender({
    "pearl/planOnly": () => ({
      steps: [{ tool: "read_file", arguments: { path: "a.txt" } }],
    }),
    "tools/call": () => ({
      content: [{ type: "text", text: "ok" }],
      isError: false,
    }),
  });
  const { events, post } = collectingPost();
  const { approve } = fixedApprover("approved");
  const { approvePlan } = fixedPlanApprover("execute");

  const controller = new ChatController(sender, post, approve, approvePlan);
  await controller.handleUserMessage("read a.txt");

  const timelineStages = events
    .filter((e) => e.type === "timeline")
    .map((e) => (e as { stage: string | null }).stage);

  assert.deepEqual(timelineStages, [
    "planning",
    "plan_ready",
    "waiting_approval",
    "waiting_approval",
    "running_tool",
    "completed",
  ]);
});

// ---------------------------------------------------------------------
// runAutonomous: live pearl/progress streaming
// ---------------------------------------------------------------------

function progressEvents(events: WebviewMessage[]): unknown[] {
  return events
    .filter((e) => e.type === "progress")
    .map((e) => (e as { event: unknown }).event);
}

test("runAutonomous posts each streamed progress event, in order", async () => {
  const bus = notificationBus();
  const { sender } = fakeSender(
    {
      "pearl/runAutonomous": () => {
        bus.emit("pearl/progress", {
          status: "planning",
          currentStep: 0,
          totalSteps: 0,
          currentAction: "Plotting...",
        });
        bus.emit("pearl/progress", {
          status: "executing_step",
          currentStep: 1,
          totalSteps: 1,
          currentAction: "Running...",
        });
        bus.emit("pearl/progress", {
          status: "task_completed",
          currentStep: 1,
          totalSteps: 1,
          currentAction: "Done",
        });
        return { stopReason: "completed", steps: [], patches: [], replansUsed: 0 };
      },
    },
    bus
  );
  const { events, post } = collectingPost();
  const { approve } = fixedApprover("approved");
  const { approvePlan } = fixedPlanApprover("execute");
  const { approvePatch } = fixedPatchApprover("approve");

  const controller = new ChatController(sender, post, approve, approvePlan, approvePatch);
  await controller.runAutonomous("do the thing");

  assert.deepEqual(progressEvents(events), [
    { status: "planning", currentStep: 0, totalSteps: 0, currentAction: "Plotting..." },
    {
      status: "executing_step",
      currentStep: 1,
      totalSteps: 1,
      currentAction: "Running...",
    },
    { status: "task_completed", currentStep: 1, totalSteps: 1, currentAction: "Done" },
    null, // cleared once the run finishes
  ]);
});

test("runAutonomous clears progress even when the request fails", async () => {
  const bus = notificationBus();
  const { sender } = fakeSender(
    {
      "pearl/runAutonomous": () => {
        bus.emit("pearl/progress", {
          status: "planning",
          currentStep: 0,
          totalSteps: 0,
          currentAction: "Plotting...",
        });
        throw new Error("connection lost");
      },
    },
    bus
  );
  const { events, post } = collectingPost();
  const { approve } = fixedApprover("approved");
  const { approvePlan } = fixedPlanApprover("execute");
  const { approvePatch } = fixedPatchApprover("approve");

  const controller = new ChatController(sender, post, approve, approvePlan, approvePatch);
  await controller.runAutonomous("do the thing");

  const streamed = progressEvents(events);
  assert.equal(streamed[streamed.length - 1], null);
  assert.ok(
    events.some(
      (e) => e.type === "addMessage" && e.message.role === "error"
    )
  );
});

test("runAutonomous keeps streaming progress across an approve() round trip", async () => {
  const bus = notificationBus();
  const target: PatchFileSummary = { path: "a.py", diff: "+1", isNewFile: true };

  const { sender } = fakeSender(
    {
      "pearl/runAutonomous": () => {
        bus.emit("pearl/progress", {
          status: "awaiting_approval",
          currentStep: 1,
          totalSteps: 1,
          currentAction: "Patch ready",
        });
        return {
          stopReason: "awaiting_approval",
          steps: [],
          patches: [target],
          replansUsed: 0,
        };
      },
      "pearl/approvePatches": () => {
        bus.emit("pearl/progress", {
          status: "task_completed",
          currentStep: 1,
          totalSteps: 1,
          currentAction: "Done",
        });
        return { stopReason: "completed", steps: [], patches: [], replansUsed: 0 };
      },
    },
    bus
  );
  const { events, post } = collectingPost();
  const { approve } = fixedApprover("approved");
  const { approvePlan } = fixedPlanApprover("execute");
  const { approvePatch } = fixedPatchApprover("approve");

  const controller = new ChatController(sender, post, approve, approvePlan, approvePatch);
  await controller.runAutonomous("create a.py");

  assert.deepEqual(
    progressEvents(events).map((e) => (e as { status: string } | null)?.status ?? null),
    ["awaiting_approval", "task_completed", null]
  );
});

test("a run's progress subscription does not receive a later, unrelated run's events", async () => {
  const bus = notificationBus();
  const secondRun: { emit: (() => void) | null } = { emit: null };

  const { sender } = fakeSender(
    {
      "pearl/runAutonomous": () => {
        // First call: register what the *second* run will later emit,
        // but don't emit it yet — proves a stale subscription from
        // this first run would otherwise pick it up.
        secondRun.emit = () =>
          bus.emit("pearl/progress", {
            status: "planning",
            currentStep: 0,
            totalSteps: 0,
            currentAction: "second run",
          });
        return { stopReason: "completed", steps: [], patches: [], replansUsed: 0 };
      },
    },
    bus
  );
  const { events, post } = collectingPost();
  const { approve } = fixedApprover("approved");
  const { approvePlan } = fixedPlanApprover("execute");
  const { approvePatch } = fixedPatchApprover("approve");

  const controller = new ChatController(sender, post, approve, approvePlan, approvePatch);
  await controller.runAutonomous("first run");

  events.length = 0; // only care about what happens after this point
  secondRun.emit?.();

  assert.deepEqual(progressEvents(events), []);
});

test("a malformed progress payload is silently dropped, not posted", async () => {
  const bus = notificationBus();
  const { sender } = fakeSender(
    {
      "pearl/runAutonomous": () => {
        bus.emit("pearl/progress", { status: "not-a-real-status" });
        return { stopReason: "completed", steps: [], patches: [], replansUsed: 0 };
      },
    },
    bus
  );
  const { events, post } = collectingPost();
  const { approve } = fixedApprover("approved");
  const { approvePlan } = fixedPlanApprover("execute");
  const { approvePatch } = fixedPatchApprover("approve");

  const controller = new ChatController(sender, post, approve, approvePlan, approvePatch);
  await controller.runAutonomous("do the thing");

  // Only the final "clear" (null) is posted — the malformed event
  // itself never reaches the webview.
  assert.deepEqual(progressEvents(events), [null]);
});

test("runAutonomous works when the sender does not implement onNotification", async () => {
  // A fake sender that only satisfies the required part of
  // RequestSender — confirms progress streaming is fully optional
  // and never breaks a caller that hasn't wired it up.
  const { sender } = fakeSender({
    "pearl/runAutonomous": () => ({
      stopReason: "completed",
      steps: [],
      patches: [],
      replansUsed: 0,
    }),
  });
  const { events, post } = collectingPost();
  const { approve } = fixedApprover("approved");
  const { approvePlan } = fixedPlanApprover("execute");
  const { approvePatch } = fixedPatchApprover("approve");

  const controller = new ChatController(sender, post, approve, approvePlan, approvePatch);
  await controller.runAutonomous("do the thing");

  assert.deepEqual(progressEvents(events), [null]);
});
