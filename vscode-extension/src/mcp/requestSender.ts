/**
 * The minimal request-sending shape shared by every MCP client
 * module (`chatClient`, `planClient`, `toolCallClient`).
 *
 * Depending on this structural interface (rather than the concrete
 * `MCPConnection` class) lets each client module be unit tested
 * with a plain fake sender instead of a real connection/process.
 */
export interface RequestSender {
  sendRequest(
    method: string,
    params?: Record<string, unknown>,
    timeoutMs?: number
  ): Promise<unknown>;
}
