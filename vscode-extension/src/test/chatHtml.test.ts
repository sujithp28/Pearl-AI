import assert from "node:assert/strict";
import { test } from "node:test";
import {
  MESSAGES_CONTAINER_ID,
  MESSAGE_INPUT_ID,
  SEND_BUTTON_ID,
  getChatHtml,
} from "../chat/chatHtml";

test("getChatHtml includes a message list, input box, and send button", () => {
  const html = getChatHtml();

  assert.match(html, new RegExp(`id="${MESSAGES_CONTAINER_ID}"`));
  assert.match(html, new RegExp(`id="${MESSAGE_INPUT_ID}"`));
  assert.match(html, new RegExp(`id="${SEND_BUTTON_ID}"`));
});

test("getChatHtml wires postMessage-based communication with the extension host", () => {
  const html = getChatHtml();

  assert.match(html, /acquireVsCodeApi/);
  assert.match(html, /vscode\.postMessage/);
  assert.match(html, /addEventListener\("message"/);
});

test("getChatHtml does not reference markdown rendering or streaming libraries", () => {
  const html = getChatHtml();

  assert.doesNotMatch(html.toLowerCase(), /marked|markdown-it|eventsource|streaming/);
});

test("getChatHtml renders plan steps with a step number and tool name", () => {
  const html = getChatHtml();

  assert.match(html, /function appendPlan/);
  assert.match(html, /"Step " \+ step\.index \+ ": " \+ step\.tool/);
});

test("getChatHtml collapses long arguments using a plain <details> element", () => {
  const html = getChatHtml();

  assert.match(html, /step\.collapsed/);
  assert.match(html, /createElement\("details"\)/);
  assert.match(html, /createElement\("summary"\)/);
});

test("getChatHtml handles showPlan messages and offers Execute Plan / Cancel actions", () => {
  const html = getChatHtml();

  assert.match(html, /data\.type === "showPlan"/);
  assert.match(html, /"Execute Plan"/);
  assert.match(html, /cancelButton\.textContent = "Cancel"/);
  assert.match(html, /type: "planDecision"/);
});

test("getChatHtml disables the plan actions once a decision is made", () => {
  const html = getChatHtml();

  assert.match(html, /executeButton\.disabled = true/);
  assert.match(html, /cancelButton\.disabled = true/);
});
