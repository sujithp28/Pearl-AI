/**
 * Integration test: the real `MCPConnection` (talking JSON-RPC over
 * a fake child process, exactly as it would over a real one) driven
 * by the real `ChatController`, `planClient`, and `toolCallClient` —
 * everything in the tool-approval pipeline except the actual
 * `vscode.Webview` and a real Python process.
 */

import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { test } from "node:test";
import { ApprovalDecision, ToolApprovalRequest } from "../chat/approval";
import {
  ChatController,
  ChatMessage,
  PostToWebview,
} from "../chat/chatController";
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

  // Respond to the automatic `initialize` handshake.
  await Promise.resolve();
  const init = child.lastRequest();
  assert.equal(init.method, "initialize");
  child.respond(init.id, { protocolVersion: "2024-11-05" });

  await waitForStatus(connection, "connected");

  return { connection, child };
}

function collectingPost(): {
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

test("integration: approving a planned tool call sends tools/call over the real MCPConnection", async () => {
  const { connection, child } = await connectFakeServer();
  const { messages, post } = collectingPost();

  const approvalRequests: ToolApprovalRequest[] = [];
  const decision: ApprovalDecision = "approved";

  const controller = new ChatController(
    connection,
    post,
    async (request) => {
      approvalRequests.push(request);
      return decision;
    },
    async () => "execute"
  );

  const handling = controller.handleUserMessage("read a.txt please");

  // The controller should have sent pearl/planOnly first.
  await new Promise((resolve) => setImmediate(resolve));
  const planRequest = child.lastRequest();
  assert.equal(planRequest.method, "pearl/planOnly");
  assert.deepEqual(planRequest.params, { prompt: "read a.txt please" });

  child.respond(planRequest.id, {
    steps: [{ tool: "read_file", arguments: { path: "a.txt" } }],
  });

  // Wait for the approval-gated tools/call to go out.
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  const toolCallRequest = child.lastRequest();
  assert.equal(toolCallRequest.method, "tools/call");
  assert.deepEqual(toolCallRequest.params, {
    name: "read_file",
    arguments: { path: "a.txt" },
  });

  child.respond(toolCallRequest.id, {
    content: [{ type: "text", text: "hello world" }],
    isError: false,
  });

  await handling;

  assert.deepEqual(approvalRequests, [
    { tool: "read_file", arguments: { path: "a.txt" } },
  ]);
  assert.deepEqual(strip(messages), [
    { role: "user", text: "read a.txt please" },
    { role: "assistant", text: 'Ran "read_file":\nhello world' },
  ]);

  connection.stop();
});

test("integration: rejecting a planned tool call never sends tools/call", async () => {
  const { connection, child } = await connectFakeServer();
  const { messages, post } = collectingPost();

  const controller = new ChatController(
    connection,
    post,
    async () => "rejected",
    async () => "execute"
  );

  const handling = controller.handleUserMessage("delete a.txt");

  await new Promise((resolve) => setImmediate(resolve));
  const planRequest = child.lastRequest();
  assert.equal(planRequest.method, "pearl/planOnly");

  child.respond(planRequest.id, {
    steps: [{ tool: "delete_file", arguments: { path: "a.txt" } }],
  });

  await handling;

  assert.equal(
    child.written.filter((line) => JSON.parse(line).method === "tools/call")
      .length,
    0,
    "tools/call must never be sent for a rejected tool"
  );

  assert.deepEqual(strip(messages), [
    { role: "user", text: "delete a.txt" },
    {
      role: "assistant",
      text: 'Tool "delete_file" was rejected. No changes were made.',
    },
  ]);

  connection.stop();
});

test("integration: a plan needing no tool falls back to pearl/chat", async () => {
  const { connection, child } = await connectFakeServer();
  const { messages, post } = collectingPost();

  const controller = new ChatController(
    connection,
    post,
    async () => "approved",
    async () => "execute"
  );

  const handling = controller.handleUserMessage("just say hi");

  await new Promise((resolve) => setImmediate(resolve));
  const planRequest = child.lastRequest();
  child.respond(planRequest.id, {
    steps: [{ tool: "none", arguments: {} }],
  });

  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  const chatRequest = child.lastRequest();
  assert.equal(chatRequest.method, "pearl/chat");
  child.respond(chatRequest.id, { message: "Hi! How can I help?" });

  await handling;

  assert.deepEqual(strip(messages), [
    { role: "user", text: "just say hi" },
    { role: "assistant", text: "Hi! How can I help?" },
  ]);

  connection.stop();
});
