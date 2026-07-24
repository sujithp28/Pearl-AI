import assert from "node:assert/strict";
import { test } from "node:test";
import { MCPStatusBar, StatusBarLike } from "../mcp/statusBar";

function fakeStatusBarItem(): StatusBarLike & { shown: number } {
  return {
    text: "",
    tooltip: undefined,
    shown: 0,
    show(this: StatusBarLike & { shown: number }) {
      this.shown += 1;
    },
  };
}

test("setStatus('connecting') shows a connecting message", () => {
  const item = fakeStatusBarItem();
  const statusBar = new MCPStatusBar(item);

  statusBar.setStatus("connecting");

  assert.match(item.text, /Connecting/);
  assert.equal(item.shown, 1);
});

test("setStatus('connected') shows a connected message", () => {
  const item = fakeStatusBarItem();
  const statusBar = new MCPStatusBar(item);

  statusBar.setStatus("connected");

  assert.match(item.text, /Connected/);
});

test("setStatus('error') includes the provided detail in the tooltip", () => {
  const item = fakeStatusBarItem();
  const statusBar = new MCPStatusBar(item);

  statusBar.setStatus("error", "spawn ENOENT");

  assert.match(item.text, /Connection Error/);
  assert.match(String(item.tooltip), /Connection Error/);
  assert.match(String(item.tooltip), /spawn ENOENT/);
});

test("setStatus('disconnected') has a tooltip even with no detail", () => {
  const item = fakeStatusBarItem();
  const statusBar = new MCPStatusBar(item);

  statusBar.setStatus("disconnected");

  assert.match(item.text, /Disconnected/);
  assert.match(String(item.tooltip), /Disconnected/);
});

test("setContext adds provider and workspace to the status text", () => {
  const item = fakeStatusBarItem();
  const statusBar = new MCPStatusBar(item);

  statusBar.setContext({ provider: "omniroute", workspace: "pearl-agent" });
  statusBar.setStatus("connected");

  assert.match(item.text, /Connected/);
  assert.match(item.text, /omniroute/);
  assert.match(item.text, /pearl-agent/);
});

test("setContext values also appear in the tooltip", () => {
  const item = fakeStatusBarItem();
  const statusBar = new MCPStatusBar(item);

  statusBar.setContext({ provider: "claude", workspace: "my-project" });
  statusBar.setStatus("connected");

  assert.match(String(item.tooltip), /Provider: claude/);
  assert.match(String(item.tooltip), /Workspace: my-project/);
});

test("without setContext, no provider/workspace segments are shown", () => {
  const item = fakeStatusBarItem();
  const statusBar = new MCPStatusBar(item);

  statusBar.setStatus("connected");

  assert.doesNotMatch(String(item.tooltip), /Provider:/);
  assert.doesNotMatch(String(item.tooltip), /Workspace:/);
});
