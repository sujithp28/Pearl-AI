import * as vscode from "vscode";
import { MCPConnection } from "../mcp/connection";
import { CheckpointTreeNode } from "./checkpointTree";
import { CheckpointTreeState } from "./checkpointTreeState";

export class CheckpointTreeItem extends vscode.TreeItem {
  constructor(public readonly node: CheckpointTreeNode) {
    super(node.label, vscode.TreeItemCollapsibleState.None);

    this.description = node.description;
    this.tooltip = node.tooltip ?? node.label;

    if (node.checkpoint) {
      // Lets view/item/context menu entries (restore/rename/delete)
      // target only real checkpoint rows, never the "No checkpoints
      // yet." / "Failed to load..." placeholder rows.
      this.contextValue = "pearlCheckpoint";
    }
  }
}

/**
 * The "Checkpoints" tree view's data provider. All the actual
 * fetch/refresh logic lives in `CheckpointTreeState`; this class only
 * adapts that to the real `vscode.TreeDataProvider` interface —
 * mirrors `../memory/memoryTreeProvider.ts`.
 */
export class CheckpointTreeProvider
  implements vscode.TreeDataProvider<CheckpointTreeItem>
{
  private readonly _onDidChangeTreeData = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  private readonly state: CheckpointTreeState;

  constructor(connection: MCPConnection) {
    this.state = new CheckpointTreeState(connection);
    this.state.onChange = () => this._onDidChangeTreeData.fire();
  }

  refresh(): Promise<void> {
    return this.state.refresh();
  }

  getTreeItem(element: CheckpointTreeItem): vscode.TreeItem {
    return element;
  }

  getChildren(): CheckpointTreeItem[] {
    return this.state.getNodes().map((node) => new CheckpointTreeItem(node));
  }
}
