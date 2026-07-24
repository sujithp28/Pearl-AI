import assert from "node:assert/strict";
import { test } from "node:test";
import { ChatController, ChatMessage } from "../chat/chatController";
import { RequestSender } from "../mcp/chatClient";

function collectingPost(): {
  messages: ChatMessage[];
  post: (m: { type: "addMessage"; message: ChatMessage }) => void;
} {
  const messages: ChatMessage[] = [];

  return {
    messages,
    post: (m) => messages.push(m.message),
  };
}

test("handleUserMessage posts the user message immediately, then the assistant reply", async () => {
  const sender: RequestSender = {
    sendRequest: async () => ({ message: "reply text" }),
  };
  const { messages, post } = collectingPost();
  const controller = new ChatController(sender, post);

  await controller.handleUserMessage("hello");

  assert.deepEqual(messages, [
    { role: "user", text: "hello" },
    { role: "assistant", text: "reply text" },
  ]);
  assert.deepEqual([...controller.getHistory()], messages);
});

test("handleUserMessage posts an error message when the connection call fails", async () => {
  const sender: RequestSender = {
    sendRequest: async () => {
      throw new Error("Not connected to Pearl MCP server.");
    },
  };
  const { messages, post } = collectingPost();
  const controller = new ChatController(sender, post);

  await controller.handleUserMessage("hello");

  assert.equal(messages.length, 2);
  assert.equal(messages[0].role, "user");
  assert.equal(messages[1].role, "error");
  assert.match(messages[1].text, /Not connected to Pearl MCP server\./);
});

test("handleUserMessage ignores blank input", async () => {
  let called = false;
  const sender: RequestSender = {
    sendRequest: async () => {
      called = true;
      return { message: "x" };
    },
  };
  const { messages, post } = collectingPost();
  const controller = new ChatController(sender, post);

  await controller.handleUserMessage("   ");

  assert.equal(called, false);
  assert.deepEqual(messages, []);
});

test("handleUserMessage trims whitespace from the user message", async () => {
  const sender: RequestSender = {
    sendRequest: async () => ({ message: "ok" }),
  };
  const { messages, post } = collectingPost();
  const controller = new ChatController(sender, post);

  await controller.handleUserMessage("  hi there  ");

  assert.equal(messages[0].text, "hi there");
});
