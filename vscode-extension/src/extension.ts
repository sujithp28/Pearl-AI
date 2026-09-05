import { spawn } from "node:child_process";
import * as vscode from "vscode";
import {
  runCreateCheckpointCommand,
  runDeleteCheckpointCommand,
  runRenameCheckpointCommand,
  runRestoreCheckpointCommand,
} from "./checkpoints/checkpointCommands";
import {
  CheckpointTreeItem,
  CheckpointTreeProvider,
} from "./checkpoints/checkpointTreeProvider";
import { ChatPanel } from "./chat/chatPanel";
import { MCPConnection } from "./mcp/connection";
import { PearlInlineCompletionProvider } from "./completion/inlineCompletionProvider";
import { MCPStatusBar } from "./mcp/statusBar";
import { MemoryTreeProvider } from "./memory/memoryTreeProvider";

export const OPEN_CHAT_COMMAND_ID = "pearl.openChat";
export const REFRESH_MEMORY_COMMAND_ID = "pearl.refreshMemory";
export const MEMORY_VIEW_ID = "pearlMemory";
export const CHECKPOINTS_VIEW_ID = "pearlCheckpoints";
export const CREATE_CHECKPOINT_COMMAND_ID = "pearl.createCheckpoint";
export const REFRESH_CHECKPOINTS_COMMAND_ID = "pearl.refreshCheckpoints";
export const RESTORE_CHECKPOINT_COMMAND_ID = "pearl.restoreCheckpoint";
export const DELETE_CHECKPOINT_COMMAND_ID = "pearl.deleteCheckpoint";
export const RENAME_CHECKPOINT_COMMAND_ID = "pearl.renameCheckpoint";

let connection: MCPConnection | undefined;

export function activate(context: vscode.ExtensionContext): void {
  const outputChannel = vscode.window.createOutputChannel("Pearl");
  context.subscriptions.push(outputChannel);

  const statusBarItem = createStatusBarItem();
  context.subscriptions.push(statusBarItem);

  const statusBar = new MCPStatusBar(statusBarItem);
  statusBar.setContext({
    provider: getConfiguredProvider(),
    workspace: getWorkspaceName(),
  });

  connection = new MCPConnection({
    command: getPythonCommand(),
    args: ["-m", "src.mcp"],
    cwd: getWorkspaceRoot(),
    spawnFn: (command, args, options) =>
      spawn(command, args, { cwd: options.cwd }),
    // Cold-starting Pearl's Python process (heavy imports like torch)
    // can take well over the library default of 5s.
    initializeTimeoutMs: 60000,
  });

  const memoryTreeProvider = new MemoryTreeProvider(connection);
  context.subscriptions.push(
    vscode.window.registerTreeDataProvider(
      MEMORY_VIEW_ID,
      memoryTreeProvider
    )
  );

  const checkpointTreeProvider = new CheckpointTreeProvider(connection);
  context.subscriptions.push(
    vscode.window.registerTreeDataProvider(
      CHECKPOINTS_VIEW_ID,
      checkpointTreeProvider
    )
  );

  connection.onStderr = (chunk) => {
    outputChannel.append(chunk);
  };

  connection.onStatusChange = (status, detail) => {
    statusBar.setStatus(status, detail);

    if (status === "connected") {
      void memoryTreeProvider.refresh();
      void checkpointTreeProvider.refresh();
    }
  };

  connection.start();

  // Inline completion. Registered for every language: Pearl's completion
  // service is language-aware via `languageId` rather than needing a
  // separate registration per language, and a language with no specific
  // handling still gets a useful generic continuation.
  const inlineProvider = new PearlInlineCompletionProvider(connection, () =>
    vscode.workspace
      .getConfiguration("pearl")
      .get<boolean>("inlineCompletion.enabled", true)
  );
  context.subscriptions.push(
    vscode.languages.registerInlineCompletionItemProvider(
      { pattern: "**" },
      inlineProvider
    ),
    inlineProvider
  );

  const openChatCommand = vscode.commands.registerCommand(
    OPEN_CHAT_COMMAND_ID,
    () => {
      if (!connection) {
        vscode.window.showErrorMessage("Pearl is not initialized yet.");
        return;
      }

      ChatPanel.createOrShow(connection);
    }
  );
  context.subscriptions.push(openChatCommand);

  const refreshMemoryCommand = vscode.commands.registerCommand(
    REFRESH_MEMORY_COMMAND_ID,
    () => {
      void memoryTreeProvider.refresh();
    }
  );
  context.subscriptions.push(refreshMemoryCommand);

  const refreshCheckpoints = () => checkpointTreeProvider.refresh();

  context.subscriptions.push(
    vscode.commands.registerCommand(REFRESH_CHECKPOINTS_COMMAND_ID, () => {
      void refreshCheckpoints();
    }),
    vscode.commands.registerCommand(CREATE_CHECKPOINT_COMMAND_ID, () => {
      if (!connection) {
        vscode.window.showErrorMessage("Pearl is not initialized yet.");
        return;
      }

      void runCreateCheckpointCommand(connection, refreshCheckpoints);
    }),
    vscode.commands.registerCommand(
      RESTORE_CHECKPOINT_COMMAND_ID,
      (item: CheckpointTreeItem) => {
        if (!connection) {
          vscode.window.showErrorMessage("Pearl is not initialized yet.");
          return;
        }

        void runRestoreCheckpointCommand(connection, refreshCheckpoints, item);
      }
    ),
    vscode.commands.registerCommand(
      DELETE_CHECKPOINT_COMMAND_ID,
      (item: CheckpointTreeItem) => {
        if (!connection) {
          vscode.window.showErrorMessage("Pearl is not initialized yet.");
          return;
        }

        void runDeleteCheckpointCommand(connection, refreshCheckpoints, item);
      }
    ),
    vscode.commands.registerCommand(
      RENAME_CHECKPOINT_COMMAND_ID,
      (item: CheckpointTreeItem) => {
        if (!connection) {
          vscode.window.showErrorMessage("Pearl is not initialized yet.");
          return;
        }

        void runRenameCheckpointCommand(connection, refreshCheckpoints, item);
      }
    )
  );

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

function getWorkspaceName(): string | undefined {
  return vscode.workspace.workspaceFolders?.[0]?.name;
}

function getConfiguredProvider(): string | undefined {
  const provider = vscode.workspace
    .getConfiguration("pearl")
    .get<string>("provider", "");

  return provider.trim() ? provider.trim() : undefined;
}

function createStatusBarItem(): vscode.StatusBarItem {
  return vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Left,
    100
  );
}
