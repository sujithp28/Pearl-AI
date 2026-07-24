/**
 * Pure formatting: turns a `MemorySnapshot` (from `memoryClient.ts`)
 * into a simple, renderable tree — four categories (Conversation,
 * Tasks, Execution History, Project Facts), each with one leaf per
 * entry. No `vscode` dependency, so this is directly unit testable
 * independent of any tree view widget.
 */

import { MemorySnapshot } from "./memoryClient";

export interface MemoryTreeNode {
  label: string;
  description?: string;
  tooltip?: string;
  children?: MemoryTreeNode[];
}

const MAX_LABEL_LENGTH = 80;

function truncate(text: string, maxLength = MAX_LABEL_LENGTH): string {
  if (text.length <= maxLength) {
    return text;
  }

  return text.slice(0, maxLength - 1) + "…";
}

function formatResult(result: unknown): string {
  if (result === null || result === undefined) {
    return "";
  }

  return typeof result === "string" ? result : JSON.stringify(result);
}

export function buildMemoryTree(snapshot: MemorySnapshot): MemoryTreeNode[] {
  const conversation: MemoryTreeNode = {
    label: `Conversation (${snapshot.conversation.length})`,
    children: snapshot.conversation.map((turn) => ({
      label: `${turn.role}: ${truncate(turn.content)}`,
      tooltip: turn.content,
    })),
  };

  const tasks: MemoryTreeNode = {
    label: `Tasks (${snapshot.tasks.length})`,
    children: snapshot.tasks.map((task) => ({
      label: truncate(task.description),
      description: task.status,
      tooltip: `${task.id} — ${task.status}`,
    })),
  };

  const executionHistory: MemoryTreeNode = {
    label: `Execution History (${snapshot.execution_history.length})`,
    children: snapshot.execution_history.map((record) => ({
      label: record.tool_name,
      description: record.error ? "failed" : "ok",
      tooltip: record.error ?? truncate(formatResult(record.result), 500),
    })),
  };

  const projectEntries = Object.entries(snapshot.project);

  const projectFacts: MemoryTreeNode = {
    label: `Project Facts (${projectEntries.length})`,
    children: projectEntries.map(([key, value]) => ({
      label: key,
      description: truncate(formatResult(value)),
      tooltip: `${key}: ${formatResult(value)}`,
    })),
  };

  return [conversation, tasks, executionHistory, projectFacts];
}
