/**
 * Static HTML for the Pearl chat webview — v3 visual design.
 *
 * Visual design ported from docs/pearl-ui-v3.html: dark-purple theme,
 * gem avatar bubbles, v3 plan / patch approval cards, polished input.
 * All VS Code messaging, approval flows, and progress updates are
 * unchanged from the previous revision — only the presentation layer
 * (CSS + avatar additions) changed.
 *
 * All class names required by the unit tests are preserved exactly:
 *   "message-row " + message.role, "bubble " + message.role,
 *   .bubble.error, patch-file-copy, .secondary, "Execute Plan",
 *   cancelButton.textContent = "Cancel", rejectButton.textContent = "Reject"
 *
 * A pure string-producing function (no `vscode` dependency) so it can
 * be unit-tested directly.
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
  /* ── v3 design tokens — VS Code theme variables are primary ─── */
  :root {
    /* Required VS Code variables (used directly, no fallback, so the
       webview host always injects the correct theme value). */
    --p-bg: var(--vscode-editor-background);
    --p-primary: var(--vscode-button-background);
    /* Additional mappings */
    --p-hdr: var(--vscode-titleBar-activeBackground, #0f0f1d);
    --p-surface: var(--vscode-editorWidget-background, #131325);
    --p-elevated: var(--vscode-input-background, #1a1a32);
    --p-border: var(--vscode-panel-border, #1f1f3a);
    --p-text: var(--vscode-foreground, #e4e4f4);
    --p-muted: var(--vscode-descriptionForeground, #6e6e90);
    --p-btn-fg: var(--vscode-button-foreground, #ffffff);
    --p-btn2-bg: var(--vscode-button-secondaryBackground, #1a1a32);
    --p-btn2-fg: var(--vscode-button-secondaryForeground, #e4e4f4);
    --p-err-bg: var(--vscode-inputValidation-errorBackground, rgba(248,113,113,.08));
    --p-err-border: var(--vscode-inputValidation-errorBorder, #f87171);
    --p-code-bg: var(--vscode-textCodeBlock-background, rgba(127,127,127,.15));
    --p-ec: #a78bfa;
    --p-green: #4ade80;
    --p-red: #f87171;
    --p-amber: #fbbf24;
    --font: var(--vscode-font-family, -apple-system, 'Segoe UI Variable', 'Segoe UI', system-ui, sans-serif);
    --mono: var(--vscode-editor-font-family, 'Cascadia Code', 'JetBrains Mono', ui-monospace, monospace);
    --radius: 10px;
  }
  :root[data-theme="dark"] {
    --p-bg: #0a0a12; --p-hdr: #0f0f1d; --p-surface: #131325;
    --p-elevated: #1a1a32; --p-border: #1f1f3a; --p-text: #e4e4f4;
    --p-muted: #6e6e90;
  }
  :root[data-theme="light"] {
    --p-bg: #f2f2f8; --p-hdr: #e6e6f0; --p-surface: #fff;
    --p-elevated: #ededf8; --p-border: #d2d2e4; --p-text: #18182c;
    --p-muted: #5a5a88;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; overflow: hidden; }
  body {
    font-family: var(--font);
    font-size: var(--vscode-font-size, 13px);
    color: var(--p-text);
    background: var(--p-bg);
    display: flex;
    flex-direction: column;
    height: 100vh;
  }

  /* ── Header ──────────────────────────────────────────────────── */
  #pearHeader {
    height: 38px;
    background: var(--p-hdr);
    border-bottom: 1px solid var(--p-border);
    display: flex;
    align-items: center;
    padding: 0 12px;
    gap: 8px;
    flex-shrink: 0;
    user-select: none;
  }
  .pearl-logo {
    display: flex;
    align-items: center;
    gap: 7px;
    font-weight: 700;
    font-size: 13px;
  }
  .logo-gem {
    width: 17px; height: 17px;
    border-radius: 50%;
    background: radial-gradient(135deg at 38% 30%, #fff 0%, #ddd0ff 20%, #b49af8 60%, #6d3fe0 100%);
    box-shadow: 0 0 7px color-mix(in srgb, var(--p-ec) 50%, transparent);
  }
  .header-spacer { flex: 1; }
  .conn-dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--p-green);
    box-shadow: 0 0 4px var(--p-green);
    animation: dot-pulse 2s ease-in-out infinite;
  }
  @keyframes dot-pulse { 0%,100%{opacity:1} 50%{opacity:.4} }

  /* ── Welcome ────────────────────────────────────────────────── */
  #${WELCOME_ID} {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    text-align: center;
    padding: 28px 20px;
    overflow-y: auto;
  }
  .welcome-gem {
    width: 52px; height: 52px;
    border-radius: 50%;
    background: radial-gradient(135deg at 38% 30%, #fff 0%, #ddd0ff 20%, #b49af8 60%, #6d3fe0 100%);
    box-shadow: 0 0 22px color-mix(in srgb, var(--p-ec) 44%, transparent);
    margin-bottom: 14px;
    animation: gem-float 3.5s ease-in-out infinite;
  }
  @keyframes gem-float { 0%,100%{transform:translateY(0)} 50%{transform:translateY(-7px)} }
  #${WELCOME_ID} h2 { font-size: 17px; font-weight: 700; margin-bottom: 5px; }
  #${WELCOME_ID} > p { font-size: 12px; color: var(--p-muted); margin-bottom: 14px; }
  #${WELCOME_ID} ul {
    list-style: none;
    font-size: 12px;
    color: var(--p-muted);
    margin-bottom: 18px;
    text-align: left;
    line-height: 2;
  }
  #examplePrompts {
    display: flex;
    flex-wrap: wrap;
    gap: 7px;
    justify-content: center;
    max-width: 360px;
  }
  .example-prompt {
    background: var(--p-elevated);
    color: var(--p-text);
    border: 1px solid var(--p-border);
    border-radius: 20px;
    padding: 5px 13px;
    cursor: pointer;
    font-size: 12px;
    font-family: var(--font);
    transition: border-color .18s, background .18s;
  }
  .example-prompt:hover {
    border-color: color-mix(in srgb, var(--p-primary) 55%, transparent);
    background: color-mix(in srgb, var(--p-primary) 9%, transparent);
  }

  /* ── Message list ───────────────────────────────────────────── */
  #${MESSAGES_CONTAINER_ID} {
    flex: 1;
    overflow-y: auto;
    padding: 12px;
    display: none;
    scrollbar-width: thin;
    scrollbar-color: var(--p-border) transparent;
  }
  #${MESSAGES_CONTAINER_ID}.visible { display: block; }

  /* message-row: v3 flex layout with avatar */
  .message-row {
    display: flex;
    gap: 9px;
    margin-bottom: 12px;
    max-width: 100%;
  }
  .message-row.user { flex-direction: row-reverse; }
  .message-row.assistant, .message-row.error { flex-direction: row; }

  /* gem / user avatar beside each bubble */
  .msg-av {
    width: 26px; height: 26px;
    border-radius: 7px;
    background: var(--p-elevated);
    border: 1px solid var(--p-border);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 13px;
    flex-shrink: 0;
    align-self: flex-start;
    margin-top: 2px;
  }
  .message-row.assistant .msg-av,
  .message-row.error .msg-av {
    background: radial-gradient(135deg at 38% 30%, #fff 0%, #ddd0ff 20%, #b49af8 60%, #6d3fe0 100%);
    border-color: color-mix(in srgb, var(--p-primary) 40%, transparent);
    box-shadow: 0 0 6px color-mix(in srgb, var(--p-ec) 28%, transparent);
  }

  /* bubble — same class name the tests require */
  .bubble {
    padding: 9px 12px;
    border-radius: 4px 12px 12px 12px;
    background: var(--p-surface);
    border: 1px solid var(--p-border);
    font-size: 13px;
    line-height: 1.65;
    max-width: 520px;
    min-width: 0;
    overflow-wrap: break-word;
  }
  .bubble.user {
    border-radius: 12px 4px 12px 12px;
    background: color-mix(in srgb, var(--p-primary) 13%, transparent);
    border-color: color-mix(in srgb, var(--p-primary) 28%, transparent);
  }
  .bubble.assistant {
    border-color: color-mix(in srgb, var(--p-primary) 18%, transparent);
  }
  /* test requires .bubble.error */
  .bubble.error {
    background: var(--p-err-bg);
    border-color: var(--p-err-border);
  }
  .bubble .error-icon {
    font-size: 11px;
    font-weight: 600;
    color: var(--p-red);
    margin-bottom: 4px;
  }
  .bubble .content { white-space: normal; }
  .bubble .content p { margin: 0 0 6px 0; }
  .bubble .content p:last-child { margin-bottom: 0; }
  .bubble .content pre {
    background: var(--p-code-bg);
    padding: 8px;
    border-radius: 5px;
    overflow-x: auto;
    font-family: var(--mono);
    font-size: 11.5px;
    border: 1px solid var(--p-border);
    margin: 5px 0;
  }
  .bubble .content code {
    background: var(--p-code-bg);
    font-family: var(--mono);
    font-size: 11.5px;
    padding: 1px 5px;
    border-radius: 4px;
    color: var(--p-ec);
  }
  .bubble .content pre code { background: none; padding: 0; color: inherit; }
  .bubble .content ul, .bubble .content ol { margin: 4px 0; padding-left: 18px; }
  .bubble .content table { border-collapse: collapse; margin: 4px 0; font-size: 12px; }
  .bubble .content th, .bubble .content td {
    border: 1px solid var(--p-border); padding: 4px 8px;
  }
  .bubble .meta {
    font-size: 10px;
    color: var(--p-muted);
    margin-top: 5px;
    opacity: .7;
  }

  /* ── Loading dots ───────────────────────────────────────────── */
  .loading-row {
    display: flex;
    gap: 9px;
    margin-bottom: 12px;
  }
  .loading-av {
    width: 26px; height: 26px;
    border-radius: 7px;
    background: radial-gradient(135deg at 38% 30%, #fff 0%, #ddd0ff 20%, #b49af8 60%, #6d3fe0 100%);
    border: 1px solid color-mix(in srgb, var(--p-primary) 40%, transparent);
    display: flex; align-items: center; justify-content: center;
    font-size: 13px; flex-shrink: 0;
  }
  .loading-bubble {
    padding: 11px 14px;
    border-radius: 4px 12px 12px 12px;
    background: var(--p-surface);
    border: 1px solid color-mix(in srgb, var(--p-primary) 18%, transparent);
    display: flex;
    gap: 5px;
    align-items: center;
  }
  .dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--p-ec);
    opacity: .4;
    animation: pearl-loading-bounce 1s infinite ease-in-out;
  }
  .dot:nth-child(2) { animation-delay: .15s; }
  .dot:nth-child(3) { animation-delay: .30s; }
  @keyframes pearl-loading-bounce {
    0%,80%,100% { opacity: .3; transform: scale(.75); }
    40% { opacity: 1; transform: scale(1); }
  }

  /* ── Progress status ────────────────────────────────────────── */
  .progress-status {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 7px 11px;
    margin-bottom: 12px;
    border-radius: 8px;
    background: var(--p-surface);
    border: 1px solid var(--p-border);
    font-size: 11.5px;
    color: var(--p-muted);
  }
  .progress-spinner {
    width: 10px; height: 10px;
    border-radius: 50%;
    border: 2px solid var(--p-primary);
    border-top-color: transparent;
    animation: progress-spin .7s linear infinite;
    flex-shrink: 0;
  }
  @keyframes progress-spin { to { transform: rotate(360deg); } }
  .progress-step { opacity: .75; flex-shrink: 0; }
  .progress-action { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  /* ── Timeline ─────────────────────────────────────────────── */
  .timeline {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 5px;
    padding: 7px 11px;
    margin-bottom: 12px;
    border-radius: 8px;
    background: var(--p-surface);
    border: 1px solid var(--p-border);
    font-size: 11px;
  }
  .timeline-step { opacity: .45; }
  .timeline-step.active { opacity: 1; font-weight: 600; color: var(--p-primary); }
  .timeline-step.done { opacity: .75; }
  .timeline-sep { opacity: .3; }

  /* ── Execution state ────────────────────────────────────────── */
  .execution-state {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 7px 11px;
    margin-bottom: 12px;
    border-radius: 8px;
    background: color-mix(in srgb, var(--p-primary) 8%, transparent);
    border: 1px solid color-mix(in srgb, var(--p-primary) 30%, transparent);
    font-size: 11.5px;
    font-weight: 600;
    color: var(--p-primary);
  }

  /* ── Plan card (v3 amber style) ─────────────────────────────── */
  .plan {
    border-radius: var(--radius);
    border: 1px solid color-mix(in srgb, var(--p-amber) 30%, transparent);
    background: color-mix(in srgb, var(--p-amber) 4%, transparent);
    margin-bottom: 12px;
    overflow: hidden;
  }
  .plan-card-top {
    padding: 10px 13px;
    display: flex;
    align-items: center;
    gap: 9px;
    border-bottom: 1px solid color-mix(in srgb, var(--p-amber) 18%, transparent);
  }
  .plan-card-ic {
    width: 30px; height: 30px;
    border-radius: 7px;
    background: color-mix(in srgb, var(--p-amber) 16%, transparent);
    border: 1px solid color-mix(in srgb, var(--p-amber) 32%, transparent);
    display: flex; align-items: center; justify-content: center;
    font-size: 14px; flex-shrink: 0;
  }
  .plan-card-title { font-size: 12.5px; font-weight: 700; }
  .plan-card-sub { font-size: 10.5px; color: var(--p-muted); margin-top: 2px; }

  .plan-header { display: none; } /* hidden — card-top replaces it */

  .plan-step {
    display: flex;
    align-items: flex-start;
    gap: 9px;
    margin: 6px 13px;
    padding: 7px 9px;
    border-radius: 7px;
    background: var(--p-elevated);
    border: 1px solid var(--p-border);
  }
  .plan-step-num {
    min-width: 20px; height: 20px;
    border-radius: 50%;
    background: color-mix(in srgb, var(--p-primary) 14%, transparent);
    border: 1px solid color-mix(in srgb, var(--p-primary) 35%, transparent);
    color: var(--p-primary);
    font-size: 10px; font-weight: 700;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0; margin-top: 1px;
  }
  .plan-step-inner { flex: 1; min-width: 0; }
  .plan-step-title { font-weight: 600; font-size: 12.5px; margin-bottom: 4px; }
  .plan pre {
    white-space: pre-wrap;
    word-wrap: break-word;
    margin: 4px 0 0 0;
    padding: 5px 8px;
    background: var(--p-code-bg);
    border-radius: 4px;
    font-family: var(--mono);
    font-size: 11px;
    border: 1px solid var(--p-border);
  }
  .plan-actions {
    margin: 10px 13px 13px;
    display: flex;
    gap: 8px;
  }
  .plan-actions button {
    flex: 1;
    padding: 7px 0;
    border-radius: 7px;
    border: none;
    background: var(--p-primary);
    color: var(--p-btn-fg);
    font-family: var(--font);
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
    transition: opacity .18s;
    text-align: center;
  }
  .plan-actions button:hover { opacity: .88; }
  .plan-actions button.secondary {
    background: color-mix(in srgb, var(--p-red) 9%, transparent);
    border: 1px solid color-mix(in srgb, var(--p-red) 28%, transparent);
    color: var(--p-red);
  }
  .plan-actions button:disabled { opacity: .4; cursor: not-allowed; }

  /* ── Patch batch card (v3 green style) ─────────────────────── */
  .patch-batch {
    border-radius: var(--radius);
    border: 1px solid color-mix(in srgb, var(--p-green) 28%, transparent);
    background: color-mix(in srgb, var(--p-green) 4%, transparent);
    margin-bottom: 12px;
    overflow: hidden;
  }
  .patch-batch-card-top {
    padding: 10px 13px;
    display: flex;
    align-items: center;
    gap: 9px;
    border-bottom: 1px solid color-mix(in srgb, var(--p-green) 18%, transparent);
  }
  .patch-card-ic {
    width: 30px; height: 30px;
    border-radius: 7px;
    background: color-mix(in srgb, var(--p-green) 16%, transparent);
    border: 1px solid color-mix(in srgb, var(--p-green) 32%, transparent);
    display: flex; align-items: center; justify-content: center;
    font-size: 14px; flex-shrink: 0;
  }
  .patch-batch-header { display: none; } /* replaced by card-top */
  .patch-batch-title { font-size: 12.5px; font-weight: 700; }
  .patch-batch-sub { font-size: 10.5px; color: var(--p-muted); margin-top: 2px; }

  .patch-file {
    margin: 6px 13px;
    padding: 8px 10px;
    border-radius: 7px;
    background: var(--p-elevated);
    border: 1px solid var(--p-border);
  }
  .patch-file-header {
    display: flex;
    align-items: center;
    gap: 7px;
    flex-wrap: wrap;
    margin-bottom: 5px;
  }
  .patch-file-path {
    font-weight: 600;
    font-family: var(--mono);
    font-size: 11.5px;
  }
  .patch-file-badge {
    font-size: 11px;
    font-family: var(--mono);
  }
  .patch-file-badge .additions { color: var(--p-green); }
  .patch-file-badge .removals { color: var(--p-red); }
  .patch-file-new {
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 8px;
    background: color-mix(in srgb, var(--p-primary) 14%, transparent);
    border: 1px solid color-mix(in srgb, var(--p-primary) 30%, transparent);
    color: var(--p-primary);
  }
  /* test requires className = "patch-file-copy" */
  .patch-file-copy {
    margin-left: auto;
    background: none;
    border: 1px solid var(--p-border);
    color: var(--p-muted);
    border-radius: 5px;
    padding: 2px 8px;
    cursor: pointer;
    font-size: 10.5px;
    font-family: var(--font);
    transition: background .15s;
  }
  .patch-file-copy:hover { background: var(--p-surface); color: var(--p-text); }

  .diff-view {
    font-family: var(--mono);
    font-size: 11px;
    border-radius: 5px;
    border: 1px solid var(--p-border);
    overflow: hidden;
    margin-top: 5px;
  }
  .diff-line { padding: 1.5px 10px; white-space: pre; line-height: 1.7; }
  .diff-line.add {
    background: color-mix(in srgb, var(--p-green) 8%, transparent);
    color: var(--p-green);
  }
  .diff-line.remove {
    background: color-mix(in srgb, var(--p-red) 8%, transparent);
    color: var(--p-red);
    opacity: .8;
  }
  .diff-line.hunk {
    background: rgba(96,165,250,.08);
    color: #60a5fa;
    font-style: italic;
  }

  .patch-actions {
    margin: 10px 13px 13px;
    display: flex;
    gap: 8px;
  }
  .patch-actions button {
    flex: 1;
    padding: 7px 0;
    border-radius: 7px;
    border: none;
    background: var(--p-primary);
    color: var(--p-btn-fg);
    font-family: var(--font);
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
    transition: opacity .18s;
    text-align: center;
  }
  .patch-actions button:hover { opacity: .88; }
  .patch-actions button.secondary {
    background: color-mix(in srgb, var(--p-red) 9%, transparent);
    border: 1px solid color-mix(in srgb, var(--p-red) 28%, transparent);
    color: var(--p-red);
  }
  .patch-actions button:disabled { opacity: .4; cursor: not-allowed; }

  /* ── Input row ──────────────────────────────────────────────── */
  #inputRow {
    display: flex;
    gap: 8px;
    padding: 10px 12px;
    border-top: 1px solid var(--p-border);
    background: var(--p-hdr);
    flex-shrink: 0;
    align-items: center;
  }
  #${MESSAGE_INPUT_ID} {
    flex: 1;
    min-width: 0;
    background: var(--p-elevated);
    color: var(--p-text);
    border: 1px solid var(--p-border);
    border-radius: var(--radius);
    padding: 8px 12px;
    font-family: var(--font);
    font-size: 13px;
    outline: none;
    transition: border-color .2s, box-shadow .2s;
  }
  #${MESSAGE_INPUT_ID}::placeholder { color: var(--p-muted); }
  #${MESSAGE_INPUT_ID}:focus {
    border-color: var(--p-primary);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--p-primary) 11%, transparent);
  }
  #${MESSAGE_INPUT_ID}:disabled {
    opacity: .55;
    cursor: not-allowed;
  }
  #${SEND_BUTTON_ID}:disabled {
    opacity: .35;
    cursor: not-allowed;
  }
  #${SEND_BUTTON_ID} {
    width: 34px; height: 34px;
    border-radius: 9px;
    border: none;
    background: var(--p-primary);
    color: var(--p-btn-fg);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 15px;
    transition: opacity .18s, transform .12s;
    flex-shrink: 0;
  }
  #${SEND_BUTTON_ID}:hover { opacity: .88; }
  #${SEND_BUTTON_ID}:active { transform: scale(.92); }
</style>
</head>
<body>
  <!-- v3 header with gem logo -->
  <div id="pearHeader">
    <div class="pearl-logo">
      <div class="logo-gem"></div>
      Pearl
    </div>
    <div class="header-spacer"></div>
    <div class="conn-dot"></div>
  </div>

  <div id="${WELCOME_ID}">
    <div class="welcome-gem"></div>
    <h2>Welcome to Pearl</h2>
    <p>Your autonomous AI coding assistant</p>
    <ul>
      <li>&#x1F4AC; Explain code &amp; answer questions</li>
      <li>&#x1F41B; Fix bugs in your codebase</li>
      <li>&#x270D;&#xFE0F; Edit files with your approval</li>
      <li>&#x1F9EA; Run tests &amp; report results</li>
      <li>&#x2728; Generate projects from scratch</li>
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
    <input id="${MESSAGE_INPUT_ID}" type="text" placeholder="Message Pearl…" autocomplete="off" />
    <button id="${SEND_BUTTON_ID}" title="Send">&#x2191;</button>
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
      let executionStateEl = null;
      let progressEl = null;

      const PROGRESS_STATUS_LABELS = {
        planning: "Planning",
        executing_step: "Running",
        step_completed: "Step complete",
        step_failed: "Step failed",
        replanning: "Replanning",
        task_completed: "Finishing up",
        cancelled: "Cancelling",
        awaiting_approval: "Awaiting approval",
        rejected: "Rejected"
      };

      const TIMELINE_STAGES = [
        ["planning", "Planning..."],
        ["plan_ready", "Plan Ready"],
        ["waiting_approval", "Waiting for Approval"],
        ["running_tool", "Running Tool..."],
        ["completed", "Completed"]
      ];

      const EXECUTION_STATE_LABELS = {
        awaiting_approval: "Awaiting Approval",
        applying_patches: "Applying Patches...",
        resuming: "Resuming Execution...",
        completed: "Completed",
        cancelled: "Cancelled"
      };

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

      /* ── Progressive assistant bubble ──────────────────────────── */
      let streamRow = null;
      let streamContent = null;
      let streamAccum = "";

      function startStream() {
        showChat();
        streamAccum = "";

        streamRow = document.createElement("div");
        streamRow.className = "message-row assistant";

        const av = document.createElement("div");
        av.className = "msg-av";
        av.textContent = "\\uD83D\\uDC8E";

        const bubble = document.createElement("div");
        bubble.className = "bubble assistant";

        streamContent = document.createElement("div");
        streamContent.className = "content";

        bubble.appendChild(streamContent);
        streamRow.appendChild(av);
        streamRow.appendChild(bubble);
        messagesEl.appendChild(streamRow);
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }

      function appendChunk(chunk) {
        if (!streamContent) {
          return;
        }
        streamAccum += chunk;
        // Display raw text progressively — finalizeStream replaces
        // this with fully rendered HTML once the response is complete.
        streamContent.textContent = streamAccum;
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }

      function finalizeStream(html, timestamp) {
        if (!streamRow) {
          return;
        }
        // Replace raw text with markdown-rendered HTML from controller.
        if (html) {
          streamContent.innerHTML = html;
        }
        const meta = document.createElement("div");
        meta.className = "meta";
        meta.textContent = formatTime(timestamp || new Date().toISOString());
        streamRow.querySelector(".bubble").appendChild(meta);
        streamRow = null;
        streamContent = null;
        streamAccum = "";
      }

      /* ── v3: avatar bubble (class names kept for test compatibility) ── */
      function appendMessage(message) {
        showChat();

        const row = document.createElement("div");
        row.className = "message-row " + message.role;

        // v3 gem / user avatar
        const av = document.createElement("div");
        av.className = "msg-av";
        av.textContent = message.role === "user" ? "\\uD83D\\uDC64" : "\\uD83D\\uDC8E";

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

        if (message.role === "user") {
          row.appendChild(bubble);
          row.appendChild(av);
        } else {
          row.appendChild(av);
          row.appendChild(bubble);
        }

        messagesEl.appendChild(row);
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }

      /* ── Loading indicator ────────────────────────────────────── */
      function setLoading(show) {
        if (show) {
          showChat();
          if (!loadingEl) {
            loadingEl = document.createElement("div");
            loadingEl.className = "loading-row";
            loadingEl.innerHTML =
              '<div class="loading-av">\\uD83D\\uDC8E</div>' +
              '<div class="loading-bubble">' +
              '<span class="dot"></span>' +
              '<span class="dot"></span>' +
              '<span class="dot"></span>' +
              '</div>';
            messagesEl.appendChild(loadingEl);
            messagesEl.scrollTop = messagesEl.scrollHeight;
          }
          inputEl.disabled = true;
          sendButton.disabled = true;
          inputEl.placeholder = "Pearl is thinking\\u2026";
        } else {
          if (loadingEl) {
            loadingEl.remove();
            loadingEl = null;
          }
          inputEl.disabled = false;
          sendButton.disabled = false;
          inputEl.placeholder = "Message Pearl\\u2026";
        }
      }

      /* ── Timeline ─────────────────────────────────────────────── */
      function updateTimeline(stage, labels) {
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

        const visibleStages = TIMELINE_STAGES.slice(0, currentIndex + 1);

        timelineEl.innerHTML = "";
        visibleStages.forEach(function (entry, index) {
          const stepEl = document.createElement("span");
          let className = "timeline-step";
          if (index < currentIndex) {
            className += " done";
          } else if (index === currentIndex) {
            className += " active";
          }
          stepEl.className = className;
          stepEl.textContent = (labels && labels[entry[0]]) || entry[1];
          timelineEl.appendChild(stepEl);

          if (index < visibleStages.length - 1) {
            const sep = document.createElement("span");
            sep.className = "timeline-sep";
            sep.textContent = "\\u2192";
            timelineEl.appendChild(sep);
          }
        });

        messagesEl.scrollTop = messagesEl.scrollHeight;
      }

      /* ── v3 plan card ─────────────────────────────────────────── */
      function appendPlan(steps) {
        showChat();

        const container = document.createElement("div");
        container.className = "plan";

        // v3 card header
        const cardTop = document.createElement("div");
        cardTop.className = "plan-card-top";
        cardTop.innerHTML =
          '<div class="plan-card-ic">&#x26A1;</div>' +
          '<div><div class="plan-card-title">Proposed Plan</div>' +
          '<div class="plan-card-sub">' + steps.length +
          ' step' + (steps.length === 1 ? '' : 's') +
          ' &#x2014; approve to execute</div></div>';
        container.appendChild(cardTop);

        // steps
        steps.forEach(function (step) {
          const stepEl = document.createElement("div");
          stepEl.className = "plan-step";

          // step number badge
          const num = document.createElement("div");
          num.className = "plan-step-num";
          num.textContent = String(step.index);
          stepEl.appendChild(num);

          const inner = document.createElement("div");
          inner.className = "plan-step-inner";

          const title = document.createElement("div");
          title.className = "plan-step-title";
          title.textContent = "Step " + step.index + ": " + step.tool;
          inner.appendChild(title);

          if (step.collapsed) {
            const details = document.createElement("details");
            const summary = document.createElement("summary");
            summary.textContent = "Arguments";
            details.appendChild(summary);
            const pre = document.createElement("pre");
            pre.textContent = step.argumentsText;
            details.appendChild(pre);
            inner.appendChild(details);
          } else {
            const pre = document.createElement("pre");
            pre.textContent = step.argumentsText;
            inner.appendChild(pre);
          }

          stepEl.appendChild(inner);
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

      /* ── Execution state ──────────────────────────────────────── */
      function updateExecutionState(state) {
        if (!state) {
          if (executionStateEl) {
            executionStateEl.remove();
            executionStateEl = null;
          }
          return;
        }

        showChat();

        if (!executionStateEl) {
          executionStateEl = document.createElement("div");
          executionStateEl.className = "execution-state";
          messagesEl.appendChild(executionStateEl);
        }

        executionStateEl.textContent = EXECUTION_STATE_LABELS[state] || state;
        messagesEl.scrollTop = messagesEl.scrollHeight;
      }

      /* ── Progress ─────────────────────────────────────────────── */
      function updateProgress(event) {
        if (!event) {
          if (progressEl) {
            progressEl.remove();
            progressEl = null;
          }
          return;
        }

        showChat();

        if (!progressEl) {
          progressEl = document.createElement("div");
          progressEl.className = "progress-status";

          const spinner = document.createElement("span");
          spinner.className = "progress-spinner";
          progressEl.appendChild(spinner);

          const step = document.createElement("span");
          step.className = "progress-step";
          progressEl.appendChild(step);

          const action = document.createElement("span");
          action.className = "progress-action";
          progressEl.appendChild(action);

          messagesEl.appendChild(progressEl);
        }

        const stepEl = progressEl.querySelector(".progress-step");
        const actionEl = progressEl.querySelector(".progress-action");

        stepEl.textContent =
          event.totalSteps > 0
            ? "Step " + event.currentStep + " of " + event.totalSteps + " \\u2014 "
            : (PROGRESS_STATUS_LABELS[event.status] || event.status) + " \\u2014 ";
        actionEl.textContent = event.currentAction || "";

        messagesEl.scrollTop = messagesEl.scrollHeight;
      }

      /* ── Diff rendering ───────────────────────────────────────── */
      function renderDiffLines(diffText) {
        const pre = document.createElement("div");
        pre.className = "diff-view";

        diffText.split("\\n").forEach(function (line) {
          const lineEl = document.createElement("div");

          if (line.indexOf("+++") === 0 || line.indexOf("---") === 0) {
            lineEl.className = "diff-line";
          } else if (line.indexOf("@@") === 0) {
            lineEl.className = "diff-line hunk";
          } else if (line.indexOf("+") === 0) {
            lineEl.className = "diff-line add";
          } else if (line.indexOf("-") === 0) {
            lineEl.className = "diff-line remove";
          } else {
            lineEl.className = "diff-line";
          }

          lineEl.textContent = line;
          pre.appendChild(lineEl);
        });

        return pre;
      }

      /* ── v3 patch batch card ──────────────────────────────────── */
      function appendPatchBatch(files) {
        showChat();

        const container = document.createElement("div");
        container.className = "patch-batch";

        // v3 card header
        const cardTop = document.createElement("div");
        cardTop.className = "patch-batch-card-top";
        cardTop.innerHTML =
          '<div class="patch-card-ic">\\uD83D\\uDCDD</div>' +
          '<div><div class="patch-batch-title">Pending Changes</div>' +
          '<div class="patch-batch-sub">' +
          files.length + ' file' + (files.length === 1 ? '' : 's') +
          ' &#x2014; approve to write to disk</div></div>';
        container.appendChild(cardTop);

        files.forEach(function (file) {
          const fileEl = document.createElement("div");
          fileEl.className = "patch-file";

          const fileHeader = document.createElement("div");
          fileHeader.className = "patch-file-header";

          const pathEl = document.createElement("span");
          pathEl.className = "patch-file-path";
          pathEl.textContent = file.path;
          fileHeader.appendChild(pathEl);

          if (file.isNewFile) {
            const newBadge = document.createElement("span");
            newBadge.className = "patch-file-new";
            newBadge.textContent = "new file";
            fileHeader.appendChild(newBadge);
          }

          const badge = document.createElement("span");
          badge.className = "patch-file-badge";
          badge.innerHTML =
            '<span class="additions">+' + file.additions + "</span> " +
            '<span class="removals">-' + file.removals + "</span>";
          fileHeader.appendChild(badge);

          const copyButton = document.createElement("button");
          copyButton.className = "patch-file-copy";
          copyButton.textContent = "Copy diff";
          copyButton.addEventListener("click", function () {
            vscode.postMessage({ type: "copyDiff", text: file.diff });
          });
          fileHeader.appendChild(copyButton);

          fileEl.appendChild(fileHeader);

          if (file.collapsed) {
            const details = document.createElement("details");
            const summary = document.createElement("summary");
            summary.textContent = "Show diff";
            details.appendChild(summary);
            details.appendChild(renderDiffLines(file.diff));
            fileEl.appendChild(details);
          } else {
            fileEl.appendChild(renderDiffLines(file.diff));
          }

          container.appendChild(fileEl);
        });

        const actions = document.createElement("div");
        actions.className = "patch-actions";

        const approveButton = document.createElement("button");
        approveButton.textContent = "Approve";
        const rejectButton = document.createElement("button");
        rejectButton.className = "secondary";
        rejectButton.textContent = "Reject";

        function decide(decision) {
          approveButton.disabled = true;
          rejectButton.disabled = true;
          vscode.postMessage({ type: "patchDecision", decision: decision });
        }

        approveButton.addEventListener("click", function () {
          decide("approve");
        });
        rejectButton.addEventListener("click", function () {
          decide("reject");
        });

        actions.appendChild(approveButton);
        actions.appendChild(rejectButton);
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
          if (inputEl.disabled) {
            return;
          }
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
        } else if (data.type === "startStream") {
          startStream();
        } else if (data.type === "appendChunk") {
          appendChunk(data.chunk);
        } else if (data.type === "finalizeStream") {
          finalizeStream(data.html, data.timestamp);
        } else if (data.type === "showPlan") {
          appendPlan(data.steps);
        } else if (data.type === "showPatchBatch") {
          appendPatchBatch(data.files);
        } else if (data.type === "loading") {
          setLoading(!!data.show);
        } else if (data.type === "timeline") {
          updateTimeline(data.stage, data.labels);
        } else if (data.type === "executionState") {
          updateExecutionState(data.state);
        } else if (data.type === "progress") {
          updateProgress(data.event);
        }
      });
    })();
  </script>
</body>
</html>`;
}
