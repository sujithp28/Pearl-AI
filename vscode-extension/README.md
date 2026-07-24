# Pearl — VS Code Extension

This extension registers one command and connects to Pearl's existing MCP
server over stdio, showing connection status in the status bar.

**Not implemented yet (by design, later phases):**

- No chat interface.
- No webviews.

---

## Project layout

```
vscode-extension/
├── src/
│   ├── extension.ts             # Activation entry point; the only file
│   │                             using the live `vscode` API for MCP.
│   ├── commands/
│   │   └── openChat.ts          # "Pearl: Open Chat" command logic (pure).
│   ├── mcp/
│   │   ├── protocolClient.ts    # JSON-RPC request/response framing (pure).
│   │   ├── processTypes.ts      # Structural child-process types, for DI.
│   │   ├── connection.ts        # Spawns + supervises the MCP server process
│   │   │                         (pure — takes an injectable `spawnFn`).
│   │   └── statusBar.ts         # Status-bar text/tooltip logic (pure).
│   └── test/
│       ├── openChat.test.ts
│       ├── protocolClient.test.ts
│       ├── connection.test.ts
│       └── statusBar.test.ts
├── .vscode/
│   ├── launch.json              # F5 debug config (Extension Development Host).
│   └── tasks.json               # Background `tsc --watch` build task.
├── package.json
├── tsconfig.json
└── README.md
```

Every module under `src/mcp/` (and `commands/openChat.ts`) is written to
have **no runtime dependency on `vscode`** — process spawning is injected
via a `spawnFn` parameter, and the status bar's logic is separated from
the real `vscode.StatusBarItem` construction. Only `extension.ts` imports
`vscode` as a value and wires the pieces together. This is what makes the
whole thing testable with plain Node, with no VS Code test harness.

---

## Connecting to Pearl's MCP server

On activation, the extension spawns Pearl's **existing, unmodified** MCP
server (`src/mcp/server.py`, run as `<python> -m src.mcp`) as a child
process and speaks its JSON-RPC protocol over stdio — see
[`../README.md`](../README.md#-running-the-mcp-server) for what that
server exposes.

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

## Prerequisites

- Node.js >= 18
- npm
- To actually connect: Python 3.12 with Pearl's dependencies installed
  (see the repo root `README.md`) — the extension will show
  `Pearl: Connection Error` and keep retrying if the server can't start,
  it won't crash without it.

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

It should display an information message: **"Pearl is connected."** (This
message is currently static — it is not yet driven by the MCP
connection's actual status.)

## Test

```bash
npm test
```

Runs the unit tests (Node's built-in test runner) against the compiled
output in `out/`. The MCP connection tests use a fake child process (no
real `python` process is spawned), so they run without Pearl's Python
dependencies installed.
