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
