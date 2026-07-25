import assert from "node:assert/strict";
import { test } from "node:test";
import { describeConnectionError } from "../chat/errorReporting";

test("describeConnectionError rewrites a timeout error without the raw method name", () => {
  const message = describeConnectionError(
    new Error("Timed out waiting for response to 'pearl/chat'.")
  );

  assert.doesNotMatch(message, /pearl\//);
  assert.match(message, /taking longer than expected/i);
});

test("describeConnectionError rewrites a timeout error for any method", () => {
  const message = describeConnectionError(
    new Error("Timed out waiting for response to 'pearl/planOnly'.")
  );

  assert.doesNotMatch(message, /pearl\//);
  assert.match(message, /taking longer than expected/i);
});

test("describeConnectionError rewrites the not-connected error", () => {
  const message = describeConnectionError(
    new Error("Not connected to Pearl MCP server.")
  );

  assert.match(message, /isn't connected/i);
});

test("describeConnectionError passes through an unrecognized Error message unchanged", () => {
  const message = describeConnectionError(new Error("Malformed response from Pearl MCP server."));

  assert.equal(message, "Malformed response from Pearl MCP server.");
});

test("describeConnectionError handles a non-Error thrown value", () => {
  const message = describeConnectionError("just a string");

  assert.equal(message, "just a string");
});
