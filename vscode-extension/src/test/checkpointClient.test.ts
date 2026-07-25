import assert from "node:assert/strict";
import { test } from "node:test";
import {
  createCheckpoint,
  deleteCheckpoint,
  listCheckpoints,
  previewRestoreCheckpoint,
  renameCheckpoint,
  restoreCheckpoint,
} from "../mcp/checkpointClient";
import { RequestSender } from "../mcp/requestSender";

const VALID_CHECKPOINT = {
  id: "a".repeat(40),
  shortId: "aaaaaaaa",
  label: "before refactor",
  createdAt: "2026-07-25T12:00:00+00:00",
};

const VALID_RESTORE_REPORT = {
  checkpointId: "a".repeat(40),
  restored: ["a.txt"],
  removed: ["new.py"],
  changedAnything: true,
};

function fakeSender(response: unknown): {
  sender: RequestSender;
  calls: Array<{ method: string; params?: Record<string, unknown> }>;
} {
  const calls: Array<{ method: string; params?: Record<string, unknown> }> = [];

  return {
    calls,
    sender: {
      sendRequest: async (method, params) => {
        calls.push({ method, params });
        return response;
      },
    },
  };
}

// ---------------------------------------------------------------------
// createCheckpoint
// ---------------------------------------------------------------------

test("createCheckpoint sends pearl/checkpointCreate with no params by default", async () => {
  const { sender, calls } = fakeSender({ checkpoint: VALID_CHECKPOINT });

  await createCheckpoint(sender);

  assert.equal(calls[0].method, "pearl/checkpointCreate");
  assert.deepEqual(calls[0].params, {});
});

test("createCheckpoint sends the given label", async () => {
  const { sender, calls } = fakeSender({ checkpoint: VALID_CHECKPOINT });

  await createCheckpoint(sender, "before refactor");

  assert.deepEqual(calls[0].params, { label: "before refactor" });
});

test("createCheckpoint returns the checkpoint", async () => {
  const { sender } = fakeSender({ checkpoint: VALID_CHECKPOINT });

  const checkpoint = await createCheckpoint(sender);

  assert.deepEqual(checkpoint, VALID_CHECKPOINT);
});

test("createCheckpoint returns null when nothing changed", async () => {
  const { sender } = fakeSender({ checkpoint: null });

  const checkpoint = await createCheckpoint(sender);

  assert.equal(checkpoint, null);
});

test("createCheckpoint rejects a malformed checkpoint", async () => {
  const { sender } = fakeSender({ checkpoint: { id: "only-an-id" } });

  await assert.rejects(() => createCheckpoint(sender), /Malformed response/);
});

// ---------------------------------------------------------------------
// listCheckpoints
// ---------------------------------------------------------------------

test("listCheckpoints sends pearl/checkpoints with no params by default", async () => {
  const { sender, calls } = fakeSender({ checkpoints: [VALID_CHECKPOINT] });

  await listCheckpoints(sender);

  assert.equal(calls[0].method, "pearl/checkpoints");
  assert.deepEqual(calls[0].params, {});
});

test("listCheckpoints forwards an explicit limit", async () => {
  const { sender, calls } = fakeSender({ checkpoints: [] });

  await listCheckpoints(sender, 10);

  assert.deepEqual(calls[0].params, { limit: 10 });
});

test("listCheckpoints returns the checkpoints", async () => {
  const { sender } = fakeSender({ checkpoints: [VALID_CHECKPOINT] });

  const checkpoints = await listCheckpoints(sender);

  assert.deepEqual(checkpoints, [VALID_CHECKPOINT]);
});

test("listCheckpoints returns an empty list", async () => {
  const { sender } = fakeSender({ checkpoints: [] });

  assert.deepEqual(await listCheckpoints(sender), []);
});

test("listCheckpoints rejects a malformed response", async () => {
  const { sender } = fakeSender({ checkpoints: "not an array" });

  await assert.rejects(() => listCheckpoints(sender), /Malformed response/);
});

// ---------------------------------------------------------------------
// previewRestoreCheckpoint / restoreCheckpoint
// ---------------------------------------------------------------------

test("previewRestoreCheckpoint sends the checkpoint id", async () => {
  const { sender, calls } = fakeSender(VALID_RESTORE_REPORT);

  await previewRestoreCheckpoint(sender, "abc123");

  assert.equal(calls[0].method, "pearl/checkpointRestorePreview");
  assert.deepEqual(calls[0].params, { id: "abc123" });
});

test("previewRestoreCheckpoint returns the report", async () => {
  const { sender } = fakeSender(VALID_RESTORE_REPORT);

  const report = await previewRestoreCheckpoint(sender, "abc123");

  assert.deepEqual(report, VALID_RESTORE_REPORT);
});

test("restoreCheckpoint sends the checkpoint id", async () => {
  const { sender, calls } = fakeSender(VALID_RESTORE_REPORT);

  await restoreCheckpoint(sender, "abc123");

  assert.equal(calls[0].method, "pearl/checkpointRestore");
  assert.deepEqual(calls[0].params, { id: "abc123" });
});

test("restoreCheckpoint rejects a malformed report", async () => {
  const { sender } = fakeSender({ restored: "not an array" });

  await assert.rejects(() => restoreCheckpoint(sender, "abc123"), /Malformed/);
});

// ---------------------------------------------------------------------
// deleteCheckpoint
// ---------------------------------------------------------------------

test("deleteCheckpoint sends the checkpoint id", async () => {
  const { sender, calls } = fakeSender({ deleted: true });

  await deleteCheckpoint(sender, "abc123");

  assert.equal(calls[0].method, "pearl/checkpointDelete");
  assert.deepEqual(calls[0].params, { id: "abc123" });
});

// ---------------------------------------------------------------------
// renameCheckpoint
// ---------------------------------------------------------------------

test("renameCheckpoint sends the checkpoint id and new label", async () => {
  const { sender, calls } = fakeSender({ checkpoint: VALID_CHECKPOINT });

  await renameCheckpoint(sender, "abc123", "new label");

  assert.equal(calls[0].method, "pearl/checkpointRename");
  assert.deepEqual(calls[0].params, { id: "abc123", label: "new label" });
});

test("renameCheckpoint returns the updated checkpoint", async () => {
  const { sender } = fakeSender({ checkpoint: VALID_CHECKPOINT });

  const checkpoint = await renameCheckpoint(sender, "abc123", "new label");

  assert.deepEqual(checkpoint, VALID_CHECKPOINT);
});

test("renameCheckpoint rejects a malformed response", async () => {
  const { sender } = fakeSender({ checkpoint: null });

  await assert.rejects(
    () => renameCheckpoint(sender, "abc123", "new label"),
    /Malformed response/
  );
});

// ---------------------------------------------------------------------
// Propagates transport failures
// ---------------------------------------------------------------------

test("createCheckpoint propagates the underlying sendRequest rejection", async () => {
  const sender: RequestSender = {
    sendRequest: async () => {
      throw new Error("boom");
    },
  };

  await assert.rejects(() => createCheckpoint(sender), /boom/);
});
