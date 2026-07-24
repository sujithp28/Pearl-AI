/**
 * "Pearl: Open Chat" command.
 *
 * Scaffold only: this does not connect to MCP and does not
 * implement an actual chat interface yet. `showMessage` is
 * injected so this logic can be unit tested without the VS Code
 * API.
 */

export const OPEN_CHAT_COMMAND_ID = "pearl.openChat";

export const PEARL_CONNECTED_MESSAGE = "Pearl is connected.";

export function openChat(showMessage: (message: string) => void): void {
  showMessage(PEARL_CONNECTED_MESSAGE);
}
