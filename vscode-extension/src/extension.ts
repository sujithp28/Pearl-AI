import * as vscode from "vscode";
import { OPEN_CHAT_COMMAND_ID, openChat } from "./commands/openChat";

export function activate(context: vscode.ExtensionContext): void {
  const disposable = vscode.commands.registerCommand(
    OPEN_CHAT_COMMAND_ID,
    () => openChat((message) => vscode.window.showInformationMessage(message))
  );

  context.subscriptions.push(disposable);
}

export function deactivate(): void {}
