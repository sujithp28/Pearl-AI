import assert from "node:assert/strict";
import { test } from "node:test";
import { parseProgressEvent } from "../mcp/progressClient";

test("parseProgressEvent parses a well-formed payload", () => {
  const event = parseProgressEvent({
    status: "executing_step",
    currentStep: 2,
    totalSteps: 5,
    currentAction: "Hammering out some code...",
  });

  assert.deepEqual(event, {
    status: "executing_step",
    currentStep: 2,
    totalSteps: 5,
    currentAction: "Hammering out some code...",
  });
});

test("parseProgressEvent accepts every known status", () => {
  const statuses = [
    "planning",
    "executing_step",
    "step_completed",
    "step_failed",
    "replanning",
    "task_completed",
    "cancelled",
    "awaiting_approval",
    "rejected",
  ];

  for (const status of statuses) {
    const event = parseProgressEvent({
      status,
      currentStep: 0,
      totalSteps: 0,
      currentAction: "",
    });
    assert.ok(event, `expected '${status}' to be a valid status`);
    assert.equal(event?.status, status);
  }
});

test("parseProgressEvent rejects an unknown status", () => {
  assert.equal(
    parseProgressEvent({
      status: "not-a-real-status",
      currentStep: 0,
      totalSteps: 0,
      currentAction: "",
    }),
    null
  );
});

test("parseProgressEvent rejects missing/wrong-typed fields", () => {
  assert.equal(parseProgressEvent({}), null);
  assert.equal(
    parseProgressEvent({ status: "planning", currentStep: "1", totalSteps: 1, currentAction: "x" }),
    null
  );
  assert.equal(
    parseProgressEvent({ status: "planning", currentStep: 1, totalSteps: 1 }),
    null
  );
});
