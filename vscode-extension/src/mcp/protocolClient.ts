/**
 * JSON-RPC framing for Pearl's MCP protocol.
 *
 * Mirrors the newline-delimited JSON-RPC 2.0 wire format implemented
 * server-side in `src/mcp/protocol.py`, but only the client-side
 * half: building outgoing requests/notifications and parsing
 * incoming response lines. No process or transport logic lives
 * here, so it has no dependency on `vscode` or `child_process` and
 * is trivially unit testable.
 */

export interface JsonRpcRequestMessage {
  jsonrpc: "2.0";
  id: number;
  method: string;
  params: Record<string, unknown>;
}

export interface JsonRpcNotificationMessage {
  jsonrpc: "2.0";
  method: string;
  params: Record<string, unknown>;
}

export interface JsonRpcSuccessResponse {
  jsonrpc: "2.0";
  id: number | string | null;
  result: unknown;
}

export interface JsonRpcErrorResponse {
  jsonrpc: "2.0";
  id: number | string | null;
  error: {
    code: number;
    message: string;
    data?: unknown;
  };
}

export type JsonRpcResponseMessage =
  | JsonRpcSuccessResponse
  | JsonRpcErrorResponse;

export function isErrorResponse(
  response: JsonRpcResponseMessage
): response is JsonRpcErrorResponse {
  return (response as JsonRpcErrorResponse).error !== undefined;
}

export class MCPProtocolClient {
  private nextId = 1;

  /**
   * Build a JSON-RPC request. Returns the id assigned (for
   * correlating the eventual response) and the newline-terminated
   * wire line to write to the server's stdin.
   */
  buildRequest(
    method: string,
    params?: Record<string, unknown>
  ): { id: number; line: string } {
    const id = this.nextId++;
    const message: JsonRpcRequestMessage = {
      jsonrpc: "2.0",
      id,
      method,
      params: params ?? {},
    };

    return { id, line: JSON.stringify(message) + "\n" };
  }

  /**
   * Build a JSON-RPC notification (no response expected).
   */
  buildNotification(method: string, params?: Record<string, unknown>): string {
    const message: JsonRpcNotificationMessage = {
      jsonrpc: "2.0",
      method,
      params: params ?? {},
    };

    return JSON.stringify(message) + "\n";
  }

  /**
   * Parse one line of server output into a JSON-RPC response.
   *
   * Returns null for blank lines or anything that is not a valid
   * JSON-RPC 2.0 response (rather than throwing), since stray
   * non-protocol output should never crash the connection.
   */
  parseResponseLine(line: string): JsonRpcResponseMessage | null {
    const trimmed = line.trim();

    if (!trimmed) {
      return null;
    }

    let parsed: unknown;

    try {
      parsed = JSON.parse(trimmed);
    } catch {
      return null;
    }

    if (
      typeof parsed !== "object" ||
      parsed === null ||
      (parsed as { jsonrpc?: unknown }).jsonrpc !== "2.0" ||
      !("id" in parsed)
    ) {
      return null;
    }

    return parsed as JsonRpcResponseMessage;
  }

  /**
   * Parse one line of server output into a server-pushed JSON-RPC
   * notification (e.g. `pearl/progress`) — the mirror image of
   * `parseResponseLine`: a valid JSON-RPC 2.0 message with a
   * `method` and no `id` at all, rather than an `id` and a
   * `result`/`error`. Mutually exclusive with `parseResponseLine` by
   * construction, so callers can try both in either order.
   *
   * Returns null for blank lines, malformed JSON, responses (which
   * `parseResponseLine` already handles), or anything else that
   * isn't a well-formed notification — never throws.
   */
  parseNotificationLine(line: string): JsonRpcNotificationMessage | null {
    const trimmed = line.trim();

    if (!trimmed) {
      return null;
    }

    let parsed: unknown;

    try {
      parsed = JSON.parse(trimmed);
    } catch {
      return null;
    }

    if (
      typeof parsed !== "object" ||
      parsed === null ||
      (parsed as { jsonrpc?: unknown }).jsonrpc !== "2.0" ||
      "id" in parsed ||
      typeof (parsed as { method?: unknown }).method !== "string"
    ) {
      return null;
    }

    const params = (parsed as { params?: unknown }).params;

    return {
      jsonrpc: "2.0",
      method: (parsed as { method: string }).method,
      params:
        typeof params === "object" && params !== null
          ? (params as Record<string, unknown>)
          : {},
    };
  }
}
