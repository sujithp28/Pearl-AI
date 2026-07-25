import assert from "node:assert/strict";
import { test } from "node:test";
import { CheckpointTreeState } from "../checkpoints/checkpointTreeState";
import { RequestSender } from "../mcp/requestSender";

const CHECKPOINT = {
  id: "a".repeat(40),
  shortId: "aaaaaaaa",
  label: "before refactor",
  createdAt: "2026-07-25T12:00:00+00:00",
};

function senderReturning(response: unknown): RequestSender {
  return {
    sendRequest: async () => response,
  };
}

test("refresh() populates nodes from the fetched checkpoints and fires onChange", async () => {
  const sender = senderReturning({ checkpoints: [CHECKPOINT] });

  const state = new CheckpointTreeState(sender);
  let changeCount = 0;
  state.onChange = () => {
    changeCount += 1;
  };

  await state.refresh();

  assert.equal(changeCount, 1);
  assert.equal(state.getNodes()[0].label, "before refactor");
  assert.equal(state.getLastError(), undefined);
});

test("refresh() surfaces a failure as a single error node instead of throwing", async () => {
  const sender: RequestSender = {
    sendRequest: async () => {
      throw new Error("Not connected to Pearl MCP server.");
    },
  };

  const state = new CheckpointTreeState(sender);
  let changed = false;
  state.onChange = () => {
    changed = true;
  };

  await state.refresh();

  assert.equal(changed, true);
  assert.equal(state.getLastError(), "Not connected to Pearl MCP server.");
  assert.equal(state.getNodes().length, 1);
  assert.match(
    state.getNodes()[0].label,
    /Failed to load checkpoints: Not connected to Pearl MCP server\./
  );
});

test("a later successful refresh clears a previous error", async () => {
  let fail = true;
  const sender: RequestSender = {
    sendRequest: async () => {
      if (fail) {
        throw new Error("temporary failure");
      }
      return { checkpoints: [CHECKPOINT] };
    },
  };

  const state = new CheckpointTreeState(sender);

  await state.refresh();
  assert.equal(state.getLastError(), "temporary failure");

  fail = false;
  await state.refresh();

  assert.equal(state.getLastError(), undefined);
  assert.equal(state.getNodes()[0].label, "before refactor");
});

test("refresh() replaces the previous nodes on each call", async () => {
  let call = 0;
  const sender: RequestSender = {
    sendRequest: async () => {
      call += 1;
      return { checkpoints: Array(call).fill(CHECKPOINT) };
    },
  };

  const state = new CheckpointTreeState(sender);

  await state.refresh();
  assert.equal(state.getNodes().length, 1);

  await state.refresh();
  assert.equal(state.getNodes().length, 2);
});
