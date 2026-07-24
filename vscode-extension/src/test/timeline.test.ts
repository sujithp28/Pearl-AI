import assert from "node:assert/strict";
import { test } from "node:test";
import {
  TIMELINE_STAGE_LABELS,
  TIMELINE_STAGE_ORDER,
} from "../chat/timeline";

test("defines the five stages in the required order", () => {
  assert.deepEqual(TIMELINE_STAGE_ORDER, [
    "planning",
    "plan_ready",
    "waiting_approval",
    "running_tool",
    "completed",
  ]);
});

test("every stage has a human-readable label matching the requirement text", () => {
  assert.equal(TIMELINE_STAGE_LABELS.planning, "Planning...");
  assert.equal(TIMELINE_STAGE_LABELS.plan_ready, "Plan Ready");
  assert.equal(TIMELINE_STAGE_LABELS.waiting_approval, "Waiting for Approval");
  assert.equal(TIMELINE_STAGE_LABELS.running_tool, "Running Tool...");
  assert.equal(TIMELINE_STAGE_LABELS.completed, "Completed");
});

test("every stage in the order has a corresponding label and vice versa", () => {
  const labelKeys = Object.keys(TIMELINE_STAGE_LABELS).sort();
  const orderKeys = [...TIMELINE_STAGE_ORDER].sort();

  assert.deepEqual(labelKeys, orderKeys);
});
