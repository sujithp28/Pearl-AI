import assert from "node:assert/strict";
import { test } from "node:test";
import {
  EXAMPLE_PROMPTS,
  MESSAGES_CONTAINER_ID,
  MESSAGE_INPUT_ID,
  SEND_BUTTON_ID,
  WELCOME_ID,
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

// ---------------------------------------------------------------------
// Welcome screen
// ---------------------------------------------------------------------

test("getChatHtml shows a welcome screen with the required copy", () => {
  const html = getChatHtml();

  assert.match(html, new RegExp(`id="${WELCOME_ID}"`));
  assert.match(html, /Welcome to Pearl/);
  assert.match(html, /Explain code/);
  assert.match(html, /Fix bugs/);
  assert.match(html, /Edit files/);
  assert.match(html, /Run tests/);
  assert.match(html, /Generate projects/);
});

test("getChatHtml includes exactly 4 clickable example prompts", () => {
  const html = getChatHtml();

  assert.equal(EXAMPLE_PROMPTS.length, 4);

  const matches = html.match(/class="example-prompt"/g) ?? [];
  assert.equal(matches.length, 4);

  for (const prompt of EXAMPLE_PROMPTS) {
    assert.match(html, new RegExp(`data-prompt="${prompt}"`));
  }
});

test("getChatHtml wires example prompts to send their text", () => {
  const html = getChatHtml();

  assert.match(html, /querySelectorAll\(".example-prompt"\)/);
  assert.match(html, /sendText\(button\.getAttribute\("data-prompt"\)\)/);
});

test("getChatHtml hides the welcome screen once a message is shown", () => {
  const html = getChatHtml();

  assert.match(html, /function showChat/);
  assert.match(html, /welcomeEl\.style\.display = "none"/);
});

// ---------------------------------------------------------------------
// Loading indicator
// ---------------------------------------------------------------------

test("getChatHtml handles loading messages with a visible/removable indicator", () => {
  const html = getChatHtml();

  assert.match(html, /function setLoading/);
  assert.match(html, /data\.type === "loading"/);
  assert.match(html, /class="dot"/);
});

// ---------------------------------------------------------------------
// Tool execution timeline
// ---------------------------------------------------------------------

test("getChatHtml renders all five timeline stages in order", () => {
  const html = getChatHtml();

  assert.match(html, /function updateTimeline/);
  assert.match(html, /data\.type === "timeline"/);
  assert.match(html, /"Planning\.\.\."/);
  assert.match(html, /"Plan Ready"/);
  assert.match(html, /"Waiting for Approval"/);
  assert.match(html, /"Running Tool\.\.\."/);
  assert.match(html, /"Completed"/);
});

test("getChatHtml clears the timeline when the stage is null", () => {
  const html = getChatHtml();

  assert.match(html, /if \(!stage\) {/);
  assert.match(html, /timelineEl\.remove\(\)/);
});

// ---------------------------------------------------------------------
// Message rendering: bubbles, timestamps, markdown content, error cards
// ---------------------------------------------------------------------

test("getChatHtml renders messages as role-specific bubbles", () => {
  const html = getChatHtml();

  assert.match(html, /className = "bubble " \+ message\.role/);
  assert.match(html, /"message-row " \+ message\.role/);
});

test("getChatHtml inserts pre-rendered message html rather than plain text", () => {
  const html = getChatHtml();

  assert.match(html, /content\.innerHTML = message\.html/);
});

test("getChatHtml shows a formatted timestamp for each message", () => {
  const html = getChatHtml();

  assert.match(html, /function formatTime/);
  assert.match(html, /toLocaleTimeString/);
  assert.match(html, /message\.timestamp/);
});

test("getChatHtml renders error messages with a distinct warning style", () => {
  const html = getChatHtml();

  assert.match(html, /\.bubble\.error/);
  assert.match(html, /error-icon/);
});

test("getChatHtml auto-scrolls the message list after appending content", () => {
  const html = getChatHtml();

  const scrollCalls = html.match(
    /messagesEl\.scrollTop = messagesEl\.scrollHeight/g
  ) ?? [];

  // appendMessage, setLoading, updateTimeline, and appendPlan each
  // scroll to the latest content.
  assert.ok(scrollCalls.length >= 4);
});

// ---------------------------------------------------------------------
// Theming
// ---------------------------------------------------------------------

test("getChatHtml only uses VS Code theme CSS variables for colors, no hardcoded theme", () => {
  const html = getChatHtml();
  const styleBlock = html.slice(html.indexOf("<style>"), html.indexOf("</style>"));

  assert.match(styleBlock, /var\(--vscode-editor-background\)/);
  assert.match(styleBlock, /var\(--vscode-button-background\)/);
  assert.doesNotMatch(styleBlock, /prefers-color-scheme/);
});

// ---------------------------------------------------------------------
// Patch preview & approval
// ---------------------------------------------------------------------

test("getChatHtml handles showPatchBatch messages and renders a file per patch", () => {
  const html = getChatHtml();

  assert.match(html, /function appendPatchBatch/);
  assert.match(html, /data\.type === "showPatchBatch"/);
  assert.match(html, /appendPatchBatch\(data\.files\)/);
  assert.match(html, /className = "patch-file-path"/);
});

test("getChatHtml shows added/removed line-count badges per file", () => {
  const html = getChatHtml();

  assert.match(html, /class="additions">\+' \+ file\.additions/);
  assert.match(html, /class="removals">-' \+ file\.removals/);
});

test("getChatHtml offers Approve / Reject actions and disables them once decided", () => {
  const html = getChatHtml();

  assert.match(html, /"Approve"/);
  assert.match(html, /rejectButton\.className = "secondary"/);
  assert.match(html, /rejectButton\.textContent = "Reject"/);
  assert.match(html, /type: "patchDecision"/);
  assert.match(html, /approveButton\.disabled = true/);
  assert.match(html, /rejectButton\.disabled = true/);
});

test("getChatHtml offers expand/collapse for large diffs via a plain <details> element", () => {
  const html = getChatHtml();

  assert.match(html, /file\.collapsed/);
  assert.match(html, /"Show diff"/);
});

test("getChatHtml offers a copy-diff action that posts the raw diff text", () => {
  const html = getChatHtml();

  assert.match(html, /className = "patch-file-copy"/);
  assert.match(html, /"Copy diff"/);
  assert.match(html, /type: "copyDiff", text: file\.diff/);
});

test("getChatHtml renders diffs with added/removed line highlighting", () => {
  const html = getChatHtml();

  assert.match(html, /function renderDiffLines/);
  assert.match(html, /"diff-line add"/);
  assert.match(html, /"diff-line remove"/);
});

test("getChatHtml renders new-file patches with a distinct badge", () => {
  const html = getChatHtml();

  assert.match(html, /file\.isNewFile/);
  assert.match(html, /className = "patch-file-new"/);
});

test("getChatHtml handles executionState messages for all five required states", () => {
  const html = getChatHtml();

  assert.match(html, /function updateExecutionState/);
  assert.match(html, /data\.type === "executionState"/);
  assert.match(html, /"Awaiting Approval"/);
  assert.match(html, /"Applying Patches\.\.\."/);
  assert.match(html, /"Resuming Execution\.\.\."/);
  assert.match(html, /EXECUTION_STATE_LABELS\[state\]/);
});

test("getChatHtml clears the execution-state banner when the state is null", () => {
  const html = getChatHtml();

  assert.match(html, /executionStateEl\.remove\(\)/);
});

// ---------------------------------------------------------------------
// Live progress streaming (pearl/progress)
// ---------------------------------------------------------------------

test("getChatHtml handles progress messages with a live status banner", () => {
  const html = getChatHtml();

  assert.match(html, /function updateProgress/);
  assert.match(html, /data\.type === "progress"/);
  assert.match(html, /updateProgress\(data\.event\)/);
  assert.match(html, /className = "progress-status"/);
});

test("getChatHtml clears the progress banner when the event is null", () => {
  const html = getChatHtml();

  const updateProgressBody = html.slice(
    html.indexOf("function updateProgress"),
    html.indexOf("function renderDiffLines")
  );

  assert.match(updateProgressBody, /if \(!event\) {/);
  assert.match(updateProgressBody, /progressEl\.remove\(\)/);
});

test("getChatHtml shows a step counter and the current action text", () => {
  const html = getChatHtml();

  assert.match(html, /step\.className = "progress-step"/);
  assert.match(html, /action\.className = "progress-action"/);
  assert.match(
    html,
    /"Step " \+ event\.currentStep \+ " of " \+ event\.totalSteps/
  );
  assert.match(html, /actionEl\.textContent = event\.currentAction/);
});

test("getChatHtml maps every known progress status to a human-readable label", () => {
  const html = getChatHtml();

  assert.match(html, /PROGRESS_STATUS_LABELS/);
  for (const label of [
    "Planning",
    "Running",
    "Step complete",
    "Step failed",
    "Replanning",
    "Finishing up",
    "Cancelling",
    "Awaiting approval",
    "Rejected",
  ]) {
    assert.match(html, new RegExp(label));
  }
});

test("getChatHtml shows a spinner while progress is streaming", () => {
  const html = getChatHtml();

  assert.match(html, /className = "progress-spinner"/);
  assert.match(html, /progress-spin/);
});
