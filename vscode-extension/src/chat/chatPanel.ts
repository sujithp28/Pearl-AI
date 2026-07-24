import * as vscode from "vscode";
import { MCPConnection } from "../mcp/connection";
import { ChatController } from "./chatController";
import { getChatHtml } from "./chatHtml";

interface WebviewInboundMessage {
  type?: string;
  text?: string;
}

/**
 * Owns the single "Pearl Chat" webview panel.
 *
 * All the actual message-flow logic (sending through the MCP
 * connection, tracking history) lives in `ChatController`; this
 * class only wires that logic to the real `vscode.WebviewPanel`.
 */
export class ChatPanel {
  private static current: ChatPanel | undefined;

  private readonly panel: vscode.WebviewPanel;
  private readonly controller: ChatController;
  private readonly disposables: vscode.Disposable[] = [];

  private constructor(panel: vscode.WebviewPanel, connection: MCPConnection) {
    this.panel = panel;
    this.panel.webview.html = getChatHtml();

    this.controller = new ChatController(connection, (message) => {
      void this.panel.webview.postMessage(message);
    });

    this.panel.webview.onDidReceiveMessage(
      (message: WebviewInboundMessage) => {
        if (message?.type === "sendMessage" && typeof message.text === "string") {
          void this.controller.handleUserMessage(message.text);
        }
      },
      undefined,
      this.disposables
    );

    this.panel.onDidDispose(() => this.dispose(), undefined, this.disposables);
  }

  static createOrShow(connection: MCPConnection): ChatPanel {
    const column = vscode.window.activeTextEditor?.viewColumn;

    if (ChatPanel.current) {
      ChatPanel.current.panel.reveal(column);
      return ChatPanel.current;
    }

    const panel = vscode.window.createWebviewPanel(
      "pearlChat",
      "Pearl Chat",
      column ?? vscode.ViewColumn.One,
      { enableScripts: true, retainContextWhenHidden: true }
    );

    ChatPanel.current = new ChatPanel(panel, connection);
    return ChatPanel.current;
  }

  private dispose(): void {
    ChatPanel.current = undefined;

    for (const disposable of this.disposables) {
      disposable.dispose();
    }

    this.panel.dispose();
  }
}
