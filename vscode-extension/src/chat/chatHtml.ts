/**
 * Static HTML for the Pearl chat webview.
 *
 * A welcome screen (with clickable example prompts) shows until the
 * first message; after that, a scrollable list of chat bubbles
 * (user/assistant/error), a loading indicator while waiting on a
 * response, a tool-execution timeline, plan-preview cards, and an
 * input row. Message content is rendered from HTML that
 * `renderMarkdownToHtml` (host-side, in `markdown.ts`) has already
 * produced and HTML-escaped — this script only inserts it.
 *
 * All colors come from VS Code theme CSS variables (`--vscode-*`),
 * so the UI follows the active light/dark/high-contrast theme
 * automatically; there is no separate light/dark stylesheet.
 *
 * A pure string-producing function (no `vscode` dependency), so its
 * structure can be unit tested directly.
 */

export const MESSAGES_CONTAINER_ID = "messages";
export const MESSAGE_INPUT_ID = "messageInput";
export const SEND_BUTTON_ID = "sendButton";
export const WELCOME_ID = "welcome";

export const EXAMPLE_PROMPTS: readonly string[] = [
  "Explain this code",
  "Fix a bug in my code",
  "Edit a file",
  "Run my tests",
];

export function getChatHtml(): string {
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<style>
  * { box-sizing: border-box; }
  body {
    font-family: var(--vscode-font-family);
    font-size: var(--vscode-font-size, 13px);
    color: var(--vscode-foreground);
    background-color: var(--vscode-editor-background);
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    height: 100vh;
  }

  /* -- Welcome screen ---------------------------------------------- */
  #${WELCOME_ID} {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    text-align: center;
    padding: 24px;
    overflow-y: auto;
  }
  #${WELCOME_ID} .welcome-emoji { font-size: 2.5em; }
  #${WELCOME_ID} h2 { margin: 8px 0; }
  #${WELCOME_ID} ul {
    list-style: none;
    padding: 0;
    margin: 8px 0 16px 0;
  }
  #${WELCOME_ID} ul li { margin: 2px 0; }
  #examplePrompts {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    justify-content: center;
    max-width: 480px;
  }
  .example-prompt {
    background-color: var(--vscode-button-secondaryBackground, var(--vscode-editorWidget-background));
    color: var(--vscode-button-secondaryForeground, var(--vscode-foreground));
    border: 1px solid var(--vscode-panel-border, transparent);
    border-radius: 16px;
    padding: 6px 14px;
    cursor: pointer;
    font-size: inherit;
  }
  .example-prompt:hover {
    background-color: var(--vscode-list-hoverBackground);
  }

  /* -- Message list -------------------------------------------------- */
  #${MESSAGES_CONTAINER_ID} {
    flex: 1;
    overflow-y: auto;
    padding: 8px;
    display: none;
  }
  #${MESSAGES_CONTAINER_ID}.visible { display: block; }

  .message-row {
    display: flex;
    margin-bottom: 10px;
  }
  .message-row.user { justify-content: flex-end; }
  .message-row.assistant, .message-row.error { justify-content: flex-start; }

  .bubble {
    max-width: 82%;
    min-width: 0;
    padding: 8px 12px;
    border-radius: 12px;
    overflow-wrap: break-word;
  }
  .bubble.user {
    background-color: var(--vscode-button-background);
    color: var(--vscode-button-foreground);
    border-bottom-right-radius: 3px;
  }
  .bubble.assistant {
    background-color: var(--vscode-editorWidget-background, var(--vscode-input-background));
    border: 1px solid var(--vscode-panel-border, transparent);
    border-bottom-left-radius: 3px;
  }
  .bubble.error {
    background-color: var(--vscode-inputValidation-errorBackground);
    border: 1px solid var(--vscode-inputValidation-errorBorder, var(--vscode-errorForeground));
    border-bottom-left-radius: 3px;
  }
  .bubble .error-icon { margin-bottom: 2px; }
  .bubble .content { white-space: normal; }
  .bubble .content p { margin: 0 0 6px 0; }
  .bubble .content p:last-child { margin-bottom: 0; }
  .bubble .content pre {
    background-color: var(--vscode-textCodeBlock-background, rgba(127,127,127,0.15));
    padding: 8px;
    border-radius: 4px;
    overflow-x: auto;
  }
  .bubble .content code {
    background-color: var(--vscode-textCodeBlock-background, rgba(127,127,127,0.15));
    font-family: var(--vscode-editor-font-family, monospace);
    padding: 1px 4px;
    border-radius: 3px;
  }
  .bubble .content pre code { background: none; padding: 0; }
  .bubble .content ul, .bubble .content ol { margin: 4px 0; padding-left: 20px; }
  .bubble .content table { border-collapse: collapse; margin: 4px 0; }
  .bubble .content th, .bubble .content td {
    border: 1px solid var(--vscode-panel-border, #444);
    padding: 4px 8px;
  }
  .bubble .meta {
    font-size: 0.8em;
    opacity: 0.7;
    margin-top: 4px;
  }

  /* -- Loading indicator ---------------------------------------------- */
  .loading .dot {
    display: inline-block;
    width: 6px;
    height: 6px;
    margin-right: 3px;
    border-radius: 50%;
    background-color: currentColor;
    opacity: 0.4;
    animation: pearl-loading-bounce 1s infinite ease-in-out;
  }
  .loading .dot:nth-child(2) { animation-delay: 0.15s; }
  .loading .dot:nth-child(3) { animation-delay: 0.3s; }
  @keyframes pearl-loading-bounce {
    0%, 80%, 100% { opacity: 0.3; transform: scale(0.8); }
    40% { opacity: 1; transform: scale(1); }
  }

  /* -- Tool execution timeline ------------------------------------------ */
  .timeline {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px;
    padding: 6px 10px;
    margin-bottom: 10px;
    border-radius: 8px;
    background-color: var(--vscode-editorWidget-background, transparent);
    font-size: 0.85em;
  }
  .timeline-step { opacity: 0.5; }
  .timeline-step.active { opacity: 1; font-weight: bold; color: var(--vscode-textLink-foreground); }
  .timeline-step.done { opacity: 0.8; }
  .timeline-sep { opacity: 0.4; }

  /* -- Plan card --------------------------------------------------------- */
  .plan {
    border: 1px solid var(--vscode-panel-border, #444);
    border-radius: 8px;
    background-color: var(--vscode-editorWidget-background, transparent);
    padding: 10px;
    margin-bottom: 10px;
  }
  .plan-header {
    font-weight: bold;
    margin-bottom: 6px;
  }
  .plan-step {
    margin: 6px 0;
    padding: 6px 8px;
    border-radius: 6px;
    background-color: var(--vscode-editor-background);
  }
  .plan-step-title { font-weight: bold; }
  .plan pre {
    white-space: pre-wrap;
    word-wrap: break-word;
    margin: 4px 0 0 0;
    background-color: var(--vscode-textCodeBlock-background, rgba(127,127,127,0.15));
    padding: 6px;
    border-radius: 4px;
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
    border-radius: 4px;
    padding: 4px 12px;
    cursor: pointer;
  }
  .plan-actions button.secondary {
    background-color: var(--vscode-button-secondaryBackground, transparent);
    color: var(--vscode-button-secondaryForeground, var(--vscode-foreground));
  }
  .plan-actions button:disabled {
    opacity: 0.6;
    cursor: default;
  }

  /* -- Input row ---------------------------------------------------------- */
  #inputRow {
    display: flex;
    gap: 8px;
    padding: 8px;
    border-top: 1px solid var(--vscode-panel-border, #444);
  }
  #${MESSAGE_INPUT_ID} {
    flex: 1;
    min-width: 0;
    background-color: var(--vscode-input-background);
    color: var(--vscode-input-foreground);
    border: 1px solid var(--vscode-input-border, transparent);
    border-radius: 4px;
    padding: 6px 8px;
  }
  #${SEND_BUTTON_ID} {
    background-color: var(--vscode-button-background);
    color: var(--vscode-button-foreground);
    border: none;
    border-radius: 4px;
    padding: 4px 14px;
    cursor: pointer;
  }
</style>
</head>
<body>
  <div id="${WELCOME_ID}">
    <div class="welcome-emoji">👋</div>
    <h2>Welcome to Pearl</h2>
    <p>You can ask me to:</p>
    <ul>
      <li>• Explain code</li>
      <li>• Fix bugs</li>
      <li>• Edit files</li>
      <li>• Run tests</li>
      <li>• Generate projects</li>
    </ul>
    <div id="examplePrompts">
      ${EXAMPLE_PROMPTS.map(
        (prompt) =>
          `<button class="example-prompt" data-prompt="${prompt}">${prompt}</button>`
      ).join("\n      ")}
    </div>
  </div>
  <div id="${MESSAGES_CONTAINER_ID}"></div>
  <div id="inputRow">
    <input id="${MESSAGE_INPUT_ID}" type="text" placeholder="Message Pearl..." autocomplete="off" />
    <button id="${SEND_BUTTON_ID}">Send</button>
  </div>
  <script>
    (function () {
      const vscode = acquireVsCodeApi();
      const welcomeEl = document.getElementById("${WELCOME_ID}");
      const messagesEl = document.getElementById("${MESSAGES_CONTAINER_ID}");
      const inputEl = document.getElementById("${MESSAGE_INPUT_ID}");
      const sendButton = document.getElementById("${SEND_BUTTON_ID}");

      let loadingEl = null;
      let timelineEl = null;

      const TIMELINE_STAGES = [
        ["planning", "Planning..."],
        ["plan_ready", "Plan Ready"],
        ["waiting_approval", "Waiting for Approval"],
        ["running_tool", "Running Tool..."],
        ["completed", "Completed"]
      ];

      function showChat() {
        welcomeEl.style.display = "none";
        messagesEl.classList.add("visible");
      }

      function formatTime(iso) {
        try {
          return new Date(iso).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit"
          });
        } catch (err) {
          return "";
        }
      }

      function appendMessage(message) {
        showChat();

        const row = document.createElement("div");
        row.className = "message-row " + message.role;

        const bubble = document.createElement("div");
        bubble.className = "bubble " + message.role;

        if (message.role === "error") {
          const icon = document.createElement("div");
          icon.className = "error-icon";
          icon.textContent = "\\u26A0\\uFE0F Something went wrong";
          bubble.appendChild(icon);
        }

        const content = document.createElement("div");
        content.className = "content";
        content.innerHTML = message.html;
        bubble.appendChild(content);

        const meta = document.createElement("div");
        meta.className = "meta";
        meta.textContent = formatTime(message.timestamp);
        bubble.appendChild(meta);

        row.appendChild(bubble);
        messagesEl.appendChild(row);
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }

      function setLoading(show) {
        if (show) {
          showChat();
          if (!loadingEl) {
            loadingEl = document.createElement("div");
            loadingEl.className = "message-row assistant";
            loadingEl.innerHTML =
              '<div class="bubble assistant loading">' +
              '<span class="dot"></span><span class="dot"></span><span class="dot"></span>' +
              "</div>";
            messagesEl.appendChild(loadingEl);
            messagesEl.scrollTop = messagesEl.scrollHeight;
          }
        } else if (loadingEl) {
          loadingEl.remove();
          loadingEl = null;
        }
      }

      function updateTimeline(stage) {
        if (!stage) {
          if (timelineEl) {
            timelineEl.remove();
            timelineEl = null;
          }
          return;
        }

        showChat();

        if (!timelineEl) {
          timelineEl = document.createElement("div");
          timelineEl.className = "timeline";
          messagesEl.appendChild(timelineEl);
        }

        const currentIndex = TIMELINE_STAGES.findIndex(function (entry) {
          return entry[0] === stage;
        });

        timelineEl.innerHTML = "";
        TIMELINE_STAGES.forEach(function (entry, index) {
          const stepEl = document.createElement("span");
          let className = "timeline-step";
          if (index < currentIndex) {
            className += " done";
          } else if (index === currentIndex) {
            className += " active";
          }
          stepEl.className = className;
          stepEl.textContent = entry[1];
          timelineEl.appendChild(stepEl);

          if (index < TIMELINE_STAGES.length - 1) {
            const sep = document.createElement("span");
            sep.className = "timeline-sep";
            sep.textContent = "\\u2192";
            timelineEl.appendChild(sep);
          }
        });

        messagesEl.scrollTop = messagesEl.scrollHeight;
      }

      function appendPlan(steps) {
        showChat();

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
        cancelButton.className = "secondary";
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

      function sendText(text) {
        if (!text || !text.trim()) {
          return;
        }
        vscode.postMessage({ type: "sendMessage", text: text });
      }

      function send() {
        const text = inputEl.value;
        sendText(text);
        inputEl.value = "";
      }

      sendButton.addEventListener("click", send);
      inputEl.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
          send();
        }
      });

      document.querySelectorAll(".example-prompt").forEach(function (button) {
        button.addEventListener("click", function () {
          sendText(button.getAttribute("data-prompt"));
        });
      });

      window.addEventListener("message", function (event) {
        const data = event.data;
        if (!data) {
          return;
        }
        if (data.type === "addMessage") {
          appendMessage(data.message);
        } else if (data.type === "showPlan") {
          appendPlan(data.steps);
        } else if (data.type === "loading") {
          setLoading(!!data.show);
        } else if (data.type === "timeline") {
          updateTimeline(data.stage);
        }
      });
    })();
  </script>
</body>
</html>`;
}
