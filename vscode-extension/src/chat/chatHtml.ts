/**
 * Static HTML for the Pearl chat webview — v4 visual design.
 *
 * Three-panel layout: left sidebar (nav + sessions) | main chat | right panel
 * (Current Run, Tools, Files Changed, Diff Preview, Activity).
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
  "Find and fix a bug",
  "Add a new feature",
  "Write tests",
];

export function getChatHtml(): string {
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<style>
  /* ── Design tokens ─────────────────────────────────────────────── */
  :root {
    /* VS Code theme anchors (required by test coverage) */
    --p-bg:      var(--vscode-editor-background);
    --p-primary: var(--vscode-button-background);
    /* Pearl accent */
    --accent:       #7C3AED;
    --accent-dim:   rgba(124,58,237,.12);
    --accent-glow:  rgba(124,58,237,.25);
    --sidebar-bg:   #111127;
    --main-bg:      #0a0a16;
    --panel-bg:     #0f0f1f;
    --surface:      #181830;
    --elevated:     #1e1e38;
    --border:       #1f1f3a;
    --border-dim:   #16162c;
    --text:         #e4e4f4;
    --muted:        #6b6b90;
    --muted2:       #4a4a70;
    --green:        #4ade80;
    --red:          #f87171;
    --amber:        #fbbf24;
    --blue:         #60a5fa;
    --radius:       10px;
    --font:         var(--vscode-font-family, -apple-system, 'Segoe UI Variable', 'Segoe UI', system-ui, sans-serif);
    --mono:         var(--vscode-editor-font-family, 'Cascadia Code', 'JetBrains Mono', ui-monospace, monospace);
    --fs:           var(--vscode-font-size, 13px);
  }

  /* VS Code theme overrides */
  :root[data-theme="light"] {
    --sidebar-bg:  #f0f0f8;
    --main-bg:     #ffffff;
    --panel-bg:    #f5f5fc;
    --surface:     #eaeaf4;
    --elevated:    #e4e4ef;
    --border:      #d0d0e4;
    --border-dim:  #d8d8ec;
    --text:        #18182c;
    --muted:       #5a5a88;
    --muted2:      #8a8ab0;
    --accent-dim:  rgba(124,58,237,.08);
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body {
    height: 100%;
    overflow: hidden;
    font-family: var(--font);
    font-size: var(--fs);
    color: var(--text);
    background: var(--main-bg);
  }

  /* ── Three-column app shell ───────────────────────────────────── */
  #appShell {
    display: grid;
    grid-template-columns: 200px 1fr 264px;
    height: 100vh;
    overflow: hidden;
  }

  /* ── Left Sidebar ─────────────────────────────────────────────── */
  #sidebar {
    background: var(--sidebar-bg);
    border-right: 1px solid var(--border-dim);
    display: flex;
    flex-direction: column;
    overflow: hidden;
    user-select: none;
  }

  .sidebar-top {
    padding: 14px 12px 10px;
    flex-shrink: 0;
  }

  .pearl-logo {
    display: flex;
    align-items: center;
    gap: 8px;
    font-weight: 700;
    font-size: 14px;
    margin-bottom: 14px;
  }
  .logo-gem {
    width: 22px; height: 22px;
    border-radius: 6px;
    background: radial-gradient(135deg at 35% 28%, #fff 0%, #ddd0ff 22%, #b49af8 58%, #6d3fe0 100%);
    box-shadow: 0 0 10px var(--accent-glow);
    flex-shrink: 0;
  }
  .logo-name { letter-spacing: -.2px; }
  .logo-ai {
    font-size: 10px;
    font-weight: 500;
    color: var(--accent);
    background: var(--accent-dim);
    border: 1px solid rgba(124,58,237,.25);
    border-radius: 4px;
    padding: 1px 5px;
    margin-left: 2px;
  }

  #newConvBtn {
    width: 100%;
    padding: 7px 10px;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--elevated);
    color: var(--text);
    font-family: var(--font);
    font-size: 12px;
    font-weight: 500;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 6px;
    transition: background .15s, border-color .15s;
    margin-bottom: 14px;
  }
  #newConvBtn:hover { background: var(--surface); border-color: var(--accent); }
  .new-conv-key {
    margin-left: auto;
    font-size: 10px;
    color: var(--muted);
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 3px;
    padding: 1px 4px;
  }

  /* Nav items */
  #mainNav { display: flex; flex-direction: column; gap: 1px; }
  .nav-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 8px;
    border-radius: 7px;
    cursor: pointer;
    font-size: 12.5px;
    color: var(--muted);
    transition: background .12s, color .12s;
    border: none;
    background: none;
    font-family: var(--font);
    width: 100%;
    text-align: left;
  }
  .nav-item:hover { background: var(--elevated); color: var(--text); }
  .nav-item.active { background: var(--accent-dim); color: var(--accent); font-weight: 600; }
  .nav-icon { font-size: 14px; width: 16px; text-align: center; flex-shrink: 0; }

  /* Recent sessions */
  #recentSessions {
    flex: 1;
    overflow-y: auto;
    padding: 10px 12px 0;
    scrollbar-width: thin;
    scrollbar-color: var(--border) transparent;
  }
  .section-label {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .06em;
    color: var(--muted2);
    margin-bottom: 6px;
    padding-left: 2px;
  }
  .session-item {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 6px 8px;
    border-radius: 7px;
    cursor: pointer;
    margin-bottom: 2px;
    transition: background .12s;
  }
  .session-item:hover { background: var(--elevated); }
  .session-item.active { background: var(--accent-dim); }
  .session-title { font-size: 12px; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .session-time { font-size: 10px; color: var(--muted); }
  .view-all {
    display: block;
    font-size: 11px;
    color: var(--accent);
    padding: 8px 8px 4px;
    text-decoration: none;
    cursor: pointer;
  }
  .view-all:hover { text-decoration: underline; }

  /* Sidebar bottom */
  #sidebarBottom {
    padding: 10px 12px;
    border-top: 1px solid var(--border-dim);
    flex-shrink: 0;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .user-avatar {
    width: 28px; height: 28px;
    border-radius: 50%;
    background: radial-gradient(135deg at 35% 28%, #fff 0%, #ddd0ff 22%, #b49af8 58%, #6d3fe0 100%);
    border: 1px solid var(--accent);
    display: flex; align-items: center; justify-content: center;
    font-size: 12px; flex-shrink: 0; font-weight: 700; color: #fff;
  }
  .user-info { flex: 1; min-width: 0; }
  .user-name { font-size: 12px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .user-plan {
    font-size: 9.5px;
    color: var(--accent);
    background: var(--accent-dim);
    border: 1px solid rgba(124,58,237,.25);
    border-radius: 4px;
    padding: 0 5px;
    display: inline-block;
  }
  .conn-dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 4px var(--green);
    animation: dot-pulse 2s ease-in-out infinite;
    flex-shrink: 0;
  }
  @keyframes dot-pulse { 0%,100%{opacity:1} 50%{opacity:.4} }

  /* ── Main content ─────────────────────────────────────────────── */
  #mainContent {
    display: flex;
    flex-direction: column;
    overflow: hidden;
    background: var(--main-bg);
  }

  /* Welcome screen */
  #${WELCOME_ID} {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 32px 24px 16px;
    text-align: center;
    overflow-y: auto;
  }
  .welcome-greeting {
    font-size: 20px;
    font-weight: 700;
    margin-bottom: 6px;
  }
  .welcome-sub { font-size: 13px; color: var(--muted); margin-bottom: 24px; }

  /* Input in welcome screen */
  .welcome-input-wrap {
    width: 100%;
    max-width: 520px;
    border: 1px solid var(--border);
    border-radius: 14px;
    background: var(--surface);
    overflow: hidden;
    margin-bottom: 14px;
    transition: border-color .2s, box-shadow .2s;
  }
  .welcome-input-wrap:focus-within {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-dim);
  }
  .welcome-input-main {
    display: flex;
    align-items: center;
    padding: 10px 14px;
    gap: 8px;
  }
  .welcome-input-main input {
    flex: 1;
    background: none;
    border: none;
    outline: none;
    font-family: var(--font);
    font-size: 13.5px;
    color: var(--text);
  }
  .welcome-input-main input::placeholder { color: var(--muted); }
  .welcome-input-icons {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 0 12px 10px;
    border-top: 1px solid var(--border);
    padding-top: 8px;
  }
  .input-icon-btn {
    background: none;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 3px 8px;
    font-size: 11px;
    color: var(--muted);
    cursor: pointer;
    font-family: var(--font);
    display: flex; align-items: center; gap: 4px;
    transition: background .12s, color .12s;
  }
  .input-icon-btn:hover { background: var(--elevated); color: var(--text); }
  .welcome-send-btn {
    margin-left: auto;
    width: 30px; height: 30px;
    border-radius: 8px;
    border: none;
    background: var(--accent);
    color: #fff;
    font-size: 15px;
    cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    transition: opacity .15s, transform .1s;
  }
  .welcome-send-btn:hover { opacity: .88; }
  .welcome-send-btn:active { transform: scale(.92); }

  /* Suggested action chips */
  #examplePrompts {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    justify-content: center;
    max-width: 480px;
    margin-bottom: 10px;
  }
  .example-prompt {
    background: var(--surface);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 20px;
    padding: 6px 14px;
    cursor: pointer;
    font-size: 12px;
    font-family: var(--font);
    transition: border-color .16s, background .16s;
  }
  .example-prompt:hover {
    border-color: rgba(124,58,237,.5);
    background: var(--accent-dim);
    color: var(--text);
  }

  /* ── Message list ─────────────────────────────────────────────── */
  #${MESSAGES_CONTAINER_ID} {
    flex: 1;
    overflow-y: auto;
    padding: 14px 16px;
    display: none;
    scrollbar-width: thin;
    scrollbar-color: var(--border) transparent;
  }
  #${MESSAGES_CONTAINER_ID}.visible { display: block; }

  .message-row {
    display: flex;
    gap: 9px;
    margin-bottom: 14px;
    max-width: 100%;
  }
  .message-row.user { flex-direction: row-reverse; }
  .message-row.assistant, .message-row.error { flex-direction: row; }

  .msg-av {
    width: 26px; height: 26px;
    border-radius: 7px;
    background: var(--elevated);
    border: 1px solid var(--border);
    display: flex; align-items: center; justify-content: center;
    font-size: 12px; flex-shrink: 0;
    align-self: flex-start; margin-top: 2px;
  }
  .message-row.assistant .msg-av,
  .message-row.error .msg-av {
    background: radial-gradient(135deg at 35% 28%, #fff 0%, #ddd0ff 22%, #b49af8 58%, #6d3fe0 100%);
    border-color: rgba(124,58,237,.4);
    box-shadow: 0 0 6px var(--accent-glow);
  }

  .bubble {
    padding: 9px 13px;
    border-radius: 4px 12px 12px 12px;
    background: var(--surface);
    border: 1px solid var(--border);
    font-size: 13px;
    line-height: 1.65;
    max-width: calc(100% - 80px);
    min-width: 0;
    overflow-wrap: break-word;
  }
  .bubble.user {
    border-radius: 12px 4px 12px 12px;
    background: rgba(124,58,237,.12);
    border-color: rgba(124,58,237,.28);
  }
  .bubble.assistant { border-color: rgba(124,58,237,.18); }
  .bubble.error {
    background: rgba(248,113,113,.08);
    border-color: var(--red);
  }
  .bubble .error-icon {
    font-size: 11px; font-weight: 600;
    color: var(--red); margin-bottom: 4px;
  }
  .bubble .content { white-space: normal; }
  .bubble .content p { margin: 0 0 6px 0; }
  .bubble .content p:last-child { margin-bottom: 0; }
  .bubble .content pre {
    background: rgba(127,127,127,.12);
    padding: 8px; border-radius: 5px;
    overflow-x: auto; font-family: var(--mono);
    font-size: 11.5px; border: 1px solid var(--border); margin: 5px 0;
  }
  .bubble .content code {
    background: rgba(127,127,127,.12);
    font-family: var(--mono); font-size: 11.5px;
    padding: 1px 5px; border-radius: 4px; color: #a78bfa;
  }
  .bubble .content pre code { background: none; padding: 0; color: inherit; }
  .bubble .content ul, .bubble .content ol { margin: 4px 0; padding-left: 18px; }
  .bubble .content table { border-collapse: collapse; margin: 4px 0; font-size: 12px; }
  .bubble .content th, .bubble .content td {
    border: 1px solid var(--border); padding: 4px 8px;
  }
  .bubble .meta { font-size: 10px; color: var(--muted); margin-top: 5px; opacity: .7; }

  /* ── Loading dots ─────────────────────────────────────────────── */
  .loading-row { display: flex; gap: 9px; margin-bottom: 12px; }
  .loading-av {
    width: 26px; height: 26px; border-radius: 7px;
    background: radial-gradient(135deg at 35% 28%, #fff 0%, #ddd0ff 22%, #b49af8 58%, #6d3fe0 100%);
    border: 1px solid rgba(124,58,237,.4);
    display: flex; align-items: center; justify-content: center;
    font-size: 12px; flex-shrink: 0;
  }
  .loading-bubble {
    padding: 11px 14px; border-radius: 4px 12px 12px 12px;
    background: var(--surface); border: 1px solid rgba(124,58,237,.18);
    display: flex; gap: 5px; align-items: center;
  }
  .dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: #a78bfa; opacity: .4;
    animation: pearl-loading-bounce 1s infinite ease-in-out;
  }
  .dot:nth-child(2) { animation-delay: .15s; }
  .dot:nth-child(3) { animation-delay: .30s; }
  @keyframes pearl-loading-bounce {
    0%,80%,100% { opacity: .3; transform: scale(.75); }
    40% { opacity: 1; transform: scale(1); }
  }

  /* ── Progress status ──────────────────────────────────────────── */
  .progress-status {
    display: flex; align-items: center; gap: 8px;
    padding: 7px 11px; margin-bottom: 12px;
    border-radius: 8px; background: var(--surface);
    border: 1px solid var(--border);
    font-size: 11.5px; color: var(--muted);
  }
  .progress-spinner {
    width: 10px; height: 10px; border-radius: 50%;
    border: 2px solid var(--accent); border-top-color: transparent;
    animation: progress-spin .7s linear infinite; flex-shrink: 0;
  }
  @keyframes progress-spin { to { transform: rotate(360deg); } }
  .progress-step { opacity: .75; flex-shrink: 0; }
  .progress-action { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  /* ── Timeline ─────────────────────────────────────────────────── */
  .timeline {
    display: flex; flex-wrap: wrap; align-items: center; gap: 5px;
    padding: 7px 11px; margin-bottom: 12px; border-radius: 8px;
    background: var(--surface); border: 1px solid var(--border); font-size: 11px;
  }
  .timeline-step { opacity: .45; }
  .timeline-step.active { opacity: 1; font-weight: 600; color: var(--accent); }
  .timeline-step.done { opacity: .75; }
  .timeline-sep { opacity: .3; }

  /* ── Execution state ──────────────────────────────────────────── */
  .execution-state {
    display: flex; align-items: center; gap: 6px;
    padding: 7px 11px; margin-bottom: 12px; border-radius: 8px;
    background: rgba(124,58,237,.08); border: 1px solid rgba(124,58,237,.3);
    font-size: 11.5px; font-weight: 600; color: var(--accent);
  }

  /* ── Plan card ────────────────────────────────────────────────── */
  .plan {
    border-radius: var(--radius);
    border: 1px solid rgba(251,191,36,.3);
    background: rgba(251,191,36,.04);
    margin-bottom: 12px; overflow: hidden;
  }
  .plan-card-top {
    padding: 10px 13px; display: flex; align-items: center; gap: 9px;
    border-bottom: 1px solid rgba(251,191,36,.18);
  }
  .plan-card-ic {
    width: 30px; height: 30px; border-radius: 7px;
    background: rgba(251,191,36,.16); border: 1px solid rgba(251,191,36,.32);
    display: flex; align-items: center; justify-content: center;
    font-size: 14px; flex-shrink: 0;
  }
  .plan-card-title { font-size: 12.5px; font-weight: 700; }
  .plan-card-sub { font-size: 10.5px; color: var(--muted); margin-top: 2px; }
  .plan-header { display: none; }
  .plan-step {
    display: flex; align-items: flex-start; gap: 9px;
    margin: 6px 13px; padding: 7px 9px;
    border-radius: 7px; background: var(--elevated); border: 1px solid var(--border);
  }
  .plan-step-num {
    min-width: 20px; height: 20px; border-radius: 50%;
    background: rgba(124,58,237,.14); border: 1px solid rgba(124,58,237,.35);
    color: var(--accent); font-size: 10px; font-weight: 700;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0; margin-top: 1px;
  }
  .plan-step-inner { flex: 1; min-width: 0; }
  .plan-step-title { font-weight: 600; font-size: 12.5px; margin-bottom: 4px; }
  .plan pre {
    white-space: pre-wrap; word-wrap: break-word; margin: 4px 0 0 0;
    padding: 5px 8px; background: rgba(127,127,127,.12);
    border-radius: 4px; font-family: var(--mono); font-size: 11px;
    border: 1px solid var(--border);
  }
  .plan-actions { margin: 10px 13px 13px; display: flex; gap: 8px; }
  .plan-actions button {
    flex: 1; padding: 7px 0; border-radius: 7px; border: none;
    background: var(--accent); color: #fff; font-family: var(--font);
    font-size: 12px; font-weight: 500; cursor: pointer;
    transition: opacity .18s; text-align: center;
  }
  .plan-actions button:hover { opacity: .88; }
  .plan-actions button.secondary {
    background: rgba(248,113,113,.09); border: 1px solid rgba(248,113,113,.28);
    color: var(--red);
  }
  .plan-actions button:disabled { opacity: .4; cursor: not-allowed; }

  /* ── Patch batch card ─────────────────────────────────────────── */
  .patch-batch {
    border-radius: var(--radius); border: 1px solid rgba(74,222,128,.28);
    background: rgba(74,222,128,.04); margin-bottom: 12px; overflow: hidden;
  }
  .patch-batch-card-top {
    padding: 10px 13px; display: flex; align-items: center; gap: 9px;
    border-bottom: 1px solid rgba(74,222,128,.18);
  }
  .patch-card-ic {
    width: 30px; height: 30px; border-radius: 7px;
    background: rgba(74,222,128,.16); border: 1px solid rgba(74,222,128,.32);
    display: flex; align-items: center; justify-content: center;
    font-size: 14px; flex-shrink: 0;
  }
  .patch-batch-header { display: none; }
  .patch-batch-title { font-size: 12.5px; font-weight: 700; }
  .patch-batch-sub { font-size: 10.5px; color: var(--muted); margin-top: 2px; }
  .patch-file {
    margin: 6px 13px; padding: 8px 10px;
    border-radius: 7px; background: var(--elevated); border: 1px solid var(--border);
  }
  .patch-file-header {
    display: flex; align-items: center; gap: 7px; flex-wrap: wrap; margin-bottom: 5px;
  }
  .patch-file-path { font-weight: 600; font-family: var(--mono); font-size: 11.5px; }
  .patch-file-badge { font-size: 11px; font-family: var(--mono); }
  .patch-file-badge .additions { color: var(--green); }
  .patch-file-badge .removals { color: var(--red); }
  .patch-file-new {
    font-size: 10px; padding: 1px 6px; border-radius: 8px;
    background: rgba(124,58,237,.14); border: 1px solid rgba(124,58,237,.3);
    color: var(--accent);
  }
  .patch-file-copy {
    margin-left: auto; background: none; border: 1px solid var(--border);
    color: var(--muted); border-radius: 5px; padding: 2px 8px;
    cursor: pointer; font-size: 10.5px; font-family: var(--font); transition: background .15s;
  }
  .patch-file-copy:hover { background: var(--surface); color: var(--text); }
  .diff-view {
    font-family: var(--mono); font-size: 11px; border-radius: 5px;
    border: 1px solid var(--border); overflow: hidden; margin-top: 5px;
  }
  .diff-line { padding: 1.5px 10px; white-space: pre; line-height: 1.7; }
  .diff-line.add { background: rgba(74,222,128,.08); color: var(--green); }
  .diff-line.remove { background: rgba(248,113,113,.08); color: var(--red); opacity: .8; }
  .diff-line.hunk { background: rgba(96,165,250,.08); color: var(--blue); font-style: italic; }
  .patch-actions { margin: 10px 13px 13px; display: flex; gap: 8px; }
  .patch-actions button {
    flex: 1; padding: 7px 0; border-radius: 7px; border: none;
    background: var(--accent); color: #fff; font-family: var(--font);
    font-size: 12px; font-weight: 500; cursor: pointer; transition: opacity .18s; text-align: center;
  }
  .patch-actions button:hover { opacity: .88; }
  .patch-actions button.secondary {
    background: rgba(248,113,113,.09); border: 1px solid rgba(248,113,113,.28); color: var(--red);
  }
  .patch-actions button:disabled { opacity: .4; cursor: not-allowed; }

  /* ── Input row (compact, shown when chat is active) ───────────── */
  #inputRow {
    display: flex;
    flex-direction: column;
    border-top: 1px solid var(--border);
    background: var(--surface);
    flex-shrink: 0;
  }
  #inputRow.hidden { display: none; }
  .input-main {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 12px 0;
  }
  #${MESSAGE_INPUT_ID} {
    flex: 1; min-width: 0;
    background: var(--elevated); color: var(--text);
    border: 1px solid var(--border); border-radius: var(--radius);
    padding: 8px 12px; font-family: var(--font); font-size: 13px;
    outline: none; transition: border-color .2s, box-shadow .2s;
  }
  #${MESSAGE_INPUT_ID}::placeholder { color: var(--muted); }
  #${MESSAGE_INPUT_ID}:focus {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px var(--accent-dim);
  }
  #${MESSAGE_INPUT_ID}:disabled { opacity: .55; cursor: not-allowed; }
  #${SEND_BUTTON_ID} {
    width: 34px; height: 34px; border-radius: 9px; border: none;
    background: var(--accent); color: #fff; cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    font-size: 15px; transition: opacity .18s, transform .12s; flex-shrink: 0;
  }
  #${SEND_BUTTON_ID}:hover { opacity: .88; }
  #${SEND_BUTTON_ID}:active { transform: scale(.92); }
  #${SEND_BUTTON_ID}:disabled { opacity: .35; cursor: not-allowed; }
  .input-bar-icons {
    display: flex; gap: 6px; padding: 6px 12px 8px;
  }

  /* ── Right panel ──────────────────────────────────────────────── */
  #rightPanel {
    background: var(--panel-bg);
    border-left: 1px solid var(--border-dim);
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .rp-header {
    padding: 10px 12px;
    border-bottom: 1px solid var(--border-dim);
    display: flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
  }
  .rp-title { font-size: 12.5px; font-weight: 700; flex: 1; }
  .badge {
    font-size: 10px; font-weight: 600;
    padding: 2px 7px; border-radius: 10px;
  }
  .badge-active {
    background: rgba(74,222,128,.15); color: var(--green);
    border: 1px solid rgba(74,222,128,.3);
  }
  .badge-idle {
    background: var(--elevated); color: var(--muted);
    border: 1px solid var(--border);
  }

  #rightScroll {
    flex: 1; overflow-y: auto;
    scrollbar-width: thin; scrollbar-color: var(--border) transparent;
  }

  /* Run card */
  .rp-section { padding: 10px 12px; border-bottom: 1px solid var(--border-dim); }
  .rp-section-title {
    font-size: 10px; font-weight: 700; letter-spacing: .05em;
    color: var(--muted2); margin-bottom: 7px;
  }
  .run-name {
    font-size: 12px; font-weight: 600; margin-bottom: 3px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .run-meta { font-size: 10.5px; color: var(--muted); margin-bottom: 8px; }
  .progress-bar-wrap {
    height: 4px; background: var(--elevated); border-radius: 4px; overflow: hidden;
  }
  .progress-bar-fill {
    height: 100%; background: var(--accent);
    border-radius: 4px; transition: width .4s ease;
  }

  /* Tool call rows */
  .tool-row {
    display: flex; align-items: center; gap: 7px;
    padding: 5px 0; font-size: 11.5px;
  }
  .tool-row + .tool-row { border-top: 1px solid var(--border-dim); }
  .tool-icon {
    width: 22px; height: 22px; border-radius: 5px;
    background: var(--elevated); border: 1px solid var(--border);
    display: flex; align-items: center; justify-content: center;
    font-size: 11px; flex-shrink: 0;
  }
  .tool-info { flex: 1; min-width: 0; }
  .tool-name { font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .tool-path { font-size: 10px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-family: var(--mono); }
  .tool-status { font-size: 13px; flex-shrink: 0; }

  /* Files changed rows */
  .file-row {
    display: flex; align-items: center; gap: 6px;
    padding: 4px 0; font-size: 11.5px;
  }
  .file-row + .file-row { border-top: 1px solid var(--border-dim); }
  .file-icon { font-size: 12px; flex-shrink: 0; }
  .file-name { flex: 1; font-family: var(--mono); font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .file-stats { font-size: 10.5px; font-family: var(--mono); flex-shrink: 0; }
  .stat-add { color: var(--green); }
  .stat-del { color: var(--red); }

  /* Right panel diff */
  .rp-diff-header {
    font-size: 10.5px; font-family: var(--mono);
    color: var(--muted); margin-bottom: 4px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .rp-diff-view {
    font-family: var(--mono); font-size: 10px; border-radius: 5px;
    border: 1px solid var(--border); overflow: hidden; max-height: 160px; overflow-y: auto;
  }
  .rp-diff-line { padding: 1px 8px; white-space: pre; line-height: 1.6; }
  .rp-diff-line.add { background: rgba(74,222,128,.08); color: var(--green); }
  .rp-diff-line.remove { background: rgba(248,113,113,.08); color: var(--red); }
  .rp-diff-line.hunk { background: rgba(96,165,250,.08); color: var(--blue); }

  /* Activity feed */
  .activity-item {
    display: flex; align-items: flex-start; gap: 7px;
    padding: 4px 0; font-size: 11px; color: var(--muted);
  }
  .activity-item + .activity-item { border-top: 1px solid var(--border-dim); }
  .activity-icon { flex-shrink: 0; width: 14px; text-align: center; margin-top: 1px; }
  .activity-text { flex: 1; line-height: 1.4; }
  .activity-time { font-size: 9.5px; color: var(--muted2); flex-shrink: 0; margin-top: 1px; }

  /* Empty state */
  .rp-empty {
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    padding: 24px 12px; text-align: center; color: var(--muted2);
    font-size: 11.5px; gap: 6px;
  }
  .rp-empty-icon { font-size: 22px; }
</style>
</head>
<body>
<div id="appShell">

  <!-- ── Left Sidebar ─────────────────────────────────────────── -->
  <aside id="sidebar">
    <div class="sidebar-top">
      <div class="pearl-logo">
        <div class="logo-gem"></div>
        <span class="logo-name">Pearl</span>
        <span class="logo-ai">AI</span>
      </div>

      <button id="newConvBtn">
        &#x2B; New Conversation
        <span class="new-conv-key">&#x2318;K</span>
      </button>

      <nav id="mainNav">
        <button class="nav-item active" data-nav="chat">
          <span class="nav-icon">&#x1F4AC;</span> Chat
        </button>
        <button class="nav-item" data-nav="projects">
          <span class="nav-icon">&#x1F4C1;</span> Projects
        </button>
        <button class="nav-item" data-nav="repository">
          <span class="nav-icon">&#x1F4BB;</span> Repository
        </button>
        <button class="nav-item" data-nav="timeline">
          <span class="nav-icon">&#x23F3;</span> Timeline
        </button>
        <button class="nav-item" data-nav="sessions">
          <span class="nav-icon">&#x1F4C6;</span> Sessions
        </button>
        <button class="nav-item" data-nav="settings">
          <span class="nav-icon">&#x2699;&#xFE0F;</span> Settings
        </button>
      </nav>
    </div>

    <div id="recentSessions">
      <div class="section-label">RECENT SESSIONS</div>
      <div class="session-item active">
        <div class="session-title">Current session</div>
        <div class="session-time">Just now</div>
      </div>
      <span class="view-all">View all sessions &#x2192;</span>
    </div>

    <div id="sidebarBottom">
      <div class="user-avatar">S</div>
      <div class="user-info">
        <div class="user-name">Sujith</div>
        <span class="user-plan">Pro</span>
      </div>
      <div class="conn-dot" title="Connected"></div>
    </div>
  </aside>

  <!-- ── Main content ──────────────────────────────────────────── -->
  <main id="mainContent">

    <!-- Welcome screen (shown initially) -->
    <div id="${WELCOME_ID}">
      <div class="welcome-greeting">&#x1F44B; Hi, how can I help you today?</div>
      <p class="welcome-title">Welcome to Pearl</p>
      <p class="welcome-sub">Your autonomous AI coding assistant — explain, fix, build, and test.</p>
      <ul class="welcome-caps" style="display:none">
        <li>Explain code &amp; answer questions</li>
        <li>Fix bugs in your codebase</li>
        <li>Edit files with your approval</li>
        <li>Run tests &amp; report results</li>
        <li>Generate projects from scratch</li>
      </ul>

      <div class="welcome-input-wrap">
        <div class="welcome-input-main">
          <input id="${MESSAGE_INPUT_ID}" type="text" placeholder="Ask Pearl anything about your code…" autocomplete="off" />
          <button id="${SEND_BUTTON_ID}" title="Send">&#x2191;</button>
        </div>
        <div class="welcome-input-icons">
          <button class="input-icon-btn">&#x40; Mention</button>
          <button class="input-icon-btn">&#x1F4CE; Attach</button>
          <button class="input-icon-btn">&#x3C;/&#x3E; Code</button>
        </div>
      </div>

      <div id="examplePrompts">
        ${EXAMPLE_PROMPTS.map(
          (p) =>
            `<button class="example-prompt" data-prompt="${p}">${p}</button>`
        ).join("\n        ")}
      </div>
    </div>

    <!-- Chat messages (shown when conversation starts) -->
    <div id="${MESSAGES_CONTAINER_ID}"></div>

    <!-- Compact input row (shown below messages once chat is active) -->
    <div id="inputRow" class="hidden">
      <div class="input-main">
        <input id="chatInput" type="text" placeholder="Message Pearl…" autocomplete="off" />
        <button id="chatSendBtn" title="Send">&#x2191;</button>
      </div>
      <div class="input-bar-icons">
        <button class="input-icon-btn">&#x40;</button>
        <button class="input-icon-btn">&#x1F4CE;</button>
        <button class="input-icon-btn">&#x3C;/&#x3E;</button>
      </div>
    </div>
  </main>

  <!-- ── Right panel ───────────────────────────────────────────── -->
  <aside id="rightPanel">
    <div class="rp-header">
      <span class="rp-title">Current Run</span>
      <span class="badge badge-idle" id="runBadge">Idle</span>
    </div>
    <div id="rightScroll">

      <!-- Run progress section -->
      <div class="rp-section" id="runSection">
        <div class="run-name" id="runName">No active run</div>
        <div class="run-meta" id="runMeta">Start a conversation to begin</div>
        <div class="progress-bar-wrap">
          <div class="progress-bar-fill" id="runProgress" style="width:0%"></div>
        </div>
      </div>

      <!-- Tools section -->
      <div class="rp-section" id="toolsSection">
        <div class="rp-section-title" id="toolsTitle">TOOLS</div>
        <div id="toolsList">
          <div class="rp-empty">
            <div class="rp-empty-icon">&#x1F527;</div>
            <span>Tool calls will appear here</span>
          </div>
        </div>
      </div>

      <!-- Files changed section -->
      <div class="rp-section" id="filesSection">
        <div class="rp-section-title" id="filesTitle">FILES CHANGED</div>
        <div id="filesList">
          <div class="rp-empty">
            <div class="rp-empty-icon">&#x1F4C4;</div>
            <span>Changed files will appear here</span>
          </div>
        </div>
      </div>

      <!-- Diff preview section -->
      <div class="rp-section" id="diffSection" style="display:none">
        <div class="rp-section-title">DIFF PREVIEW</div>
        <div class="rp-diff-header" id="diffFileName"></div>
        <div class="rp-diff-view" id="rpDiffView"></div>
      </div>

      <!-- Activity feed -->
      <div class="rp-section" id="activitySection">
        <div class="rp-section-title">ACTIVITY</div>
        <div id="activityList">
          <div class="rp-empty">
            <div class="rp-empty-icon">&#x26A1;</div>
            <span>Activity will appear here</span>
          </div>
        </div>
      </div>

    </div>
  </aside>

</div>

<script>
  (function () {
    const vscode = acquireVsCodeApi();

    // ── DOM refs (welcome flow) ───────────────────────────────────
    const welcomeEl   = document.getElementById("${WELCOME_ID}");
    const messagesEl  = document.getElementById("${MESSAGES_CONTAINER_ID}");
    const inputEl     = document.getElementById("${MESSAGE_INPUT_ID}");
    const sendButton  = document.getElementById("${SEND_BUTTON_ID}");

    // Compact chat input (below messages)
    const inputRowEl   = document.getElementById("inputRow");
    const chatInputEl  = document.getElementById("chatInput");
    const chatSendBtn  = document.getElementById("chatSendBtn");

    // Right panel refs
    const runBadgeEl    = document.getElementById("runBadge");
    const runNameEl     = document.getElementById("runName");
    const runMetaEl     = document.getElementById("runMeta");
    const runProgressEl = document.getElementById("runProgress");
    const toolsTitle    = document.getElementById("toolsTitle");
    const toolsListEl   = document.getElementById("toolsList");
    const filesTitle    = document.getElementById("filesTitle");
    const filesListEl   = document.getElementById("filesList");
    const diffSectionEl = document.getElementById("diffSection");
    const diffFileEl    = document.getElementById("diffFileName");
    const rpDiffViewEl  = document.getElementById("rpDiffView");
    const activityListEl= document.getElementById("activityList");

    let loadingEl       = null;
    let timelineEl      = null;
    let executionStateEl= null;
    let progressEl      = null;

    // Right panel state
    let toolCallCount = 0;
    let toolDoneCount = 0;
    let fileChangeCount = 0;
    let activityEmpty = true;
    let toolsEmpty    = true;
    let filesEmpty    = true;
    let currentStep   = 0;
    let totalSteps    = 0;

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
      ["planning",         "Planning..."],
      ["plan_ready",       "Plan Ready"],
      ["waiting_approval", "Waiting for Approval"],
      ["running_tool",     "Running Tool..."],
      ["completed",        "Completed"]
    ];

    const EXECUTION_STATE_LABELS = {
      awaiting_approval: "Awaiting Approval",
      applying_patches:  "Applying Patches...",
      resuming:          "Resuming Execution...",
      completed:         "Completed",
      cancelled:         "Cancelled"
    };

    // ── Layout helpers ────────────────────────────────────────────
    function showChat() {
      welcomeEl.style.display = "none";
      messagesEl.classList.add("visible");
      inputRowEl.classList.remove("hidden");
    }

    function formatTime(iso) {
      try {
        return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      } catch (_) { return ""; }
    }

    // ── Right-panel helpers ───────────────────────────────────────
    function setRunActive(name) {
      runBadgeEl.textContent = "Active";
      runBadgeEl.className = "badge badge-active";
      runNameEl.textContent = name || "Running…";
      runMetaEl.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }

    function setRunIdle() {
      runBadgeEl.textContent = "Idle";
      runBadgeEl.className = "badge badge-idle";
    }

    function setProgress(pct) {
      runProgressEl.style.width = Math.min(100, Math.max(0, pct)) + "%";
    }

    function addActivity(icon, text) {
      if (activityEmpty) {
        activityListEl.innerHTML = "";
        activityEmpty = false;
      }
      const item = document.createElement("div");
      item.className = "activity-item";
      const t = formatTime(new Date().toISOString());
      item.innerHTML =
        '<span class="activity-icon">' + icon + '</span>' +
        '<span class="activity-text">' + text + '</span>' +
        '<span class="activity-time">' + t + '</span>';
      activityListEl.prepend(item);
    }

    function addToolCall(toolName, toolPath, status) {
      toolCallCount++;
      if (toolsEmpty) {
        toolsListEl.innerHTML = "";
        toolsEmpty = false;
      }
      const row = document.createElement("div");
      row.className = "tool-row";
      const statusIcon = status === "done" ? "\\u2705" : status === "failed" ? "\\u274C" : "\\u23F3";
      row.innerHTML =
        '<div class="tool-icon">\\uD83D\\uDD27</div>' +
        '<div class="tool-info">' +
          '<div class="tool-name">' + toolName + '</div>' +
          '<div class="tool-path">' + (toolPath || "") + '</div>' +
        '</div>' +
        '<span class="tool-status">' + statusIcon + '</span>';
      toolsListEl.appendChild(row);
      toolsTitle.textContent = "TOOLS (" + toolDoneCount + "/" + toolCallCount + ")";
    }

    function addFileChange(filePath, additions, removals, diff) {
      fileChangeCount++;
      if (filesEmpty) {
        filesListEl.innerHTML = "";
        filesEmpty = false;
      }
      const row = document.createElement("div");
      row.className = "file-row";
      row.innerHTML =
        '<span class="file-icon">\\uD83D\\uDCC4</span>' +
        '<span class="file-name">' + filePath + '</span>' +
        '<span class="file-stats">' +
          '<span class="stat-add">+' + additions + '</span>' +
          ' <span class="stat-del">-' + removals + '</span>' +
        '</span>';
      filesListEl.appendChild(row);
      filesTitle.textContent = "FILES CHANGED (" + fileChangeCount + ")";

      // Update diff preview with first/last file
      if (diff) {
        diffSectionEl.style.display = "";
        diffFileEl.textContent = filePath;
        rpDiffViewEl.innerHTML = "";
        diff.split("\\n").slice(0, 40).forEach(function (line) {
          const el = document.createElement("div");
          if (line.indexOf("@@") === 0)       el.className = "rp-diff-line hunk";
          else if (line.indexOf("+") === 0)   el.className = "rp-diff-line add";
          else if (line.indexOf("-") === 0)   el.className = "rp-diff-line remove";
          else                                el.className = "rp-diff-line";
          el.textContent = line;
          rpDiffViewEl.appendChild(el);
        });
      }
    }

    // ── Stream helpers ────────────────────────────────────────────
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
      if (!streamContent) return;
      streamAccum += chunk;
      streamContent.textContent = streamAccum;
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function finalizeStream(html, timestamp) {
      if (!streamRow) return;
      if (html) streamContent.innerHTML = html;
      const meta = document.createElement("div");
      meta.className = "meta";
      meta.textContent = formatTime(timestamp || new Date().toISOString());
      streamRow.querySelector(".bubble").appendChild(meta);
      streamRow = null; streamContent = null; streamAccum = "";
    }

    // ── appendMessage ─────────────────────────────────────────────
    function appendMessage(message) {
      showChat();
      const row = document.createElement("div");
      row.className = "message-row " + message.role;
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

    // ── Loading ───────────────────────────────────────────────────
    function setLoading(show) {
      if (show) {
        showChat();
        if (!loadingEl) {
          loadingEl = document.createElement("div");
          loadingEl.className = "loading-row";
          loadingEl.innerHTML =
            '<div class="loading-av">\\uD83D\\uDC8E</div>' +
            '<div class="loading-bubble">' +
            '<span class="dot"></span><span class="dot"></span><span class="dot"></span>' +
            '</div>';
          messagesEl.appendChild(loadingEl);
          messagesEl.scrollTop = messagesEl.scrollHeight;
        }
        chatInputEl.disabled = true;
        chatSendBtn.disabled = true;
        inputEl.disabled = true;
        sendButton.disabled = true;
        chatInputEl.placeholder = "Pearl is thinking\\u2026";
        inputEl.placeholder = "Pearl is thinking\\u2026";
      } else {
        if (loadingEl) { loadingEl.remove(); loadingEl = null; }
        chatInputEl.disabled = false;
        chatSendBtn.disabled = false;
        inputEl.disabled = false;
        sendButton.disabled = false;
        chatInputEl.placeholder = "Message Pearl\\u2026";
        inputEl.placeholder = "Ask Pearl anything about your code\\u2026";
      }
    }

    // ── Timeline ──────────────────────────────────────────────────
    function updateTimeline(stage, labels) {
      if (!stage) {
        if (timelineEl) { timelineEl.remove(); timelineEl = null; }
        return;
      }
      showChat();
      if (!timelineEl) {
        timelineEl = document.createElement("div");
        timelineEl.className = "timeline";
        messagesEl.appendChild(timelineEl);
      }
      const currentIndex = TIMELINE_STAGES.findIndex(function (e) { return e[0] === stage; });
      const visible = TIMELINE_STAGES.slice(0, currentIndex + 1);
      timelineEl.innerHTML = "";
      visible.forEach(function (entry, i) {
        const stepEl = document.createElement("span");
        stepEl.className = "timeline-step" + (i < currentIndex ? " done" : i === currentIndex ? " active" : "");
        stepEl.textContent = (labels && labels[entry[0]]) || entry[1];
        timelineEl.appendChild(stepEl);
        if (i < visible.length - 1) {
          const sep = document.createElement("span");
          sep.className = "timeline-sep";
          sep.textContent = "\\u2192";
          timelineEl.appendChild(sep);
        }
      });
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    // ── Plan card ─────────────────────────────────────────────────
    function appendPlan(steps) {
      showChat();
      setRunActive("Execution plan — " + steps.length + " steps");
      addActivity("\\u26A1", "Plan proposed: " + steps.length + " steps");

      const container = document.createElement("div");
      container.className = "plan";

      const cardTop = document.createElement("div");
      cardTop.className = "plan-card-top";
      cardTop.innerHTML =
        '<div class="plan-card-ic">&#x26A1;</div>' +
        '<div><div class="plan-card-title">Proposed Plan</div>' +
        '<div class="plan-card-sub">' + steps.length + ' step' + (steps.length === 1 ? '' : 's') +
        ' &#x2014; approve to execute</div></div>';
      container.appendChild(cardTop);

      totalSteps = steps.length;
      setProgress(5);

      steps.forEach(function (step) {
        const stepEl = document.createElement("div");
        stepEl.className = "plan-step";
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
        if (decision === "execute") addActivity("\\u25B6\\uFE0F", "Execution started");
        else { addActivity("\\u274C", "Plan cancelled"); setRunIdle(); setProgress(0); }
      }
      executeButton.addEventListener("click", function () { decide("execute"); });
      cancelButton.addEventListener("click", function () { decide("cancel"); });
      actions.appendChild(executeButton);
      actions.appendChild(cancelButton);
      container.appendChild(actions);
      messagesEl.appendChild(container);
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    // ── Execution state ───────────────────────────────────────────
    function updateExecutionState(state) {
      if (!state) {
        if (executionStateEl) { executionStateEl.remove(); executionStateEl = null; }
        return;
      }
      showChat();
      if (!executionStateEl) {
        executionStateEl = document.createElement("div");
        executionStateEl.className = "execution-state";
        messagesEl.appendChild(executionStateEl);
      }
      executionStateEl.textContent = EXECUTION_STATE_LABELS[state] || state;
      if (state === "completed") { setRunIdle(); setProgress(100); addActivity("\\u2705", "Run completed"); }
      else if (state === "cancelled") { setRunIdle(); addActivity("\\u274C", "Run cancelled"); }
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    // ── Progress ──────────────────────────────────────────────────
    function updateProgress(event) {
      if (!event) {
        if (progressEl) { progressEl.remove(); progressEl = null; }
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
      const stepEl   = progressEl.querySelector(".progress-step");
      const actionEl = progressEl.querySelector(".progress-action");
      stepEl.textContent = event.totalSteps > 0
        ? "Step " + event.currentStep + " of " + event.totalSteps + " \\u2014 "
        : (PROGRESS_STATUS_LABELS[event.status] || event.status) + " \\u2014 ";
      actionEl.textContent = event.currentAction || "";

      // Update right panel
      if (event.status === "planning") {
        setRunActive("Planning…");
        setProgress(10);
        addActivity("\\uD83E\\uDDE0", "Planning…");
      } else if (event.status === "executing_step" && event.totalSteps > 0) {
        currentStep = event.currentStep;
        totalSteps  = event.totalSteps;
        const pct = 10 + (currentStep / totalSteps) * 80;
        setProgress(pct);
        if (event.currentAction) {
          addToolCall(event.currentAction, "", "running");
          addActivity("\\uD83D\\uDD27", "Running: " + event.currentAction);
        }
        setRunActive("Step " + currentStep + " of " + totalSteps);
      } else if (event.status === "step_completed") {
        toolDoneCount++;
        toolsTitle.textContent = "TOOLS (" + toolDoneCount + "/" + toolCallCount + ")";
      } else if (event.status === "task_completed") {
        setProgress(100);
        setRunIdle();
        addActivity("\\u2705", "Task completed");
      } else if (event.status === "replanning") {
        addActivity("\\uD83D\\uDD04", "Replanning…");
      }

      messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    // ── Diff rendering (main chat area) ──────────────────────────
    function renderDiffLines(diffText) {
      const view = document.createElement("div");
      view.className = "diff-view";
      diffText.split("\\n").forEach(function (line) {
        const lineEl = document.createElement("div");
        if (line.indexOf("+++") === 0 || line.indexOf("---") === 0) lineEl.className = "diff-line";
        else if (line.indexOf("@@") === 0)  lineEl.className = "diff-line hunk";
        else if (line.indexOf("+") === 0)   lineEl.className = "diff-line add";
        else if (line.indexOf("-") === 0)   lineEl.className = "diff-line remove";
        else                                lineEl.className = "diff-line";
        lineEl.textContent = line;
        view.appendChild(lineEl);
      });
      return view;
    }

    // ── Patch batch card ──────────────────────────────────────────
    function appendPatchBatch(files) {
      showChat();
      addActivity("\\uD83D\\uDCDD", "Changes ready: " + files.length + " file" + (files.length === 1 ? "" : "s"));

      const container = document.createElement("div");
      container.className = "patch-batch";
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
        // Mirror to right panel
        addFileChange(file.path, file.additions, file.removals, file.diff);

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
        if (decision === "approve") addActivity("\\u2705", "Changes approved and written to disk");
        else addActivity("\\u274C", "Changes rejected");
      }
      approveButton.addEventListener("click", function () { decide("approve"); });
      rejectButton.addEventListener("click", function () { decide("reject"); });
      actions.appendChild(approveButton);
      actions.appendChild(rejectButton);
      container.appendChild(actions);
      messagesEl.appendChild(container);
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    // ── Send helpers ──────────────────────────────────────────────
    function sendText(text) {
      if (!text || !text.trim()) return;
      vscode.postMessage({ type: "sendMessage", text: text });
    }

    function sendWelcome() {
      const text = inputEl.value;
      sendText(text);
      inputEl.value = "";
    }

    function sendChat() {
      const text = chatInputEl.value;
      sendText(text);
      chatInputEl.value = "";
    }

    // Welcome input
    sendButton.addEventListener("click", sendWelcome);
    inputEl.addEventListener("keydown", function (e) {
      if (e.key === "Enter") sendWelcome();
    });

    // Compact chat input
    chatSendBtn.addEventListener("click", sendChat);
    chatInputEl.addEventListener("keydown", function (e) {
      if (e.key === "Enter") sendChat();
    });

    // Example prompt chips
    document.querySelectorAll(".example-prompt").forEach(function (button) {
      button.addEventListener("click", function () {
        if (inputEl.disabled) return;
        sendText(button.getAttribute("data-prompt"));
      });
    });

    // New conversation button
    document.getElementById("newConvBtn").addEventListener("click", function () {
      vscode.postMessage({ type: "newConversation" });
    });

    // Nav items (informational for now)
    document.querySelectorAll(".nav-item").forEach(function (btn) {
      btn.addEventListener("click", function () {
        document.querySelectorAll(".nav-item").forEach(function (b) { b.classList.remove("active"); });
        btn.classList.add("active");
        vscode.postMessage({ type: "navigate", section: btn.getAttribute("data-nav") });
      });
    });

    // ── Message bus ───────────────────────────────────────────────
    window.addEventListener("message", function (event) {
      const data = event.data;
      if (!data) return;
      if      (data.type === "addMessage")      appendMessage(data.message);
      else if (data.type === "startStream")     startStream();
      else if (data.type === "appendChunk")     appendChunk(data.chunk);
      else if (data.type === "finalizeStream")  finalizeStream(data.html, data.timestamp);
      else if (data.type === "showPlan")        appendPlan(data.steps);
      else if (data.type === "showPatchBatch")  appendPatchBatch(data.files);
      else if (data.type === "loading")         setLoading(!!data.show);
      else if (data.type === "timeline")        updateTimeline(data.stage, data.labels);
      else if (data.type === "executionState")  updateExecutionState(data.state);
      else if (data.type === "progress")        updateProgress(data.event);
    });
  })();
</script>
</body>
</html>`;
}
