import type * as vscode from "vscode";
import { ConnectionStatus } from "./connection";

const STATUS_TEXT: Record<ConnectionStatus, string> = {
  connecting: "$(sync~spin) Pearl: Connecting...",
  connected: "$(check) Pearl: Connected",
  disconnected: "$(circle-slash) Pearl: Disconnected",
  error: "$(error) Pearl: Connection Error",
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

export class MCPStatusBar {
  constructor(private readonly item: StatusBarLike) {}

  setStatus(status: ConnectionStatus, detail?: string): void {
    this.item.text = STATUS_TEXT[status];
    this.item.tooltip = detail ?? STATUS_TEXT[status];
    this.item.show();
  }
}
