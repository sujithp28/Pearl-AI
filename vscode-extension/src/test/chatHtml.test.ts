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
