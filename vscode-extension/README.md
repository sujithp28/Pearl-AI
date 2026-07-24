# Pearl — VS Code Extension

This extension registers one command, connects to Pearl's existing MCP
server over stdio (showing connection status in the status bar), opens a
simple chat webview that talks to Pearl through that same connection —
showing the full proposed plan for review before gating each tool call
behind an approval dialog — and provides a read-only Memory view (in its
own "Pearl" activity bar container) showing conversation history, task
history, execution history, and project facts, refreshable on demand.

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
│   │   ├── approval.ts          # Per-tool approval types (`ToolApprover`, pure).
│   │   ├── planFormatting.ts    # Formats plan steps for display: step
│   │   │                         number, tool, args text, collapse-if-long
│   │   │                         decision (pure, independently testable).
│   │   ├── planApproval.ts      # Shows the plan in the webview and
│   │   │                         resolves once "Execute Plan"/"Cancel" is
│   │   │                         chosen there (pure — driven by an
│   │   │                         injected `post` + fed-back `resolveDecision`).
│   │   ├── chatController.ts    # Chat message flow: plan -> show full plan
│   │   │                         for Execute/Cancel -> (if executed) approve
│   │   │                         each tool step -> execute or reject -> post
│   │   │                         messages (pure, no `vscode`/webview).
│   │   ├── chatHtml.ts          # Static webview HTML: message list, input,
│   │   │                         send button, plan preview with collapsible
│   │   │                         arguments and Execute Plan/Cancel buttons
│   │   │                         (pure string builder).
│   │   ├── chatPanel.ts         # Owns the real `vscode.WebviewPanel` and
│   │   │                         wires it to `ChatController`/`WebviewPlanApprover`.
│   │   └── vscodeToolApprover.ts # The real per-tool approval dialog
│   │                              (`vscode.window.showWarningMessage`).
│   ├── memory/
│   │   ├── memoryClient.ts       # Reads Memory via `pearl/memory` (pure).
│   │   ├── memoryTree.ts         # Formats a snapshot into 4 categories x
│   │   │                          leaf nodes (pure, independently testable).
│   │   ├── memoryTreeState.ts    # Fetch/refresh orchestration + onChange
│   │   │                          callback (pure — driven by `RequestSender`).
│   │   └── memoryTreeProvider.ts # Owns the real `vscode.TreeDataProvider`
│   │                              and adapts `MemoryTreeState` to it.
│   └── test/
│       ├── protocolClient.test.ts
│       ├── connection.test.ts
│       ├── statusBar.test.ts
│       ├── chatClient.test.ts
│       ├── planClient.test.ts
│       ├── toolCallClient.test.ts
│       ├── planFormatting.test.ts
│       ├── planApproval.test.ts
│       ├── memoryClient.test.ts
│       ├── memoryTree.test.ts
│       ├── memoryTreeState.test.ts
│       ├── chatController.test.ts
│       ├── chatHtml.test.ts
│       ├── chatApproval.integration.test.ts
│       ├── planVisualization.integration.test.ts
│       └── memoryPanel.integration.test.ts
├── .vscode/
│   ├── launch.json              # F5 debug config (Extension Development Host).
│   └── tasks.json               # Background `tsc --watch` build task.
├── package.json
├── tsconfig.json
└── README.md
```

Every module under `src/mcp/`, `src/chat/`, and `src/memory/` *except*
`chatPanel.ts`, `vscodeToolApprover.ts`, and `memoryTreeProvider.ts` has
**no runtime dependency on `vscode`** — process spawning is injected via a
`spawnFn` parameter, the chat controller depends only on a structural
`sendRequest` shape (not the concrete `MCPConnection` class) plus an
injectable `ToolApprover` function (not a real dialog), the memory tree
state depends only on the same structural `sendRequest` shape, and the
status bar's logic is separated from the real `vscode.StatusBarItem`
construction. Only `extension.ts`, `chatPanel.ts`, `vscodeToolApprover.ts`,
and `memoryTreeProvider.ts` import `vscode` as a value; everything else is
testable with plain Node, no VS Code test harness required.

---

## Connecting to Pearl's MCP server

On activation, the extension spawns Pearl's MCP server (`src/mcp/server.py`,
run as `<python> -m src.mcp`) as a child process and speaks its JSON-RPC
protocol over stdio — see
[`../README.md`](../README.md#-running-the-mcp-server) for what that
server exposes. (Prior steps added `pearl/planOnly`, `pearl/chat`; this
step added `pearl/memory` — see "Memory view" below — everything else
about the server, including how tools actually execute, is unchanged.)

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
2. Asks the *existing* `Planner` what it would do, via the `pearl/planOnly`
   JSON-RPC method added in Step 4 (reuses `Planner.plan()` — previously
   only used internally by `Planner.run()` — without dispatching or
   recording anything; see `src/mcp/server.py`, unchanged in this step).
3. **If no tool is needed**, falls back to the plain conversational
   `pearl/chat` method from Step 3, unchanged — no plan preview and no
   approval of any kind, since nothing would execute.
4. **If one or more tools are proposed**, before any tool approval is
   requested, the **complete plan** is shown in the chat as its own block
   (`planFormatting.ts` + `planApproval.ts`, rendered by `appendPlan` in
   the webview): each step numbered, with its tool name, and its
   arguments — pretty-printed inline, or collapsed behind a plain
   `<details>`/"Arguments" toggle when the pretty-printed JSON exceeds 100
   characters. Below the steps are two buttons: **Execute Plan** and
   **Cancel**.
   - **Cancel** → posts `Plan cancelled. No changes were made.` to the
     chat. No tool approval is requested and `tools/call` is never sent
     for any step in the plan.
   - **Execute Plan** → continues into the existing per-tool approval loop
     from Step 4, **unchanged**: for each step in order —
     - Shows a modal **approval dialog** (`vscodeToolApprover.ts`) with the
       tool name and its arguments, and **Approve** / **Reject** buttons.
     - **Approve** → executes it through the *existing* `tools/call`
       method (the *existing* `ToolDispatcher`, reused as-is), and posts
       the result to the chat.
     - **Reject** → sends nothing to the server, cancels the rest of the
       plan, and posts `Tool "<name>" was rejected. No changes were made.`
       to the chat.
     - A tool that fails once approved (`isError: true`, or a
       transport-level failure) also stops any remaining steps and
       reports the failure to the chat.

The plan preview is not a native modal — it renders in the chat webview
itself via a `showPlan` message, and the webview posts a `planDecision`
message back once the user clicks a button. `WebviewPlanApprover`
correlates that one round trip per plan (see `planApproval.ts`).

---

## Memory view

A **"Pearl"** container in the Activity Bar holds a **Memory** tree view
with four top-level, always-present categories, each labeled with its
current count and expandable to its entries:

- **Conversation** — every recorded turn, as `role: content` (truncated in
  the label past 80 characters; the full text is in the tooltip).
- **Tasks** — each task's description as the label, its status
  (`pending`/`in_progress`/`completed`/`failed`) as the description text.
- **Execution History** — each tool call's name as the label, `ok`/`failed`
  as the description, and the result or error as the tooltip.
- **Project Facts** — each key/value pair remembered in `Memory.project`.

This reads `Memory` through the *existing* MCP server via a new
`pearl/memory` method (`src/mcp/server.py`), which returns
`Memory.to_dict()` **exactly as-is** — the `Memory` class itself
(`src/memory/memory.py`) was not touched to build this view. The view is
read-only: nothing in this feature ever calls a Memory-mutating method.

The view refreshes automatically once the MCP connection is established,
and again any time you click the refresh icon (**$(refresh)**) in the
view's title bar, or run **"Pearl: Refresh Memory"** from the Command
Palette. A failed refresh (e.g. connection down) replaces the tree with a
single `Failed to load memory: <reason>` node instead of leaving stale
data or crashing the view.

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

This opens the chat panel described above. The **Memory** view lives in
the **Pearl** icon in the Activity Bar (usually the left-hand icon strip)
of that same Extension Development Host window.

## Test

```bash
npm test
```

Runs the unit tests (Node's built-in test runner) against the compiled
output in `out/`. The MCP connection and chat tests use a fake child
process / fake MCP sender (no real `python` process is spawned), so they
run without Pearl's Python dependencies installed.

`chatApproval.integration.test.ts`, `planVisualization.integration.test.ts`,
and `memoryPanel.integration.test.ts` are a step above the per-module unit
tests: they drive the real `MCPConnection` (talking JSON-RPC over a fake
child process, exactly as it would over a real one) together with the real
`ChatController`/`planClient`/`toolCallClient`/`WebviewPlanApprover` or
`MemoryTreeState`, verifying their respective pipelines end-to-end —
everything except the actual `vscode.Webview`/`vscode.TreeView` and a real
Python process. `memoryPanel.integration.test.ts` specifically covers a
successful `pearl/memory` fetch building the expected tree, a protocol
error surfacing as a single error node without crashing, and repeated
refreshes correctly replacing the previous tree.
