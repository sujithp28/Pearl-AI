// Tests for the pure formatting helpers.
//
// These are the functions with no DOM and no network in them, which is
// exactly why they are the ones worth testing: every other module in
// pearl_ui/js reaches for the document, so it needs a browser. These do
// not, so they run under `node --test` with no harness at all.
//
// `escapeHtml` earns its tests twice over. Every piece of untrusted text
// the UI renders — a tool name from a plan, a conversation title, a file
// path in a diff — passes through it on the way into an innerHTML
// assignment. A gap here is not a formatting bug, it is stored XSS.

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  bestOutput,
  escapeHtml,
  relativeTime,
  toolLabel,
} from "./format.js";

describe("escapeHtml", () => {
  it("neutralises the characters that open a tag or close an attribute", () => {
    assert.equal(
      escapeHtml('<script>alert("x")</script>'),
      "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;",
    );
  });

  it("escapes ampersands first so entities are not double-decoded", () => {
    // If & were escaped after <, the output of "&lt;" would come back
    // as a literal "<" in the browser and the escaping would be void.
    assert.equal(escapeHtml("&lt;"), "&amp;lt;");
  });

  it("returns an empty string for null, undefined and empty input", () => {
    assert.equal(escapeHtml(null), "");
    assert.equal(escapeHtml(undefined), "");
    assert.equal(escapeHtml(""), "");
  });

  it("coerces non-strings rather than throwing", () => {
    assert.equal(escapeHtml(42), "42");
  });
});

describe("toolLabel", () => {
  it("uses the friendly name where one is known", () => {
    assert.equal(toolLabel("execute_shell"), "Run command");
    assert.equal(toolLabel("find_references"), "Find references");
  });

  it("title-cases an unknown tool rather than showing a raw identifier", () => {
    assert.equal(toolLabel("some_new_tool"), "Some New Tool");
  });

  it("falls back to a generic label when the name is missing", () => {
    assert.equal(toolLabel(""), "Tool");
    assert.equal(toolLabel(undefined), "Tool");
  });
});

describe("relativeTime", () => {
  it("reports minutes within the hour", () => {
    assert.equal(relativeTime(Date.now() - 5 * 60_000), "5m");
  });

  it("never reports zero minutes for something that just happened", () => {
    // "0m" reads as broken. A conversation saved a second ago is "1m".
    assert.equal(relativeTime(Date.now()), "1m");
  });

  it("switches to hours and then days", () => {
    assert.equal(relativeTime(Date.now() - 3 * 3_600_000), "3h");
    assert.equal(relativeTime(Date.now() - 2 * 86_400_000), "2d");
  });
});

describe("bestOutput", () => {
  const step = (over) => ({ succeeded: true, tool_name: "read_file", ...over });

  it("returns null when there are no steps", () => {
    assert.equal(bestOutput([]), null);
    assert.equal(bestOutput(undefined), null);
  });

  it("prefers the last successful text-producing step", () => {
    const steps = [
      step({ result: "x".repeat(60) }),
      step({ tool_name: "explain_file", result: "y".repeat(60) }),
    ];

    assert.ok(bestOutput(steps).startsWith("y"));
  });

  it("skips failed steps", () => {
    const steps = [
      step({ result: "good".repeat(30) }),
      step({ succeeded: false, result: "bad".repeat(40) }),
    ];

    assert.ok(bestOutput(steps).startsWith("good"));
  });

  it("caps the returned text so one step cannot flood the transcript", () => {
    const steps = [step({ result: "z".repeat(9000) })];

    assert.equal(bestOutput(steps).length, 5000);
  });
});
