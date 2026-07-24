/**
 * Fetches Memory through the existing MCP connection and holds the
 * resulting tree, independent of any real `vscode.TreeDataProvider`.
 *
 * Depends only on the structural `RequestSender` and exposes a
 * plain `onChange` callback (fired after every refresh, success or
 * failure), so the refresh behavior itself is unit testable without
 * a real tree view widget.
 */

import { RequestSender } from "../mcp/requestSender";
import { fetchMemory } from "./memoryClient";
import { MemoryTreeNode, buildMemoryTree } from "./memoryTree";

export class MemoryTreeState {
  private roots: MemoryTreeNode[] = [];
  private lastError: string | undefined;

  public onChange: (() => void) | undefined;

  constructor(private readonly connection: RequestSender) {}

  getRoots(): MemoryTreeNode[] {
    return this.roots;
  }

  getLastError(): string | undefined {
    return this.lastError;
  }

  async refresh(): Promise<void> {
    try {
      const snapshot = await fetchMemory(this.connection);
      this.roots = buildMemoryTree(snapshot);
      this.lastError = undefined;
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      this.lastError = detail;
      this.roots = [{ label: `Failed to load memory: ${detail}` }];
    }

    this.onChange?.();
  }
}
