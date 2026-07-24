import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { test } from "node:test";
import { ConnectionStatus, MCPConnection } from "../mcp/connection";

/**
 * A fake child process: enough of Node's `ChildProcess` shape to
 * exercise `MCPConnection` without spawning anything real.
 */
class FakeChildProcess extends EventEmitter {
  public written: string[] = [];
  public killed = false;
  public stdout = new EventEmitter();
  public stdin = {
    write: (chunk: string): boolean => {
      this.written.push(chunk);
      return true;
    },
  };

  kill(): void {
    this.killed = true;
  }

  /** Simulate the server responding to the request with this id. */
  respond(id: number, result: unknown): void {
    this.stdout.emit(
      "data",
      JSON.stringify({ jsonrpc: "2.0", id, result }) + "\n"
    );
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
      reject(
        new Error(
          `Timed out waiting for status '${target}' (currently '${connection.getStatus()}').`
        )
      );
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

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

test("start() spawns the process and reaches 'connected' after initialize responds", async () => {
  const processes: FakeChildProcess[] = [];

  const connection = new MCPConnection({
    command: "python3",
    args: ["-m", "src.mcp"],
    spawnFn: () => {
      const child = new FakeChildProcess();
      processes.push(child);
      return child;
    },
  });

  connection.start();

  assert.equal(processes.length, 1);
  assert.equal(connection.getStatus(), "connecting");

  const initializeLine = JSON.parse(processes[0].written[0]);
  assert.equal(initializeLine.method, "initialize");

  processes[0].respond(initializeLine.id, { protocolVersion: "2024-11-05" });

  await waitForStatus(connection, "connected");

  connection.stop();
});

test("sendRequest resolves with the matching response result", async () => {
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

  const initializeLine = JSON.parse(child.written[0]);
  child.respond(initializeLine.id, {});
  await waitForStatus(connection, "connected");

  const pending = connection.sendRequest("tools/list");

  const toolsListLine = JSON.parse(child.written[1]);
  assert.equal(toolsListLine.method, "tools/list");

  child.respond(toolsListLine.id, { tools: [] });

  const result = await pending;
  assert.deepEqual(result, { tools: [] });

  connection.stop();
});

test("a spawn 'error' event is handled gracefully and schedules a reconnect", async () => {
  const processes: FakeChildProcess[] = [];

  const connection = new MCPConnection({
    command: "does-not-exist",
    args: [],
    reconnectDelayMs: 10,
    spawnFn: () => {
      const child = new FakeChildProcess();
      processes.push(child);
      return child;
    },
  });

  connection.start();
  processes[0].emit("error", new Error("spawn ENOENT"));

  await waitForStatus(connection, "error");

  await sleep(50);

  assert.equal(processes.length, 2, "expected a reconnect attempt");

  connection.stop();
});

test("an unexpected exit after connecting triggers reconnect and recovery", async () => {
  const processes: FakeChildProcess[] = [];

  const connection = new MCPConnection({
    command: "python3",
    args: ["-m", "src.mcp"],
    reconnectDelayMs: 10,
    spawnFn: () => {
      const child = new FakeChildProcess();
      processes.push(child);
      return child;
    },
  });

  connection.start();

  const firstInit = JSON.parse(processes[0].written[0]);
  processes[0].respond(firstInit.id, {});
  await waitForStatus(connection, "connected");

  processes[0].emit("exit", 1, null);

  await waitForStatus(connection, "error");
  await sleep(50);

  assert.equal(processes.length, 2, "expected a reconnect attempt");

  const secondInit = JSON.parse(processes[1].written[0]);
  processes[1].respond(secondInit.id, {});

  await waitForStatus(connection, "connected");

  connection.stop();
});

test("stop() marks the connection disconnected and does not reconnect", async () => {
  const processes: FakeChildProcess[] = [];

  const connection = new MCPConnection({
    command: "python3",
    args: ["-m", "src.mcp"],
    reconnectDelayMs: 10,
    spawnFn: () => {
      const child = new FakeChildProcess();
      processes.push(child);
      return child;
    },
  });

  connection.start();

  const initLine = JSON.parse(processes[0].written[0]);
  processes[0].respond(initLine.id, {});
  await waitForStatus(connection, "connected");

  connection.stop();

  assert.equal(connection.getStatus(), "disconnected");
  assert.equal(processes[0].killed, true);

  // Simulate the OS delivering the exit event slightly after kill().
  processes[0].emit("exit", null, "SIGTERM");

  await sleep(50);

  assert.equal(connection.getStatus(), "disconnected");
  assert.equal(processes.length, 1, "should not reconnect after stop()");
});

test("sendRequest rejects immediately when not connected", async () => {
  const connection = new MCPConnection({
    command: "python3",
    args: ["-m", "src.mcp"],
    spawnFn: () => {
      throw new Error("should not be called");
    },
  });

  await assert.rejects(
    () => connection.sendRequest("tools/list"),
    /Not connected/
  );
});

test("sendRequest times out if no response arrives", async () => {
  let child!: FakeChildProcess;

  const connection = new MCPConnection({
    command: "python3",
    args: ["-m", "src.mcp"],
    initializeTimeoutMs: 20,
    reconnectDelayMs: 1000,
    spawnFn: () => {
      child = new FakeChildProcess();
      return child;
    },
  });

  connection.start();

  await waitForStatus(connection, "error");

  assert.ok(child.written.length >= 1);

  connection.stop();
});
