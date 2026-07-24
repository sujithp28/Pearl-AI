# Pearl — VS Code Extension (Scaffold)

This is the initial scaffold for Pearl's VS Code extension. It registers
one command and displays a static message — nothing more.

**Not implemented yet (by design, later phases):**

- No connection to Pearl's MCP server.
- No chat interface.
- No webviews.

---

## Project layout

```
vscode-extension/
├── src/
│   ├── extension.ts          # Activation entry point; registers commands.
│   ├── commands/
│   │   └── openChat.ts       # "Pearl: Open Chat" command logic (pure, no VS Code API).
│   └── test/
│       └── openChat.test.ts  # Unit test for the command logic.
├── .vscode/
│   ├── launch.json           # F5 debug config (Extension Development Host).
│   └── tasks.json            # Background `tsc --watch` build task.
├── package.json
├── tsconfig.json
└── README.md
```

`commands/openChat.ts` takes its message-display function as a parameter
instead of importing `vscode` directly, so its logic can be unit tested
with plain Node — no VS Code test harness required. `extension.ts` is the
only file that talks to the real `vscode` API.

---

## Prerequisites

- Node.js >= 18
- npm

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
launches an Extension Development Host window with Pearl loaded.

In that window, open the Command Palette (`Ctrl+Shift+P` /
`Cmd+Shift+P`) and run:

```
Pearl: Open Chat
```

It should display an information message: **"Pearl is connected."**

## Test

```bash
npm test
```

Runs the unit tests (Node's built-in test runner) against the compiled
output in `out/`.
