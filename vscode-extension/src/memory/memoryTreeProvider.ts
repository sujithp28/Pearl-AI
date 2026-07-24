import * as vscode from "vscode";
import { MCPConnection } from "../mcp/connection";
import { MemoryTreeNode } from "./memoryTree";
import { MemoryTreeState } from "./memoryTreeState";

export class MemoryTreeItem extends vscode.TreeItem {
  constructor(public readonly node: MemoryTreeNode) {
    super(
      node.label,
      node.children && node.children.length > 0
        ? vscode.TreeItemCollapsibleState.Collapsed
        : vscode.TreeItemCollapsibleState.None
    );

    this.description = node.description;
    this.tooltip = node.tooltip ?? node.label;
  }
}

/**
 * The "Memory" tree view's data provider. All the actual
 * fetch/refresh logic lives in `MemoryTreeState`; this class only
 * adapts that to the real `vscode.TreeDataProvider` interface.
 */
export class MemoryTreeProvider
  implements vscode.TreeDataProvider<MemoryTreeItem>
{
  private readonly _onDidChangeTreeData = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  private readonly state: MemoryTreeState;

  constructor(connection: MCPConnection) {
    this.state = new MemoryTreeState(connection);
    this.state.onChange = () => this._onDidChangeTreeData.fire();
  }

  refresh(): Promise<void> {
    return this.state.refresh();
  }

  getTreeItem(element: MemoryTreeItem): vscode.TreeItem {
    return element;
  }

  getChildren(element?: MemoryTreeItem): MemoryTreeItem[] {
    const nodes = element ? element.node.children ?? [] : this.state.getRoots();
    return nodes.map((node) => new MemoryTreeItem(node));
  }
}
