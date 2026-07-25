import assert from "node:assert/strict";
import { test } from "node:test";
import { buildCheckpointTree } from "../checkpoints/checkpointTree";
import { Checkpoint } from "../mcp/checkpointClient";

const CHECKPOINT: Checkpoint = {
  id: "a".repeat(40),
  shortId: "aaaaaaaa",
  label: "before refactor",
  createdAt: "2026-07-25T12:00:00+00:00",
};

test("buildCheckpointTree returns a placeholder row when there are none", () => {
  const nodes = buildCheckpointTree([]);

  assert.equal(nodes.length, 1);
  assert.equal(nodes[0].label, "No checkpoints yet.");
  assert.equal(nodes[0].checkpoint, undefined);
});

test("buildCheckpointTree returns one node per checkpoint", () => {
  const second: Checkpoint = { ...CHECKPOINT, id: "b".repeat(40), label: "second" };

  const nodes = buildCheckpointTree([CHECKPOINT, second]);

  assert.equal(nodes.length, 2);
  assert.equal(nodes[0].label, "before refactor");
  assert.equal(nodes[1].label, "second");
});

test("buildCheckpointTree preserves the label as the node label", () => {
  const nodes = buildCheckpointTree([CHECKPOINT]);

  assert.equal(nodes[0].label, CHECKPOINT.label);
});

test("buildCheckpointTree attaches the checkpoint object for command handlers", () => {
  const nodes = buildCheckpointTree([CHECKPOINT]);

  assert.deepEqual(nodes[0].checkpoint, CHECKPOINT);
});

test("buildCheckpointTree's tooltip includes the short id and full label", () => {
  const nodes = buildCheckpointTree([CHECKPOINT]);

  assert.match(nodes[0].tooltip ?? "", /aaaaaaaa/);
  assert.match(nodes[0].tooltip ?? "", /before refactor/);
});

test("buildCheckpointTree falls back to the raw string for an unparsable date", () => {
  const withBadDate: Checkpoint = { ...CHECKPOINT, createdAt: "not-a-date" };

  const nodes = buildCheckpointTree([withBadDate]);

  assert.equal(nodes[0].description, "not-a-date");
});
