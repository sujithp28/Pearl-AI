/**
 * Static HTML for the Pearl chat webview.
 *
 * Deliberately simple: a message list, an input box, a send button,
 * and (before any tool approval) a plan preview with "Execute Plan"
 * / "Cancel" actions. No markdown rendering and no streaming yet —
 * messages are inserted as plain text, in full, once a complete
 * reply arrives. Long tool arguments in the plan preview are
 * collapsed by default (a plain `<details>` element — the
 * "collapsed if long" decision itself is made host-side, in
 * `planFormatting.ts`; this script only renders what it's given).
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
  .plan {
    border: 1px solid var(--vscode-panel-border, #444);
    border-radius: 4px;
    padding: 8px;
    margin-bottom: 8px;
  }
  .plan-header {
    font-weight: bold;
    margin-bottom: 4px;
  }
  .plan-step {
    margin: 4px 0;
  }
  .plan-step-title {
    font-weight: bold;
  }
  .plan pre {
    white-space: pre-wrap;
    word-wrap: break-word;
    margin: 4px 0 0 0;
  }
  .plan-actions {
    margin-top: 8px;
    display: flex;
    gap: 8px;
  }
  .plan-actions button {
    background-color: var(--vscode-button-background);
    color: var(--vscode-button-foreground);
    border: none;
    padding: 4px 12px;
    cursor: pointer;
  }
  .plan-actions button:disabled {
    opacity: 0.6;
    cursor: default;
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

      function appendPlan(steps) {
        const container = document.createElement("div");
        container.className = "plan";

        const header = document.createElement("div");
        header.className = "plan-header";
        header.textContent = "Proposed plan:";
        container.appendChild(header);

        steps.forEach(function (step) {
          const stepEl = document.createElement("div");
          stepEl.className = "plan-step";

          const title = document.createElement("div");
          title.className = "plan-step-title";
          title.textContent = "Step " + step.index + ": " + step.tool;
          stepEl.appendChild(title);

          if (step.collapsed) {
            const details = document.createElement("details");
            const summary = document.createElement("summary");
            summary.textContent = "Arguments";
            details.appendChild(summary);
            const pre = document.createElement("pre");
            pre.textContent = step.argumentsText;
            details.appendChild(pre);
            stepEl.appendChild(details);
          } else {
            const pre = document.createElement("pre");
            pre.textContent = step.argumentsText;
            stepEl.appendChild(pre);
          }

          container.appendChild(stepEl);
        });

        const actions = document.createElement("div");
        actions.className = "plan-actions";

        const executeButton = document.createElement("button");
        executeButton.textContent = "Execute Plan";
        const cancelButton = document.createElement("button");
        cancelButton.textContent = "Cancel";

        function decide(decision) {
          executeButton.disabled = true;
          cancelButton.disabled = true;
          vscode.postMessage({ type: "planDecision", decision: decision });
        }

        executeButton.addEventListener("click", function () {
          decide("execute");
        });
        cancelButton.addEventListener("click", function () {
          decide("cancel");
        });

        actions.appendChild(executeButton);
        actions.appendChild(cancelButton);
        container.appendChild(actions);

        messagesEl.appendChild(container);
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
        } else if (data && data.type === "showPlan") {
          appendPlan(data.steps);
        }
      });
    })();
  </script>
</body>
</html>`;
}
