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

  /**
   * Subscribe to server-pushed notifications (e.g. `pearl/progress`)
   * for `method`, returning an unsubscribe function. Optional: only
   * the real `MCPConnection` supports this; fakes used in tests that
   * don't need it can omit it entirely.
   */
  onNotification?(
    method: string,
    handler: (params: Record<string, unknown>) => void
  ): () => void;
}
