/**
 * Integration test: the real `MCPConnection` (talking JSON-RPC over
 * a fake child process, exactly as it would over a real one) driven
 * by the real `ChatController.runAutonomous` and the real
 * `WebviewPatchApprover` — the full "run autonomously, preview the
 * patch batch, approve/reject, resume" pipeline, everything except
 * the actual `vscode.Webview` and a real Python process.
 *
 * The fake server's responses mirror exactly what
 * `src/mcp/server.py`'s `pearl/runAutonomous` / `pearl/approvePatches`
 * / `pearl/rejectPatches` handlers produce (camelCase
 * `stopReason`/`isNewFile`/`replansUsed`, see `_execution_report_to_dict`
 * in `server.py`), so this validates the two sides of the MCP
 * ↔ VS Code contract agree, not just that the TS code compiles
 * against its own assumed shape.
 */

import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { test } from "node:test";
import {
  ChatController,
  ChatMessage,
  PostToWebview,
  WebviewMessage,
} from "../chat/chatController";
import { ConnectionStatus, MCPConnection } from "../mcp/connection";
import { WebviewPatchApprover } from "../chat/patchApproval";

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

  requestsFor(method: string): Array<{ id: number; method: string; params?: Record<string, unknown> }> {
    return this.written
      .map((line) => JSON.parse(line))
      .filter((req) => req.method === method);
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
  child.respond(init.id, {
    protocolVersion: "2024-11-05",
    capabilities: { experimental: { pearlAutonomous: {} } },
  });

  await waitForStatus(connection, "connected");

  return { connection, child };
}

function collectingChatPost(): {
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

const tick = () => new Promise((resolve) => setImmediate(resolve));

test("integration: complete autonomous workflow — patch shown, approved, resumed to completion", async () => {
  const { connection, child } = await connectFakeServer();
  const { messages: chatMessages, post: postChat } = collectingChatPost();

  const shownBatches: unknown[] = [];
  const patchApprover = new WebviewPatchApprover((message) => {
    shownBatches.push(message);
  });

  const controller = new ChatController(
    connection,
    postChat,
    async () => {
      throw new Error("ToolApprover should not be used by runAutonomous.");
    },
    async () => {
      throw new Error("PlanApprover should not be used by runAutonomous.");
    },
    patchApprover.requestApproval
  );

  const running = controller.runAutonomous("create a login function");

  await tick();
  const runRequest = child.lastRequest();
  assert.equal(runRequest.method, "pearl/runAutonomous");
  assert.deepEqual(runRequest.params, { prompt: "create a login function" });

  child.respond(runRequest.id, {
    stopReason: "awaiting_approval",
    steps: [
      {
        tool: "create_file",
        arguments: { path: "src/auth.py" },
        succeeded: true,
        summary: "'create_file' succeeded",
      },
    ],
    patches: [
      {
        path: "src/auth.py",
        diff: "--- /dev/null\n+++ b/src/auth.py\n@@ -0,0 +1,2 @@\n+def login():\n+    return True",
        isNewFile: true,
      },
    ],
    replansUsed: 0,
  });

  await tick();
  await tick();

  // The patch batch must reach the webview before anything is
  // written and before a resume request is ever sent.
  assert.equal(shownBatches.length, 1);
  assert.equal(
    child.requestsFor("pearl/approvePatches").length,
    0,
    "must not resume before the user decides"
  );

  // Simulate the webview posting "Approve" back.
  patchApprover.resolveDecision("approve");

  await tick();
  const approveRequest = child.lastRequest();
  assert.equal(approveRequest.method, "pearl/approvePatches");
  assert.deepEqual(approveRequest.params, {});

  child.respond(approveRequest.id, {
    stopReason: "completed",
    steps: [
      {
        tool: "create_file",
        arguments: { path: "src/auth.py" },
        succeeded: true,
        summary: "'create_file' succeeded",
      },
    ],
    patches: [],
    replansUsed: 0,
  });

  await running;

  // pearl/runAutonomous must only ever have been sent once — resume
  // never re-plans.
  assert.equal(child.requestsFor("pearl/runAutonomous").length, 1);
  assert.equal(child.requestsFor("pearl/approvePatches").length, 1);

  const last = strip(chatMessages).at(-1);
  assert.equal(last?.role, "assistant");
  assert.match(last?.text ?? "", /completed successfully/);

  connection.stop();
});

test("integration: rejecting a patch batch discards it and never calls pearl/approvePatches", async () => {
  const { connection, child } = await connectFakeServer();
  const { messages: chatMessages, post: postChat } = collectingChatPost();

  const patchApprover = new WebviewPatchApprover(() => {});

  const controller = new ChatController(
    connection,
    postChat,
    async () => {
      throw new Error("ToolApprover should not be used by runAutonomous.");
    },
    async () => {
      throw new Error("PlanApprover should not be used by runAutonomous.");
    },
    patchApprover.requestApproval
  );

  const running = controller.runAutonomous("create scratch.py");

  await tick();
  const runRequest = child.lastRequest();

  child.respond(runRequest.id, {
    stopReason: "awaiting_approval",
    steps: [],
    patches: [
      { path: "scratch.py", diff: "+x = 1", isNewFile: true },
    ],
    replansUsed: 0,
  });

  await tick();
  await tick();

  patchApprover.resolveDecision("reject");

  await tick();
  const rejectRequest = child.lastRequest();
  assert.equal(rejectRequest.method, "pearl/rejectPatches");

  child.respond(rejectRequest.id, {
    stopReason: "rejected",
    steps: [],
    patches: [],
    replansUsed: 0,
  });

  await running;

  assert.equal(child.requestsFor("pearl/approvePatches").length, 0);

  const last = strip(chatMessages).at(-1);
  assert.equal(last?.role, "assistant");
  assert.match(last?.text ?? "", /rejected/i);

  connection.stop();
});

test("integration: a multi-file patch batch arrives as a single showPatchBatch message", async () => {
  const { connection, child } = await connectFakeServer();
  const { post: postChat } = collectingChatPost();

  const shownBatches: Array<{ files: unknown[] }> = [];
  const patchApprover = new WebviewPatchApprover((message) => {
    shownBatches.push(message as { files: unknown[] });
  });

  const controller = new ChatController(
    connection,
    postChat,
    async () => "approved" as const,
    async () => "execute" as const,
    patchApprover.requestApproval
  );

  const running = controller.runAutonomous("scaffold a FastAPI project");

  await tick();
  const runRequest = child.lastRequest();

  child.respond(runRequest.id, {
    stopReason: "awaiting_approval",
    steps: [],
    patches: [
      { path: "src/main.py", diff: "+app = 1", isNewFile: true },
      { path: "src/models.py", diff: "+class User: pass", isNewFile: true },
      { path: "src/routes.py", diff: "+routes = []", isNewFile: true },
    ],
    replansUsed: 0,
  });

  await tick();
  await tick();

  assert.equal(shownBatches.length, 1);
  assert.equal(shownBatches[0].files.length, 3);

  patchApprover.resolveDecision("approve");
  await tick();
  const approveRequest = child.lastRequest();
  child.respond(approveRequest.id, {
    stopReason: "completed",
    steps: [],
    patches: [],
    replansUsed: 0,
  });

  await running;
  connection.stop();
});
