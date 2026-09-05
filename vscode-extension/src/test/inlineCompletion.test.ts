import assert from "node:assert/strict";
import { test } from "node:test";
import { shouldSkipCompletion } from "../completion/completionGating";
import { requestCompletion, COMPLETION_TIMEOUT_MS } from "../mcp/completionClient";
import { RequestSender } from "../mcp/requestSender";

// ---------------------------------------------------------------------------
// shouldSkipCompletion — the local gates
//
// Every false negative costs a wasted inference on the typing path;
// every false positive shows ghost text where it is unwanted. Both are
// worth pinning down, since neither is visible in a compile error.
// ---------------------------------------------------------------------------

test("skips an empty line — nothing to continue", () => {
  assert.equal(shouldSkipCompletion("", ""), true);
  assert.equal(shouldSkipCompletion("    ", ""), true);
});

test("skips mid-word, where VS Code's own word completion applies", () => {
  // Cursor between "ret" and "urn": suggesting here fights the built-in.
  assert.equal(shouldSkipCompletion("    ret", "urn"), true);
});

test("does not skip at a word boundary followed by punctuation", () => {
  assert.equal(shouldSkipCompletion("    return", ""), false);
  assert.equal(shouldSkipCompletion("const x =", ""), false);
});

test("skips inside line comments in each supported style", () => {
  assert.equal(shouldSkipCompletion("  // some note", ""), true);
  assert.equal(shouldSkipCompletion("  # some note", ""), true);
  assert.equal(shouldSkipCompletion("   * jsdoc line", ""), true);
});

test("does not skip real code that merely contains a comment marker", () => {
  // The marker is not at the start of the line, so this is code.
  assert.equal(shouldSkipCompletion("url = 'http://x'", ""), false);
});

test("does not skip an ordinary code continuation point", () => {
  assert.equal(shouldSkipCompletion("def add(a, b):", ""), false);
  assert.equal(shouldSkipCompletion("        self.x =", ""), false);
});

// ---------------------------------------------------------------------------
// completionClient — must degrade silently
// ---------------------------------------------------------------------------

function fakeSender(impl: () => unknown | Promise<unknown>): RequestSender {
  return { sendRequest: async () => impl() };
}

test("returns the parsed completion result", async () => {
  const sender = fakeSender(() => ({
    completion: " a + b",
    cached: false,
    declinedReason: null,
  }));

  const result = await requestCompletion(sender, "return", "", "python");

  assert.equal(result?.completion, " a + b");
});

test("returns null instead of throwing when the transport fails", async () => {
  // An inline provider must degrade to "no suggestion", never to an
  // error popup mid-keystroke.
  const sender = fakeSender(() => {
    throw new Error("server disconnected");
  });

  const result = await requestCompletion(sender, "return", "", "python");

  assert.equal(result, null);
});

test("returns null on a malformed response rather than trusting it", async () => {
  const sender = fakeSender(() => ({ unexpected: "shape" }));

  const result = await requestCompletion(sender, "return", "", "python");

  assert.equal(result, null);
});

test("forwards prefix, suffix and language to the server", async () => {
  const calls: Array<Record<string, unknown>> = [];
  const sender: RequestSender = {
    sendRequest: async (_method, params) => {
      calls.push(params as Record<string, unknown>);
      return { completion: "x", cached: false, declinedReason: null };
    },
  };

  await requestCompletion(sender, "pre", "suf", "typescript");

  assert.deepEqual(calls[0], {
    prefix: "pre",
    suffix: "suf",
    language: "typescript",
  });
});

test("uses pearl/complete as the method", async () => {
  const methods: string[] = [];
  const sender: RequestSender = {
    sendRequest: async (method) => {
      methods.push(method);
      return { completion: "x", cached: false, declinedReason: null };
    },
  };

  await requestCompletion(sender, "pre", "", "python");

  assert.deepEqual(methods, ["pearl/complete"]);
});

test("uses a short timeout, not the general LLM call timeout", async () => {
  // A suggestion arriving after the developer has typed past it is worse
  // than none, so this budget must stay well under LLM_CALL_TIMEOUT_MS.
  const timeouts: Array<number | undefined> = [];
  const sender: RequestSender = {
    sendRequest: async (_method, _params, timeout) => {
      timeouts.push(timeout as number | undefined);
      return { completion: "x", cached: false, declinedReason: null };
    },
  };

  await requestCompletion(sender, "pre", "", "python");

  assert.equal(timeouts[0], COMPLETION_TIMEOUT_MS);
  assert.ok(
    COMPLETION_TIMEOUT_MS <= 3000,
    "completion timeout must stay on the typing-path budget"
  );
});
