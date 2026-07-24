import { spawn } from "node:child_process";
import * as vscode from "vscode";
import { OPEN_CHAT_COMMAND_ID, openChat } from "./commands/openChat";
import { MCPConnection } from "./mcp/connection";
import { MCPStatusBar } from "./mcp/statusBar";

let connection: MCPConnection | undefined;

export function activate(context: vscode.ExtensionContext): void {
  const disposable = vscode.commands.registerCommand(
    OPEN_CHAT_COMMAND_ID,
    () => openChat((message) => vscode.window.showInformationMessage(message))
  );
  context.subscriptions.push(disposable);

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
