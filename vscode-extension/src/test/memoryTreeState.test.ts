import assert from "node:assert/strict";
import { test } from "node:test";
import { MemoryTreeState } from "../memory/memoryTreeState";
import { RequestSender } from "../mcp/requestSender";

function senderReturning(response: unknown): RequestSender {
  return {
    sendRequest: async () => response,
  };
}

test("refresh() populates roots from the fetched snapshot and fires onChange", async () => {
  const sender = senderReturning({
    conversation: [{ role: "user", content: "hi", timestamp: "t1" }],
    tasks: [],
    project: {},
    execution_history: [],
  });

  const state = new MemoryTreeState(sender);
  let changeCount = 0;
  state.onChange = () => {
    changeCount += 1;
  };

  await state.refresh();

  assert.equal(changeCount, 1);
  assert.equal(state.getRoots()[0].label, "Conversation (1)");
  assert.equal(state.getLastError(), undefined);
});

test("refresh() surfaces a failure as a single error node instead of throwing", async () => {
  const sender: RequestSender = {
    sendRequest: async () => {
      throw new Error("Not connected to Pearl MCP server.");
    },
  };

  const state = new MemoryTreeState(sender);
  let changed = false;
  state.onChange = () => {
    changed = true;
  };

  await state.refresh();

  assert.equal(changed, true);
  assert.equal(state.getLastError(), "Not connected to Pearl MCP server.");
  assert.equal(state.getRoots().length, 1);
  assert.match(
    state.getRoots()[0].label,
    /Failed to load memory: Not connected to Pearl MCP server\./
  );
});

test("refresh() can be called repeatedly and each call replaces the previous roots", async () => {
  let call = 0;
  const sender: RequestSender = {
    sendRequest: async () => {
      call += 1;
      return {
        conversation: Array(call).fill({
          role: "user",
          content: "hi",
          timestamp: "t",
        }),
        tasks: [],
        project: {},
        execution_history: [],
      };
    },
  };

  const state = new MemoryTreeState(sender);

  await state.refresh();
  assert.equal(state.getRoots()[0].label, "Conversation (1)");

  await state.refresh();
  assert.equal(state.getRoots()[0].label, "Conversation (2)");
});

test("a later successful refresh clears a previous error", async () => {
  let fail = true;
  const sender: RequestSender = {
    sendRequest: async () => {
      if (fail) {
        throw new Error("temporary failure");
      }
      return { conversation: [], tasks: [], project: {}, execution_history: [] };
    },
  };

  const state = new MemoryTreeState(sender);

  await state.refresh();
  assert.equal(state.getLastError(), "temporary failure");

  fail = false;
  await state.refresh();

  assert.equal(state.getLastError(), undefined);
  assert.equal(state.getRoots()[0].label, "Conversation (0)");
});
