import * as vscode from "vscode";
import { MCPConnection } from "../mcp/connection";
import { ChatController } from "./chatController";
import { getChatHtml } from "./chatHtml";
import { PatchDecision, WebviewPatchApprover } from "./patchApproval";
import { PlanDecision, WebviewPlanApprover } from "./planApproval";
import { showToolApprovalDialog } from "./vscodeToolApprover";

interface WebviewInboundMessage {
  type?: string;
  text?: string;
  decision?: string;
}

function isPlanDecision(value: unknown): value is PlanDecision {
  return value === "execute" || value === "cancel";
}

function isPatchDecision(value: unknown): value is PatchDecision {
  return value === "approve" || value === "reject";
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
  private readonly planApprover: WebviewPlanApprover;
  private readonly patchApprover: WebviewPatchApprover;
  private readonly disposables: vscode.Disposable[] = [];

  private constructor(panel: vscode.WebviewPanel, connection: MCPConnection) {
    this.panel = panel;
    this.panel.webview.html = getChatHtml();

    this.planApprover = new WebviewPlanApprover((message) => {
      void this.panel.webview.postMessage(message);
    });

    this.patchApprover = new WebviewPatchApprover((message) => {
      void this.panel.webview.postMessage(message);
    });

    this.controller = new ChatController(
      connection,
      (message) => {
        void this.panel.webview.postMessage(message);
      },
      showToolApprovalDialog,
      this.planApprover.requestApproval,
      this.patchApprover.requestApproval
    );

    this.panel.webview.onDidReceiveMessage(
      (message: WebviewInboundMessage) => {
        if (message?.type === "sendMessage" && typeof message.text === "string") {
          void this.controller.handleUserMessage(message.text);
        } else if (
          message?.type === "planDecision" &&
          isPlanDecision(message.decision)
        ) {
          this.planApprover.resolveDecision(message.decision);
        } else if (
          message?.type === "patchDecision" &&
          isPatchDecision(message.decision)
        ) {
          this.patchApprover.resolveDecision(message.decision);
        } else if (
          message?.type === "copyDiff" &&
          typeof message.text === "string"
        ) {
          void vscode.env.clipboard.writeText(message.text);
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
