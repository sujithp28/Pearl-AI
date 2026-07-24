import assert from "node:assert/strict";
import { test } from "node:test";
import {
  DIFF_COLLAPSE_LINE_THRESHOLD,
  countDiffLines,
  formatPatchFiles,
  guessLanguage,
} from "../chat/patchFormatting";

const SMALL_DIFF = [
  "--- a/a.py",
  "+++ b/a.py",
  "@@ -1,2 +1,2 @@",
  " unchanged",
  "-old line",
  "+new line",
].join("\n");

test("countDiffLines counts added and removed lines only", () => {
  const counts = countDiffLines(SMALL_DIFF);

  assert.equal(counts.additions, 1);
  assert.equal(counts.removals, 1);
});

test("countDiffLines ignores the file header lines", () => {
  const diff = "--- a/x.py\n+++ b/x.py\n+added\n";

  const counts = countDiffLines(diff);

  assert.equal(counts.additions, 1);
  assert.equal(counts.removals, 0);
});

test("countDiffLines handles an empty diff", () => {
  assert.deepEqual(countDiffLines(""), { additions: 0, removals: 0 });
});

test("guessLanguage maps known extensions", () => {
  assert.equal(guessLanguage("src/main.py"), "python");
  assert.equal(guessLanguage("app.ts"), "typescript");
  assert.equal(guessLanguage("style.css"), "css");
});

test("guessLanguage returns empty string for unknown or missing extensions", () => {
  assert.equal(guessLanguage("Makefile"), "");
  assert.equal(guessLanguage("README.weirdext"), "");
});

test("formatPatchFiles computes additions/removals per file", () => {
  const [file] = formatPatchFiles([
    { path: "a.py", diff: SMALL_DIFF, isNewFile: false },
  ]);

  assert.equal(file.additions, 1);
  assert.equal(file.removals, 1);
  assert.equal(file.path, "a.py");
  assert.equal(file.isNewFile, false);
  assert.equal(file.language, "python");
});

test("formatPatchFiles does not collapse small diffs", () => {
  const [file] = formatPatchFiles([
    { path: "a.py", diff: SMALL_DIFF, isNewFile: false },
  ]);

  assert.equal(file.collapsed, false);
});

test("formatPatchFiles collapses diffs longer than the threshold", () => {
  const bigDiff =
    "--- a/big.py\n+++ b/big.py\n" +
    Array.from(
      { length: DIFF_COLLAPSE_LINE_THRESHOLD + 5 },
      (_, i) => `+line ${i}`
    ).join("\n");

  const [file] = formatPatchFiles([
    { path: "big.py", diff: bigDiff, isNewFile: false },
  ]);

  assert.equal(file.collapsed, true);
});

test("formatPatchFiles handles multiple files independently", () => {
  const files = formatPatchFiles([
    { path: "a.py", diff: "+x", isNewFile: true },
    { path: "b.py", diff: "-y", isNewFile: false },
  ]);

  assert.equal(files.length, 2);
  assert.equal(files[0].path, "a.py");
  assert.equal(files[0].isNewFile, true);
  assert.equal(files[1].path, "b.py");
  assert.equal(files[1].isNewFile, false);
});

test("formatPatchFiles handles an empty batch", () => {
  assert.deepEqual(formatPatchFiles([]), []);
});
