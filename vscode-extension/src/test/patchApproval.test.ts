import assert from "node:assert/strict";
import { test } from "node:test";
import { WebviewPatchApprover } from "../chat/patchApproval";
import { formatPatchFiles } from "../chat/patchFormatting";

const SAMPLE_FILES = [
  { path: "a.py", diff: "+x = 1", isNewFile: true },
];

test("requestApproval posts the formatted patch batch to the webview", async () => {
  const posted: unknown[] = [];
  const approver = new WebviewPatchApprover((message) => {
    posted.push(message);
  });

  const pending = approver.requestApproval(SAMPLE_FILES);

  assert.deepEqual(posted, [
    { type: "showPatchBatch", files: formatPatchFiles(SAMPLE_FILES) },
  ]);

  approver.resolveDecision("approve");
  assert.equal(await pending, "approve");
});

test("resolveDecision('approve') resolves the pending promise with 'approve'", async () => {
  const approver = new WebviewPatchApprover(() => {});

  const pending = approver.requestApproval(SAMPLE_FILES);
  approver.resolveDecision("approve");

  assert.equal(await pending, "approve");
});

test("resolveDecision('reject') resolves the pending promise with 'reject'", async () => {
  const approver = new WebviewPatchApprover(() => {});

  const pending = approver.requestApproval(SAMPLE_FILES);
  approver.resolveDecision("reject");

  assert.equal(await pending, "reject");
});

test("resolveDecision is a no-op when there is no pending patch batch", () => {
  const approver = new WebviewPatchApprover(() => {});

  // Must not throw.
  approver.resolveDecision("approve");
});

test("a stray second resolveDecision call does not affect the next batch", async () => {
  const approver = new WebviewPatchApprover(() => {});

  const first = approver.requestApproval(SAMPLE_FILES);
  approver.resolveDecision("approve");
  assert.equal(await first, "approve");

  // Late/duplicate decision from a stale webview message: ignored.
  approver.resolveDecision("reject");

  const second = approver.requestApproval(SAMPLE_FILES);
  approver.resolveDecision("reject");
  assert.equal(await second, "reject");
});

test("requestApproval works for a multi-file batch", async () => {
  const posted: unknown[] = [];
  const approver = new WebviewPatchApprover((message) => {
    posted.push(message);
  });

  const files = [
    { path: "a.py", diff: "+x", isNewFile: true },
    { path: "b.py", diff: "-y", isNewFile: false },
    { path: "c.py", diff: "+z", isNewFile: true },
  ];

  approver.requestApproval(files);

  const posted0 = posted[0] as { files: unknown[] };
  assert.equal(posted0.files.length, 3);
});
