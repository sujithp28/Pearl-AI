# Pearl — VS Code Extension

This extension registers one command, connects to Pearl's existing MCP
server over stdio (showing connection status in the status bar), and opens
a simple chat webview that talks to Pearl through that same connection —
gating every tool call behind an approval dialog before it runs.

**Not implemented yet (by design, later phases):**

- No markdown rendering — messages render as plain text.
- No streaming — replies appear once complete.

---

## Project layout

```
vscode-extension/
├── src/
│   ├── extension.ts             # Activation entry point; the only file
│   │                             using the live `vscode` API for MCP/status bar.
│   ├── mcp/
│   │   ├── protocolClient.ts    # JSON-RPC request/response framing (pure).
│   │   ├── processTypes.ts      # Structural child-process types, for DI.
│   │   ├── connection.ts        # Spawns + supervises the MCP server process
│   │   │                         (pure — takes an injectable `spawnFn`).
│   │   ├── statusBar.ts         # Status-bar text/tooltip logic (pure).
│   │   └── chatClient.ts        # Sends a chat message through an MCP
│   │                             connection (pure — depends only on a
│   │                             structural `sendRequest`).
│   │   ├── planClient.ts        # Asks the planner what it would do, via
│   │   │                         `pearl/planOnly`, without executing (pure).
│   │   └── toolCallClient.ts    # Executes one approved tool via
│   │                             `tools/call` (pure).
│   ├── chat/
│   │   ├── approval.ts          # Tool-approval types (`ToolApprover`, pure).
│   │   ├── chatController.ts    # Chat message flow: plan -> approve each
│   │   │                         tool step -> execute or reject -> post
│   │   │                         messages (pure, no `vscode`/webview).
│   │   ├── chatHtml.ts          # Static webview HTML: message list, input,
│   │   │                         send button (pure string builder).
│   │   ├── chatPanel.ts         # Owns the real `vscode.WebviewPanel` and
│   │   │                         wires it to `ChatController`.
│   │   └── vscodeToolApprover.ts # The real approval dialog
│   │                              (`vscode.window.showWarningMessage`).
│   └── test/
│       ├── protocolClient.test.ts
│       ├── connection.test.ts
│       ├── statusBar.test.ts
│       ├── chatClient.test.ts
│       ├── planClient.test.ts
│       ├── toolCallClient.test.ts
│       ├── chatController.test.ts
│       ├── chatHtml.test.ts
│       └── chatApproval.integration.test.ts
├── .vscode/
│   ├── launch.json              # F5 debug config (Extension Development Host).
│   └── tasks.json               # Background `tsc --watch` build task.
├── package.json
├── tsconfig.json
└── README.md
```

Every module under `src/mcp/` and `src/chat/` *except* `chatPanel.ts` and
`vscodeToolApprover.ts` has **no runtime dependency on `vscode`** — process
spawning is injected via a `spawnFn` parameter, the chat controller depends
only on a structural `sendRequest` shape (not the concrete `MCPConnection`
class) plus an injectable `ToolApprover` function (not a real dialog), and
the status bar's logic is separated from the real `vscode.StatusBarItem`
construction. Only `extension.ts`, `chatPanel.ts`, and
`vscodeToolApprover.ts` import `vscode` as a value; everything else is
testable with plain Node, no VS Code test harness required.

---

## Connecting to Pearl's MCP server

On activation, the extension spawns Pearl's MCP server (`src/mcp/server.py`,
run as `<python> -m src.mcp`) as a child process and speaks its JSON-RPC
protocol over stdio — see
[`../README.md`](../README.md#-running-the-mcp-server) for what that
server exposes. (This step added one new server-side method,
`pearl/planOnly` — see "Chat webview" below — everything else about the
server, including how tools actually execute, is unchanged.)

- **Startup detection**: the extension sends `initialize` right after
  spawning and waits for a response (5s default timeout). A process that
  fails to spawn (bad interpreter path), or spawns but never responds
  (crashes on import, wrong working directory, etc.), is treated as a
  startup failure — not a crash of the extension.
- **Status bar**: shows `Pearl: Connecting...`, `Pearl: Connected`,
  `Pearl: Connection Error`, or `Pearl: Disconnected`, with the failure
  detail (if any) as the tooltip.
- **Automatic reconnect**: if the server process exits unexpectedly (not
  via the extension's own `stop()`), the extension waits 2s and retries.
  This repeats until it either connects or the extension is deactivated.
- **Clean shutdown**: on deactivation, the extension sends Pearl's `exit`
  notification (so the server's own read loop ends gracefully) and then
  terminates the process.

### Configuration

| Setting | Default | Description |
|---|---|---|
| `pearl.pythonPath` | `python3` | Interpreter used to run `-m src.mcp`. Point this at `<repo>/.venv/bin/python` (or wherever Pearl's dependencies are installed) if your system `python3` doesn't have them. |

The server is spawned with `cwd` set to the first VS Code workspace
folder, since `python -m src.mcp` must run from the Pearl repo root.

---

## Chat webview

Running the **"Pearl: Open Chat"** command opens a webview panel with a
message list, a text input, and a send button. Re-running the command
reveals the existing panel instead of opening a second one. Messages
render as plain text with no markdown formatting and no incremental/
streaming updates — both deferred to later phases.

Typing a message and pressing **Enter** (or clicking **Send**) runs this
flow (`ChatController.handleUserMessage`):

1. Renders the message immediately under the `user` role.
2. Asks the *existing* `Planner` what it would do, via a new
   `pearl/planOnly` JSON-RPC method (reuses `Planner.plan()` — previously
   only used internally by `Planner.run()` — without dispatching or
   recording anything; see `src/mcp/server.py`).
3. **If no tool is needed**, falls back to the plain conversational
   `pearl/chat` method from Step 3, unchanged.
4. **If one or more tools are proposed**, for each one in order:
   - Shows a modal **approval dialog** (`vscodeToolApprover.ts`) with the
     tool name and its arguments (pretty-printed JSON), and **Approve** /
     **Reject** buttons.
   - **Approve** → executes it through the *existing* `tools/call` method
     (the *existing* `ToolDispatcher`, reused as-is), and posts the result
     to the chat.
   - **Reject** → sends nothing to the server (`tools/call` is never
     called for a rejected step), cancels the rest of the plan, and posts
     `Tool "<name>" was rejected. No changes were made.` to the chat.
   - A tool that fails once approved (`isError: true`, or a transport-level
     failure) also stops any remaining steps and reports the failure to
     the chat — the webview never crashes or hangs silently on failure.

No approval is requested for the no-tool / plain-chat path, since nothing
executes there.

---

## Prerequisites

- Node.js >= 18
- npm
- To actually connect and chat: Python 3.12 with Pearl's dependencies
  installed (see the repo root `README.md`), and a reachable LLM
  provider (see `Settings.LLM_PROVIDER` in `src/config/settings.py`) —
  the extension will show `Pearl: Connection Error` / an in-chat error
  message and keep retrying rather than crash if either isn't available.

## Install

```bash
cd vscode-extension
npm install
```

## Build

```bash
npm run compile
```

Or watch for changes while developing:

```bash
npm run watch
```

## Run the extension

Open the `vscode-extension/` folder in VS Code and press **F5**. This
launches an Extension Development Host window with Pearl loaded. If the
opened workspace is the Pearl repo (or you set `pearl.pythonPath` to a
working interpreter), the status bar should read **"Pearl: Connected"**
within a few seconds.

In that window, open the Command Palette (`Ctrl+Shift+P` /
`Cmd+Shift+P`) and run:

```
Pearl: Open Chat
```

This opens the chat panel described above.

## Test

```bash
npm test
```

Runs the unit tests (Node's built-in test runner) against the compiled
output in `out/`. The MCP connection and chat tests use a fake child
process / fake MCP sender (no real `python` process is spawned), so they
run without Pearl's Python dependencies installed.

`chatApproval.integration.test.ts` is a step above the per-module unit
tests: it drives the real `MCPConnection` (talking JSON-RPC over a fake
child process, exactly as it would over a real one) with the real
`ChatController`/`planClient`/`toolCallClient`, verifying the full
plan → approve/reject → `tools/call` (or `pearl/chat` fallback) pipeline
end-to-end — everything except the actual `vscode.Webview` and a real
Python process.
