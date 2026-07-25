/**
 * Fetches checkpoints through the existing MCP connection and holds
 * the resulting tree, independent of any real `vscode.TreeDataProvider`
 * — mirrors `../memory/memoryTreeState.ts`.
 */

import { listCheckpoints } from "../mcp/checkpointClient";
import { RequestSender } from "../mcp/requestSender";
import { CheckpointTreeNode, buildCheckpointTree } from "./checkpointTree";

export class CheckpointTreeState {
  private nodes: CheckpointTreeNode[] = [];
  private lastError: string | undefined;

  public onChange: (() => void) | undefined;

  constructor(private readonly connection: RequestSender) {}

  getNodes(): CheckpointTreeNode[] {
    return this.nodes;
  }

  getLastError(): string | undefined {
    return this.lastError;
  }

  async refresh(): Promise<void> {
    try {
      const checkpoints = await listCheckpoints(this.connection);
      this.nodes = buildCheckpointTree(checkpoints);
      this.lastError = undefined;
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      this.lastError = detail;
      this.nodes = [{ label: `Failed to load checkpoints: ${detail}` }];
    }

    this.onChange?.();
  }
}
