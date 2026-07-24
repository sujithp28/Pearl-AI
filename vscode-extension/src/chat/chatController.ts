/**
 * Drives the chat webview's message flow: takes user input, sends
 * it through the existing MCP connection (via `sendChatMessage`),
 * and reports the resulting messages back via an injected `post`
 * callback.
 *
 * Deliberately independent of the real `vscode.Webview` /
 * `MCPConnection` types (structural `RequestSender` only), so this
 * — the actual behavior worth testing — is unit testable without a
 * webview or a real MCP connection.
 */

import { RequestSender, sendChatMessage } from "../mcp/chatClient";

export type ChatRole = "user" | "assistant" | "error";

export interface ChatMessage {
  role: ChatRole;
  text: string;
}

export type PostToWebview = (message: {
  type: "addMessage";
  message: ChatMessage;
}) => void;

export class ChatController {
  private readonly history: ChatMessage[] = [];

  constructor(
    private readonly connection: RequestSender,
    private readonly post: PostToWebview
  ) {}

  getHistory(): readonly ChatMessage[] {
    return this.history;
  }

  async handleUserMessage(text: string): Promise<void> {
    const trimmed = text.trim();

    if (!trimmed) {
      return;
    }

    this.addAndPost({ role: "user", text: trimmed });

    try {
      const reply = await sendChatMessage(this.connection, trimmed);
      this.addAndPost({ role: "assistant", text: reply });
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      this.addAndPost({
        role: "error",
        text: `Failed to reach Pearl: ${detail}`,
      });
    }
  }

  private addAndPost(message: ChatMessage): void {
    this.history.push(message);
    this.post({ type: "addMessage", message });
  }
}
