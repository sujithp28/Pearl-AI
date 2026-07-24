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

test("setStatus('error') includes the provided detail as the tooltip", () => {
  const item = fakeStatusBarItem();
  const statusBar = new MCPStatusBar(item);

  statusBar.setStatus("error", "spawn ENOENT");

  assert.match(item.text, /Connection Error/);
  assert.equal(item.tooltip, "spawn ENOENT");
});

test("setStatus('disconnected') falls back to the status text as tooltip", () => {
  const item = fakeStatusBarItem();
  const statusBar = new MCPStatusBar(item);

  statusBar.setStatus("disconnected");

  assert.match(item.text, /Disconnected/);
  assert.equal(item.tooltip, item.text);
});
