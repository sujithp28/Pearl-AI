import type * as vscode from "vscode";
import { ConnectionStatus } from "./connection";

const STATUS_TEXT: Record<ConnectionStatus, string> = {
  connecting: "$(sync~spin) Pearl: Connecting...",
  connected: "$(check) Pearl: Connected",
  disconnected: "$(circle-slash) Pearl: Disconnected",
  error: "$(error) Pearl: Connection Error",
};

const STATUS_LABEL: Record<ConnectionStatus, string> = {
  connecting: "Connecting",
  connected: "Connected",
  disconnected: "Disconnected",
  error: "Connection Error",
};

/**
 * The subset of `vscode.StatusBarItem` this wrapper needs, so it
 * can be unit tested with a plain fake object instead of a real
 * status bar item. Only the `vscode` *type* is referenced here
 * (`import type`), so this module has no runtime dependency on the
 * `vscode` module and can be imported outside the extension host.
 */
export interface StatusBarLike {
  text: string;
  tooltip?: string | vscode.MarkdownString;
  show(): void;
}

/**
 * Static, rarely-changing context shown alongside connection status:
 * which LLM provider Pearl is configured to use, and which workspace
 * is open. Both are display-only — read from extension settings /
 * the VS Code workspace API, never queried from the MCP server.
 */
export interface StatusBarContext {
  provider?: string;
  workspace?: string;
}

export class MCPStatusBar {
  private context: StatusBarContext = {};

  constructor(private readonly item: StatusBarLike) {}

  setContext(context: StatusBarContext): void {
    this.context = context;
  }

  setStatus(status: ConnectionStatus, detail?: string): void {
    const segments = [STATUS_TEXT[status]];

    if (this.context.provider) {
      segments.push(this.context.provider);
    }

    if (this.context.workspace) {
      segments.push(this.context.workspace);
    }

    this.item.text = segments.join("  ·  ");
    this.item.tooltip = this.buildTooltip(status, detail);
    this.item.show();
  }

  private buildTooltip(status: ConnectionStatus, detail?: string): string {
    const lines = [`Pearl: ${STATUS_LABEL[status]}`];

    if (detail) {
      lines.push(detail);
    }

    if (this.context.provider) {
      lines.push(`Provider: ${this.context.provider}`);
    }

    if (this.context.workspace) {
      lines.push(`Workspace: ${this.context.workspace}`);
    }

    return lines.join("\n");
  }
}
