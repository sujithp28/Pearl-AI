/**
 * Static HTML for the Pearl chat webview.
 *
 * Deliberately simple: a message list, an input box, and a send
 * button. No markdown rendering and no streaming yet — messages are
 * inserted as plain text, in full, once a complete reply arrives.
 *
 * A pure string-producing function (no `vscode` dependency), so its
 * structure can be unit tested directly.
 */

export const MESSAGES_CONTAINER_ID = "messages";
export const MESSAGE_INPUT_ID = "messageInput";
export const SEND_BUTTON_ID = "sendButton";

export function getChatHtml(): string {
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<style>
  body {
    font-family: var(--vscode-font-family);
    color: var(--vscode-foreground);
    background-color: var(--vscode-editor-background);
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    height: 100vh;
  }
  #${MESSAGES_CONTAINER_ID} {
    flex: 1;
    overflow-y: auto;
    padding: 8px;
  }
  .message {
    margin-bottom: 8px;
    white-space: pre-wrap;
    word-wrap: break-word;
  }
  .message.user { color: var(--vscode-foreground); }
  .message.assistant { color: var(--vscode-textLink-foreground); }
  .message.error { color: var(--vscode-errorForeground); }
  #inputRow {
    display: flex;
    gap: 8px;
    padding: 8px;
    border-top: 1px solid var(--vscode-panel-border, #444);
  }
  #${MESSAGE_INPUT_ID} {
    flex: 1;
    background-color: var(--vscode-input-background);
    color: var(--vscode-input-foreground);
    border: 1px solid var(--vscode-input-border, transparent);
    padding: 4px 8px;
  }
  #${SEND_BUTTON_ID} {
    background-color: var(--vscode-button-background);
    color: var(--vscode-button-foreground);
    border: none;
    padding: 4px 12px;
    cursor: pointer;
  }
</style>
</head>
<body>
  <div id="${MESSAGES_CONTAINER_ID}"></div>
  <div id="inputRow">
    <input id="${MESSAGE_INPUT_ID}" type="text" placeholder="Message Pearl..." autocomplete="off" />
    <button id="${SEND_BUTTON_ID}">Send</button>
  </div>
  <script>
    (function () {
      const vscode = acquireVsCodeApi();
      const messagesEl = document.getElementById("${MESSAGES_CONTAINER_ID}");
      const inputEl = document.getElementById("${MESSAGE_INPUT_ID}");
      const sendButton = document.getElementById("${SEND_BUTTON_ID}");

      const ROLE_PREFIX = { user: "You: ", assistant: "Pearl: ", error: "Error: " };

      function appendMessage(message) {
        const el = document.createElement("div");
        el.className = "message " + message.role;
        el.textContent = (ROLE_PREFIX[message.role] || "") + message.text;
        messagesEl.appendChild(el);
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }

      function send() {
        const text = inputEl.value;
        if (!text.trim()) {
          return;
        }
        vscode.postMessage({ type: "sendMessage", text: text });
        inputEl.value = "";
      }

      sendButton.addEventListener("click", send);
      inputEl.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
          send();
        }
      });

      window.addEventListener("message", function (event) {
        const data = event.data;
        if (data && data.type === "addMessage") {
          appendMessage(data.message);
        }
      });
    })();
  </script>
</body>
</html>`;
}
