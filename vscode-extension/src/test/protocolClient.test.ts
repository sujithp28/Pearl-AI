import assert from "node:assert/strict";
import { test } from "node:test";
import {
  MCPProtocolClient,
  isErrorResponse,
  JsonRpcSuccessResponse,
  JsonRpcErrorResponse,
} from "../mcp/protocolClient";

test("buildRequest assigns incrementing ids and a newline-terminated line", () => {
  const client = new MCPProtocolClient();

  const first = client.buildRequest("initialize");
  const second = client.buildRequest("tools/list", { foo: "bar" });

  assert.equal(first.id, 1);
  assert.equal(second.id, 2);
  assert.ok(first.line.endsWith("\n"));

  const parsed = JSON.parse(first.line);
  assert.deepEqual(parsed, {
    jsonrpc: "2.0",
    id: 1,
    method: "initialize",
    params: {},
  });

  const parsedSecond = JSON.parse(second.line);
  assert.deepEqual(parsedSecond.params, { foo: "bar" });
});

test("buildNotification has no id", () => {
  const client = new MCPProtocolClient();

  const line = client.buildNotification("exit");
  const parsed = JSON.parse(line);

  assert.equal(parsed.method, "exit");
  assert.equal("id" in parsed, false);
});

test("parseResponseLine parses a success response", () => {
  const client = new MCPProtocolClient();

  const response = client.parseResponseLine(
    JSON.stringify({ jsonrpc: "2.0", id: 1, result: { ok: true } })
  );

  assert.ok(response);
  assert.equal(isErrorResponse(response as JsonRpcSuccessResponse), false);
  assert.deepEqual((response as JsonRpcSuccessResponse).result, { ok: true });
});

test("parseResponseLine parses an error response", () => {
  const client = new MCPProtocolClient();

  const response = client.parseResponseLine(
    JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      error: { code: -32601, message: "Unknown method" },
    })
  );

  assert.ok(response);
  assert.equal(isErrorResponse(response as JsonRpcErrorResponse), true);
  assert.equal(
    (response as JsonRpcErrorResponse).error.message,
    "Unknown method"
  );
});

test("parseResponseLine returns null for blank lines", () => {
  const client = new MCPProtocolClient();

  assert.equal(client.parseResponseLine(""), null);
  assert.equal(client.parseResponseLine("   \n"), null);
});

test("parseResponseLine returns null for invalid JSON", () => {
  const client = new MCPProtocolClient();

  assert.equal(client.parseResponseLine("not json"), null);
});

test("parseResponseLine returns null for JSON that is not a JSON-RPC response", () => {
  const client = new MCPProtocolClient();

  assert.equal(client.parseResponseLine(JSON.stringify([1, 2, 3])), null);
  assert.equal(
    client.parseResponseLine(JSON.stringify({ hello: "world" })),
    null
  );
  assert.equal(
    client.parseResponseLine(JSON.stringify({ jsonrpc: "1.0", id: 1 })),
    null
  );
});

test("parseNotificationLine parses a server-pushed notification", () => {
  const client = new MCPProtocolClient();

  const notification = client.parseNotificationLine(
    JSON.stringify({
      jsonrpc: "2.0",
      method: "pearl/progress",
      params: { status: "planning", currentStep: 0, totalSteps: 0 },
    })
  );

  assert.ok(notification);
  assert.equal(notification?.method, "pearl/progress");
  assert.deepEqual(notification?.params, {
    status: "planning",
    currentStep: 0,
    totalSteps: 0,
  });
});

test("parseNotificationLine defaults params to {} when omitted", () => {
  const client = new MCPProtocolClient();

  const notification = client.parseNotificationLine(
    JSON.stringify({ jsonrpc: "2.0", method: "pearl/progress" })
  );

  assert.deepEqual(notification?.params, {});
});

test("parseNotificationLine returns null for a response line (has an id)", () => {
  const client = new MCPProtocolClient();

  assert.equal(
    client.parseNotificationLine(
      JSON.stringify({ jsonrpc: "2.0", id: 1, result: { ok: true } })
    ),
    null
  );
});

test("parseNotificationLine returns null for blank lines and invalid JSON", () => {
  const client = new MCPProtocolClient();

  assert.equal(client.parseNotificationLine(""), null);
  assert.equal(client.parseNotificationLine("   \n"), null);
  assert.equal(client.parseNotificationLine("not json"), null);
});

test("parseNotificationLine returns null for JSON missing a method", () => {
  const client = new MCPProtocolClient();

  assert.equal(
    client.parseNotificationLine(JSON.stringify({ jsonrpc: "2.0" })),
    null
  );
  assert.equal(
    client.parseNotificationLine(JSON.stringify({ jsonrpc: "1.0", method: "x" })),
    null
  );
});

test("parseResponseLine and parseNotificationLine are mutually exclusive", () => {
  const client = new MCPProtocolClient();

  const responseLine = JSON.stringify({ jsonrpc: "2.0", id: 1, result: {} });
  const notificationLine = JSON.stringify({
    jsonrpc: "2.0",
    method: "pearl/progress",
    params: {},
  });

  assert.ok(client.parseResponseLine(responseLine));
  assert.equal(client.parseNotificationLine(responseLine), null);

  assert.ok(client.parseNotificationLine(notificationLine));
  assert.equal(client.parseResponseLine(notificationLine), null);
});
