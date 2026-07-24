/**
 * Integration test: the real `MCPConnection` (talking JSON-RPC over
 * a fake child process, exactly as it would over a real one) driven
 * by the real `ChatController` and `WebviewPlanApprover` — the full
 * "show the plan, then Execute Plan / Cancel" pipeline, everything
 * except the actual `vscode.Webview` and a real Python process.
 */

import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { test } from "node:test";
import {
  ChatController,
  ChatMessage,
  PostToWebview,
} from "../chat/chatController";
import { WebviewPlanApprover } from "../chat/planApproval";
import { ConnectionStatus, MCPConnection } from "../mcp/connection";

class FakeChildProcess extends EventEmitter {
  public written: string[] = [];
  public stdout = new EventEmitter();
  public stderr = new EventEmitter();
  public stdin = {
    write: (chunk: string): boolean => {
      this.written.push(chunk);
      return true;
    },
  };

  kill(): void {
    // No-op: nothing observes this in the integration test.
  }

  respond(id: number, result: unknown): void {
    this.stdout.emit(
      "data",
      JSON.stringify({ jsonrpc: "2.0", id, result }) + "\n"
    );
  }

  lastRequest(): { id: number; method: string; params?: Record<string, unknown> } {
    return JSON.parse(this.written[this.written.length - 1]);
  }
}

function waitForStatus(
  connection: MCPConnection,
  target: ConnectionStatus,
  timeoutMs = 1000
): Promise<void> {
  if (connection.getStatus() === target) {
    return Promise.resolve();
  }

  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error(`Timed out waiting for status '${target}'.`));
    }, timeoutMs);

    const previous = connection.onStatusChange;
    connection.onStatusChange = (status, detail) => {
      previous?.(status, detail);
      if (status === target) {
        clearTimeout(timer);
        connection.onStatusChange = previous;
        resolve();
      }
    };
  });
}

async function connectFakeServer(): Promise<{
  connection: MCPConnection;
  child: FakeChildProcess;
}> {
  let child!: FakeChildProcess;

  const connection = new MCPConnection({
    command: "python3",
    args: ["-m", "src.mcp"],
    spawnFn: () => {
      child = new FakeChildProcess();
      return child;
    },
  });

  connection.start();

  await Promise.resolve();
  const init = child.lastRequest();
  assert.equal(init.method, "initialize");
  child.respond(init.id, { protocolVersion: "2024-11-05" });

  await waitForStatus(connection, "connected");

  return { connection, child };
}

function collectingPost<T extends { type: string }>(): {
  messages: T[];
  post: (m: T) => void;
} {
  const messages: T[] = [];
  return { messages, post: (m) => messages.push(m) };
}

function collectingChatPost(): {
  messages: ChatMessage[];
  post: PostToWebview;
} {
  const messages: ChatMessage[] = [];
  return {
    messages,
    post: (m) => {
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

const tick = () => new Promise((resolve) => setImmediate(resolve));

test("integration: the plan is shown to the webview before any tool approval, and Execute Plan runs it", async () => {
  const { connection, child } = await connectFakeServer();
  const { messages: chatMessages, post: postChat } = collectingChatPost();
  const { messages: webviewMessages, post: postWebview } = collectingPost<{
    type: "showPlan";
    steps: unknown[];
  }>();

  const planApprover = new WebviewPlanApprover(postWebview);
  const toolApprovalRequests: unknown[] = [];

  const controller = new ChatController(
    connection,
    postChat,
    async (request) => {
      toolApprovalRequests.push(request);
      return "approved";
    },
    planApprover.requestApproval
  );

  const handling = controller.handleUserMessage("read a.txt");

  await tick();
  const planRequest = child.lastRequest();
  assert.equal(planRequest.method, "pearl/planOnly");

  child.respond(planRequest.id, {
    steps: [{ tool: "read_file", arguments: { path: "a.txt" } }],
  });

  // The plan preview must reach the webview before any tool
  // approval is requested and before tools/call is ever sent.
  await tick();
  await tick();

  assert.equal(webviewMessages.length, 1);
  assert.equal(webviewMessages[0].type, "showPlan");
  assert.deepEqual(webviewMessages[0].steps, [
    {
      index: 1,
      tool: "read_file",
      argumentsText: JSON.stringify({ path: "a.txt" }, null, 2),
      collapsed: false,
    },
  ]);
  assert.equal(toolApprovalRequests.length, 0);
  assert.equal(
    child.written.some((line) => JSON.parse(line).method === "tools/call"),
    false
  );

  // Simulate the webview posting "Execute Plan" back.
  planApprover.resolveDecision("execute");

  await tick();
  await tick();

  const toolCallRequest = child.lastRequest();
  assert.equal(toolCallRequest.method, "tools/call");
  assert.deepEqual(toolCallRequest.params, {
    name: "read_file",
    arguments: { path: "a.txt" },
  });

  child.respond(toolCallRequest.id, {
    content: [{ type: "text", text: "file contents" }],
    isError: false,
  });

  await handling;

  assert.equal(toolApprovalRequests.length, 1);
  assert.deepEqual(strip(chatMessages), [
    { role: "user", text: "read a.txt" },
    { role: "assistant", text: 'Ran "read_file":\nfile contents' },
  ]);

  connection.stop();
});

test("integration: Cancel from the plan preview performs no tool approvals or tool calls", async () => {
  const { connection, child } = await connectFakeServer();
  const { messages: chatMessages, post: postChat } = collectingChatPost();
  const { post: postWebview } = collectingPost<{
    type: "showPlan";
    steps: unknown[];
  }>();

  const planApprover = new WebviewPlanApprover(postWebview);
  const toolApprovalRequests: unknown[] = [];

  const controller = new ChatController(
    connection,
    postChat,
    async (request) => {
      toolApprovalRequests.push(request);
      return "approved";
    },
    planApprover.requestApproval
  );

  const handling = controller.handleUserMessage("delete a.txt and b.txt");

  await tick();
  const planRequest = child.lastRequest();
  child.respond(planRequest.id, {
    steps: [
      { tool: "delete_file", arguments: { path: "a.txt" } },
      { tool: "delete_file", arguments: { path: "b.txt" } },
    ],
  });

  await tick();
  await tick();

  // Simulate the webview posting "Cancel" back.
  planApprover.resolveDecision("cancel");

  await handling;

  assert.equal(toolApprovalRequests.length, 0);
  assert.equal(
    child.written.some((line) => JSON.parse(line).method === "tools/call"),
    false,
    "tools/call must never be sent after cancelling the plan"
  );

  assert.deepEqual(strip(chatMessages), [
    { role: "user", text: "delete a.txt and b.txt" },
    {
      role: "assistant",
      text: "Plan cancelled. No changes were made.",
    },
  ]);

  connection.stop();
});
