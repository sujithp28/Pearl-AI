import assert from "node:assert/strict";
import { test } from "node:test";
import { buildMemoryTree } from "../memory/memoryTree";
import { MemorySnapshot } from "../memory/memoryClient";

function emptySnapshot(): MemorySnapshot {
  return { conversation: [], tasks: [], project: {}, execution_history: [] };
}

test("buildMemoryTree produces four categories for an empty snapshot", () => {
  const tree = buildMemoryTree(emptySnapshot());

  assert.deepEqual(
    tree.map((node) => node.label),
    [
      "Conversation (0)",
      "Tasks (0)",
      "Execution History (0)",
      "Project Facts (0)",
    ]
  );
  tree.forEach((node) => assert.deepEqual(node.children, []));
});

test("buildMemoryTree renders conversation turns as role: content", () => {
  const snapshot = emptySnapshot();
  snapshot.conversation = [
    { role: "user", content: "hi", timestamp: "t1" },
    { role: "agent", content: "hello there", timestamp: "t2" },
  ];

  const [conversation] = buildMemoryTree(snapshot);

  assert.equal(conversation.label, "Conversation (2)");
  assert.deepEqual(
    conversation.children?.map((c) => c.label),
    ["user: hi", "agent: hello there"]
  );
  assert.equal(conversation.children?.[0].tooltip, "hi");
});

test("buildMemoryTree truncates long conversation content in the label but keeps it in the tooltip", () => {
  const longContent = "x".repeat(200);
  const snapshot = emptySnapshot();
  snapshot.conversation = [
    { role: "user", content: longContent, timestamp: "t1" },
  ];

  const [conversation] = buildMemoryTree(snapshot);
  const [turn] = conversation.children ?? [];

  assert.ok(turn.label.length < longContent.length);
  assert.ok(turn.label.endsWith("…"));
  assert.equal(turn.tooltip, longContent);
});

test("buildMemoryTree renders tasks with description as label and status as description", () => {
  const snapshot = emptySnapshot();
  snapshot.tasks = [
    {
      id: "task-1",
      description: "add two numbers",
      status: "completed",
      created_at: "c1",
      updated_at: "u1",
    },
  ];

  const [, tasks] = buildMemoryTree(snapshot);

  assert.equal(tasks.label, "Tasks (1)");
  assert.equal(tasks.children?.[0].label, "add two numbers");
  assert.equal(tasks.children?.[0].description, "completed");
  assert.equal(tasks.children?.[0].tooltip, "task-1 — completed");
});

test("buildMemoryTree renders execution history with tool name and ok/failed status", () => {
  const snapshot = emptySnapshot();
  snapshot.execution_history = [
    {
      tool_name: "add",
      kwargs: { a: 1, b: 2 },
      result: 3,
      error: null,
      timestamp: "t1",
    },
    {
      tool_name: "boom",
      kwargs: {},
      result: null,
      error: "kaboom",
      timestamp: "t2",
    },
  ];

  const [, , executionHistory] = buildMemoryTree(snapshot);

  assert.equal(executionHistory.label, "Execution History (2)");
  assert.equal(executionHistory.children?.[0].label, "add");
  assert.equal(executionHistory.children?.[0].description, "ok");
  assert.equal(executionHistory.children?.[0].tooltip, "3");

  assert.equal(executionHistory.children?.[1].label, "boom");
  assert.equal(executionHistory.children?.[1].description, "failed");
  assert.equal(executionHistory.children?.[1].tooltip, "kaboom");
});

test("buildMemoryTree renders project facts as key/value pairs", () => {
  const snapshot = emptySnapshot();
  snapshot.project = { language: "python", framework: "none" };

  const [, , , projectFacts] = buildMemoryTree(snapshot);

  assert.equal(projectFacts.label, "Project Facts (2)");
  assert.deepEqual(
    projectFacts.children?.map((c) => c.label),
    ["language", "framework"]
  );
  assert.equal(projectFacts.children?.[0].description, "python");
});
