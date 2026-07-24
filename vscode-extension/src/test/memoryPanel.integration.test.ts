/**
 * Integration test: the real `MCPConnection` (talking JSON-RPC over
 * a fake child process, exactly as it would over a real one) driven
 * by the real `MemoryTreeState` (which itself uses the real
 * `fetchMemory`/`buildMemoryTree`) — everything in the Memory Panel
 * pipeline except the actual `vscode.TreeView` and a real Python
 * process.
 */

import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { test } from "node:test";
import { MemoryTreeState } from "../memory/memoryTreeState";
import { ConnectionStatus, MCPConnection } from "../mcp/connection";

class FakeChildProcess extends EventEmitter {
  public written: string[] = [];
  public stdout = new EventEmitter();
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

  respondWithError(id: number, code: number, message: string): void {
    this.stdout.emit(
      "data",
      JSON.stringify({ jsonrpc: "2.0", id, error: { code, message } }) + "\n"
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

test("integration: refresh() fetches pearl/memory over the real MCPConnection and builds the tree", async () => {
  const { connection, child } = await connectFakeServer();

  const state = new MemoryTreeState(connection);
  let changeCount = 0;
  state.onChange = () => {
    changeCount += 1;
  };

  const refreshing = state.refresh();

  await new Promise((resolve) => setImmediate(resolve));
  const memoryRequest = child.lastRequest();
  assert.equal(memoryRequest.method, "pearl/memory");

  child.respond(memoryRequest.id, {
    conversation: [{ role: "user", content: "hi", timestamp: "t1" }],
    tasks: [
      {
        id: "task-1",
        description: "add numbers",
        status: "completed",
        created_at: "c1",
        updated_at: "u1",
      },
    ],
    project: { language: "python" },
    execution_history: [
      {
        tool_name: "add",
        kwargs: { a: 1, b: 2 },
        result: 3,
        error: null,
        timestamp: "t2",
      },
    ],
  });

  await refreshing;

  assert.equal(changeCount, 1);
  assert.deepEqual(
    state.getRoots().map((node) => node.label),
    [
      "Conversation (1)",
      "Tasks (1)",
      "Execution History (1)",
      "Project Facts (1)",
    ]
  );
  assert.equal(state.getRoots()[1].children?.[0].label, "add numbers");
  assert.equal(state.getRoots()[1].children?.[0].description, "completed");

  connection.stop();
});

test("integration: a pearl/memory protocol error surfaces as a single error node, without crashing", async () => {
  const { connection, child } = await connectFakeServer();

  const state = new MemoryTreeState(connection);
  const refreshing = state.refresh();

  await new Promise((resolve) => setImmediate(resolve));
  const memoryRequest = child.lastRequest();
  child.respondWithError(memoryRequest.id, -32603, "boom");

  await refreshing;

  assert.equal(state.getRoots().length, 1);
  assert.match(state.getRoots()[0].label, /Failed to load memory: boom/);

  connection.stop();
});

test("integration: refresh() can be triggered again (e.g. by a refresh command) and updates the tree", async () => {
  const { connection, child } = await connectFakeServer();
  const state = new MemoryTreeState(connection);

  const first = state.refresh();
  await new Promise((resolve) => setImmediate(resolve));
  child.respond(child.lastRequest().id, {
    conversation: [],
    tasks: [],
    project: {},
    execution_history: [],
  });
  await first;

  assert.equal(state.getRoots()[0].label, "Conversation (0)");

  const second = state.refresh();
  await new Promise((resolve) => setImmediate(resolve));
  child.respond(child.lastRequest().id, {
    conversation: [{ role: "user", content: "hi", timestamp: "t1" }],
    tasks: [],
    project: {},
    execution_history: [],
  });
  await second;

  assert.equal(state.getRoots()[0].label, "Conversation (1)");

  connection.stop();
});
