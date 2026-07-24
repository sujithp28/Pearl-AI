import assert from "node:assert/strict";
import { test } from "node:test";
import {
  PLAN_ARGS_COLLAPSE_THRESHOLD,
  formatPlanSteps,
} from "../chat/planFormatting";

test("formatPlanSteps numbers steps starting at 1", () => {
  const formatted = formatPlanSteps([
    { tool: "read_file", arguments: { path: "a.txt" } },
    { tool: "write_file", arguments: { path: "b.txt", content: "x" } },
  ]);

  assert.deepEqual(
    formatted.map((s) => s.index),
    [1, 2]
  );
  assert.deepEqual(
    formatted.map((s) => s.tool),
    ["read_file", "write_file"]
  );
});

test("formatPlanSteps pretty-prints arguments as JSON", () => {
  const [step] = formatPlanSteps([
    { tool: "read_file", arguments: { path: "a.txt" } },
  ]);

  assert.equal(step.argumentsText, JSON.stringify({ path: "a.txt" }, null, 2));
});

test("formatPlanSteps does not collapse short arguments", () => {
  const [step] = formatPlanSteps([
    { tool: "read_file", arguments: { path: "a.txt" } },
  ]);

  assert.equal(step.collapsed, false);
});

test("formatPlanSteps collapses arguments longer than the threshold", () => {
  const longValue = "x".repeat(PLAN_ARGS_COLLAPSE_THRESHOLD + 1);

  const [step] = formatPlanSteps([
    { tool: "write_file", arguments: { content: longValue } },
  ]);

  assert.ok(step.argumentsText.length > PLAN_ARGS_COLLAPSE_THRESHOLD);
  assert.equal(step.collapsed, true);
});

test("formatPlanSteps handles empty arguments", () => {
  const [step] = formatPlanSteps([{ tool: "pwd", arguments: {} }]);

  assert.equal(step.argumentsText, "{}");
  assert.equal(step.collapsed, false);
});

test("formatPlanSteps handles an empty plan", () => {
  assert.deepEqual(formatPlanSteps([]), []);
});
