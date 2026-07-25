/**
 * Turns a raw connection/transport error (from `MCPConnection`/
 * `RequestSender`) into a short, actionable message for the chat UI.
 *
 * Never surfaces Pearl's internal JSON-RPC method names (e.g.
 * `pearl/chat`, `pearl/planOnly`) to the user — those are wire
 * protocol details that mean nothing outside Pearl's own source and
 * only make an error message look like a bug report, not guidance.
 * The original message (with the method name) is still logged to
 * the console for anyone debugging the extension itself.
 */

const TIMEOUT_PATTERN = /^Timed out waiting for response to '.+'\.$/;

const NOT_CONNECTED_MESSAGE = "Not connected to Pearl MCP server.";

export function describeConnectionError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);

  if (TIMEOUT_PATTERN.test(message)) {
    return (
      "Pearl is taking longer than expected to respond. This usually " +
      "means the model is still generating a reply — wait a moment and " +
      "try again, or check that your configured LLM provider (e.g. " +
      "Ollama) is running and reachable."
    );
  }

  if (message === NOT_CONNECTED_MESSAGE) {
    return (
      "Pearl isn't connected right now. Check the Pearl status in the " +
      "VS Code status bar, or try restarting the extension."
    );
  }

  return message;
}
