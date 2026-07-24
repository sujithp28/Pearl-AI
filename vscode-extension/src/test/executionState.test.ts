import assert from "node:assert/strict";
import { test } from "node:test";
import { EXECUTION_STATE_LABELS } from "../chat/executionState";

test("defines the five required execution states with human-readable labels", () => {
  assert.equal(EXECUTION_STATE_LABELS.awaiting_approval, "Awaiting Approval");
  assert.equal(
    EXECUTION_STATE_LABELS.applying_patches,
    "Applying Patches..."
  );
  assert.equal(
    EXECUTION_STATE_LABELS.resuming,
    "Resuming Execution..."
  );
  assert.equal(EXECUTION_STATE_LABELS.completed, "Completed");
  assert.equal(EXECUTION_STATE_LABELS.cancelled, "Cancelled");
});

test("has exactly the five required states, no more, no fewer", () => {
  assert.deepEqual(Object.keys(EXECUTION_STATE_LABELS).sort(), [
    "applying_patches",
    "awaiting_approval",
    "cancelled",
    "completed",
    "resuming",
  ]);
});
