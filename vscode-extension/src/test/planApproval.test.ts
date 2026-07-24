import assert from "node:assert/strict";
import { test } from "node:test";
import { WebviewPlanApprover } from "../chat/planApproval";
import { formatPlanSteps } from "../chat/planFormatting";

test("requestApproval posts the formatted plan to the webview", async () => {
  const posted: unknown[] = [];
  const approver = new WebviewPlanApprover((message) => {
    posted.push(message);
  });

  const steps = [{ tool: "read_file", arguments: { path: "a.txt" } }];
  const pending = approver.requestApproval(steps);

  assert.deepEqual(posted, [
    { type: "showPlan", steps: formatPlanSteps(steps) },
  ]);

  approver.resolveDecision("execute");
  assert.equal(await pending, "execute");
});

test("resolveDecision('execute') resolves the pending promise with 'execute'", async () => {
  const approver = new WebviewPlanApprover(() => {});

  const pending = approver.requestApproval([]);
  approver.resolveDecision("execute");

  assert.equal(await pending, "execute");
});

test("resolveDecision('cancel') resolves the pending promise with 'cancel'", async () => {
  const approver = new WebviewPlanApprover(() => {});

  const pending = approver.requestApproval([]);
  approver.resolveDecision("cancel");

  assert.equal(await pending, "cancel");
});

test("resolveDecision is a no-op when there is no pending plan", () => {
  const approver = new WebviewPlanApprover(() => {});

  // Must not throw.
  approver.resolveDecision("execute");
});

test("a stray second resolveDecision call does not affect the next plan", async () => {
  const approver = new WebviewPlanApprover(() => {});

  const first = approver.requestApproval([]);
  approver.resolveDecision("execute");
  assert.equal(await first, "execute");

  // Late/duplicate decision from a stale webview message: ignored.
  approver.resolveDecision("cancel");

  const second = approver.requestApproval([]);
  approver.resolveDecision("cancel");
  assert.equal(await second, "cancel");
});
