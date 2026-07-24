import { spawn } from "node:child_process";
import * as vscode from "vscode";
import { ChatPanel } from "./chat/chatPanel";
import { MCPConnection } from "./mcp/connection";
import { MCPStatusBar } from "./mcp/statusBar";

export const OPEN_CHAT_COMMAND_ID = "pearl.openChat";

let connection: MCPConnection | undefined;

export function activate(context: vscode.ExtensionContext): void {
  const statusBarItem = createStatusBarItem();
  context.subscriptions.push(statusBarItem);

  const statusBar = new MCPStatusBar(statusBarItem);

  connection = new MCPConnection({
    command: getPythonCommand(),
    args: ["-m", "src.mcp"],
    cwd: getWorkspaceRoot(),
    spawnFn: (command, args, options) =>
      spawn(command, args, { cwd: options.cwd }),
  });

  connection.onStatusChange = (status, detail) =>
    statusBar.setStatus(status, detail);

  connection.start();

  const disposable = vscode.commands.registerCommand(
    OPEN_CHAT_COMMAND_ID,
    () => {
      if (!connection) {
        vscode.window.showErrorMessage("Pearl is not initialized yet.");
        return;
      }

      ChatPanel.createOrShow(connection);
    }
  );
  context.subscriptions.push(disposable);

  context.subscriptions.push({ dispose: () => connection?.stop() });
}

export function deactivate(): void {
  connection?.stop();
}

function getPythonCommand(): string {
  return vscode.workspace
    .getConfiguration("pearl")
    .get<string>("pythonPath", "python3");
}

function getWorkspaceRoot(): string | undefined {
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
}

function createStatusBarItem(): vscode.StatusBarItem {
  return vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Left,
    100
  );
}
