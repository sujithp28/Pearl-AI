# Pearl — Human UI Acceptance Test Guide

**Version:** 1.2.0-beta.0  
**VSIX:** `vscode-extension/pearl-vscode-1.2.0-beta.0.vsix` (61 KB, built 2026-08-29)  
**Commit:** `ae4cf94 feat(ui): three-panel layout — sidebar, chat, right run-inspector`

---

> **Important — read before starting:**
>
> Automated tests do not constitute UI acceptance.  
> The VS Code webview must be manually exercised.  
> An HTML artifact is not sufficient evidence.  
>
> This document exists because the development environment (Linux/WSL2) cannot
> launch the Windows VS Code host. Every scenario in this guide must be performed
> by a human inside a real running VS Code window. Leave the "Observed" and
> "Result" columns blank until you perform each test yourself.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Installing the VSIX](#2-installing-the-vsix)
3. [Starting Pearl](#3-starting-pearl)
4. [Test Scenarios](#4-test-scenarios)
5. [Visual Acceptance Checklist](#5-visual-acceptance-checklist)
6. [Responsive Tests](#6-responsive-tests)
7. [Log Collection](#7-log-collection)
8. [Results Table](#8-results-table)

---

## 1. Prerequisites

| Requirement | Details |
|---|---|
| OS | Windows 10/11 or macOS |
| VS Code | 1.85 or later |
| Pearl VSIX | `vscode-extension/pearl-vscode-1.2.0-beta.0.vsix` |
| LLM provider | An active provider key in `.env` — e.g. `PEARL_LLM_PROVIDER=claude` + `ANTHROPIC_API_KEY=sk-…` |
| Python | 3.11+ with `pip install -r requirements.txt` |
| Workspace | The `pearl-agent` repository folder |

**Before you begin,** confirm the backend can start:

```bash
cd pearl-agent
python3 -m src.mcp
```

Press `Ctrl+C` after you see `Pearl MCP server ready`. If it errors, fix the backend before testing the UI.

---

## 2. Installing the VSIX

### Step 1 — Open VS Code

Open VS Code. Make sure no other Pearl extension version is installed.  
Check: **Extensions** sidebar → search "Pearl" → uninstall any existing version.

### Step 2 — Install from VSIX

**Method A — Command Palette (recommended):**

1. Press `Ctrl+Shift+P` (Windows) or `Cmd+Shift+P` (macOS)
2. Type: `Extensions: Install from VSIX…`
3. Select it, press Enter
4. Navigate to: `pearl-agent/vscode-extension/pearl-vscode-1.2.0-beta.0.vsix`
5. Click **Install**

**Method B — Extensions sidebar:**

1. Click the Extensions icon in the Activity Bar
2. Click the `…` menu at the top right of the Extensions panel
3. Select **Install from VSIX…**
4. Navigate to the `.vsix` file and click **Install**

### Step 3 — Reload Window

When prompted, click **Reload Window**.  
If not prompted: `Ctrl+Shift+P` → `Developer: Reload Window`

### Step 4 — Verify installation

In the Extensions sidebar, search "Pearl".  
Confirm: **Pearl AI Coding Assistant** version **1.2.0-beta.0** is listed and enabled.

---

## 3. Starting Pearl

### Step 1 — Open the workspace

`File` → `Open Folder…` → select the `pearl-agent` folder.

This is important: Pearl's file tools are scoped to the open workspace. Opening a different folder means file operations will target different files.

### Step 2 — Configure the LLM provider

Open the `.env` file at the root of the workspace. Set your provider:

```bash
# Example — Anthropic Claude
PEARL_LLM_PROVIDER=claude
ANTHROPIC_API_KEY=sk-ant-...

# Example — OpenAI
PEARL_LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...

# Example — OpenRouter
PEARL_LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=...
OPENROUTER_MODEL=anthropic/claude-sonnet-4-5
```

Save the file. Pearl reads `.env` on startup.

### Step 3 — Open Pearl

**Method A:** Click the Pearl gem icon in the Activity Bar (left sidebar).  
**Method B:** `Ctrl+Shift+P` → `Pearl: Open Chat`  
**Method C:** `Ctrl+K` shortcut (if configured)

The Pearl panel should open as a three-column layout:
- Left sidebar: navigation + recent sessions + user profile
- Center: welcome screen with greeting and suggestion chips
- Right panel: "Current Run" / Tools / Files Changed / Diff Preview / Activity

### Step 4 — Check connection status

Look at the **bottom-left of the left sidebar** for the green pulsing dot.  
- Green dot: Pearl is connected to the MCP backend  
- No dot / grey: backend is not running — open a terminal and run `python3 -m src.mcp` manually

### Step 5 — Check model indicator

The welcome screen subtitle should mention your configured model/provider.  
If it shows a connection error, verify your `.env` key and retry.

---

## 4. Test Scenarios

For each scenario:
1. Type the exact input shown
2. Wait for the full response
3. Record what you actually observed
4. Mark PASS or FAIL

---

### TEST 1 — Simple greeting (no tools)

**Input (type exactly):**
```
hello
```

**Expected behavior:**
- Pearl responds conversationally within a few seconds
- No "Proposed Plan" card appears
- No tool calls appear in the right panel
- No `read_file`, `search_text`, or any other tool executes
- Right panel remains idle (badge stays "Idle")
- Response is plain text, no file paths, no code blocks

**Checking the right panel:** Tools section should still show "Tool calls appear here". If a tool row appears, mark FAIL and record which tool ran.

**Record:**

| | |
|---|---|
| Observed | |
| Tools executed | none / list them |
| Plan card appeared | yes / no |
| Result | PASS / FAIL |
| Screenshot | attach |

---

### TEST 2 — Second greeting (no hallucinated tools)

**Input:**
```
hi Pearl
```

**Expected behavior:**
- Short, friendly conversational reply
- No tool execution of any kind
- No planning card
- Response arrives quickly (no loading delay implying tool execution)

**Record:**

| | |
|---|---|
| Observed | |
| Tools executed | none / list them |
| Plan card appeared | yes / no |
| Result | PASS / FAIL |
| Screenshot | attach |

---

### TEST 3 — Technical question, no tools needed

**Input:**
```
What is the difference between asyncio.gather and asyncio.wait in Python?
```

**Expected behavior:**
- Response streams progressively — text appears incrementally, not all at once
- Markdown renders correctly: bullet points, bold text, code spans
- Code examples appear in styled code blocks (monospace font, distinct background)
- No tool execution

**While streaming, check:**
- Can you see text appearing word by word or sentence by sentence?
- Does the scroll position follow the incoming text?
- Is there any duplicated text or garbled characters?

**Record:**

| | |
|---|---|
| Observed | |
| Streaming visible | yes / no |
| Markdown rendered | yes / no |
| Code blocks styled | yes / no |
| Tools executed | none / list them |
| Result | PASS / FAIL |
| Screenshot | attach |

---

### TEST 4 — Repository search (plan + tools + result)

**Input:**
```
Find where ParserRegistry is defined in this codebase.
```

**Expected behavior (in order):**

1. Pearl shows a loading indicator
2. A **"Proposed Plan"** card appears with numbered steps (e.g. `search_text`, `read_file`)
3. An **"Execute Plan"** button and **"Cancel"** button are visible
4. Click **Execute Plan**
5. The right panel "Current Run" badge changes to **Active**
6. Progress appears — tool rows populate in the "Tools" section as each tool runs
7. The progress bar advances during execution
8. After execution completes, a final answer identifies the file and line number
9. The Activity feed shows the sequence of events

**Critical checks:**
- Progress must be visible DURING execution, not only after completion
- At least 2 tool rows must appear in the right panel (search + read)
- The final answer must name a real file (e.g. `src/memory/repository/parser_registry.py`)

**Record:**

| | |
|---|---|
| Plan card appeared | yes / no |
| Steps shown in plan | number |
| Execute button visible | yes / no |
| Cancel button visible | yes / no |
| Progress during execution | yes / no |
| Tool rows in right panel | number |
| Final answer file | |
| Result | PASS / FAIL |
| Screenshot (plan card) | attach |
| Screenshot (after execution) | attach |

---

### TEST 5 — Multi-tool task

**Input:**
```
Read the file src/web/service.py and explain what WebIntelligenceService does, then tell me how many methods it has.
```

**Expected behavior:**
- Plan includes at least: `read_file`, possibly `find_method` or `search_text`
- Click Execute Plan
- Multiple tool rows appear in the right panel sequentially
- Each tool shows name + path/argument + status icon
- Progress bar advances between tools
- Final answer describes the service AND gives a method count

**Critical: Progress updates during execution, not only at the end.**

**Record:**

| | |
|---|---|
| Number of tool calls | |
| Tool names (in order) | |
| Progress bar moved between tools | yes / no |
| Right panel populated correctly | yes / no |
| Final answer accurate | yes / no |
| Result | PASS / FAIL |
| Screenshot | attach |

---

### TEST 6 — Approval workflow (Approve + Reject)

#### Part A — Reject

**Input:**
```
Create a new file called test_approval.txt with the content "hello world"
```

**Expected behavior:**
- Plan appears with a `write_file` step
- Click Execute Plan
- A **"Pending Changes"** approval card appears showing `test_approval.txt`
- The diff preview shows `+hello world`
- Click **Reject**
- The file `test_approval.txt` must NOT be created on disk

**Verify file was NOT created:**
- Open VS Code Explorer — `test_approval.txt` should not appear
- In terminal: `ls pearl-agent/test_approval.txt` → "No such file"

**Record:**

| | |
|---|---|
| Approval card appeared | yes / no |
| File name shown in card | |
| Diff preview visible | yes / no |
| After Reject: file exists | yes (FAIL) / no (PASS) |
| Result | PASS / FAIL |
| Screenshot | attach |

#### Part B — Approve

**Input:**
```
Create a new file called test_approval.txt with the content "hello world"
```

**Expected behavior:**
- Approval card appears again
- Click **Approve**
- File appears in VS Code Explorer
- `test_approval.txt` contains `hello world`

**Verify file WAS created:**
- VS Code Explorer shows `test_approval.txt`
- Click it — content is `hello world`

**Clean up:** Delete `test_approval.txt` after this test.

**Record:**

| | |
|---|---|
| After Approve: file exists | yes / no |
| File content correct | yes / no |
| Result | PASS / FAIL |
| Screenshot | attach |

---

### TEST 7 — Tool failure (nonexistent file)

**Input:**
```
Read the file src/nonexistent_file_xyz_12345.py and summarize it.
```

**Expected behavior:**
- Pearl attempts `read_file` with the nonexistent path
- An error appears — either in the chat bubble or as an error card
- The error is readable (e.g. "File not found" or similar)
- The UI remains usable — you can type in the input box
- Sending another message works normally

**After the error, send:**
```
hello
```

**Expected:** Normal conversational response — session is not corrupted.

**Record:**

| | |
|---|---|
| Error message visible | yes / no |
| Error message readable | yes / no |
| Input box usable after error | yes / no |
| Second message responded | yes / no |
| Session corrupted | yes (FAIL) / no |
| Result | PASS / FAIL |
| Screenshot | attach |

---

### TEST 8 — Long streaming response with code blocks

**Input:**
```
Write a complete Python implementation of a binary search tree with insert, search, delete, and in-order traversal. Include docstrings and type hints.
```

**Expected behavior:**
- Response streams progressively — long response arrives incrementally
- Code appears inside styled code blocks with monospace font
- The conversation scrolls automatically to follow the streaming text
- The input remains accessible at the bottom (not hidden behind content)
- After completion: no duplicated lines, no missing sections, no garbled characters

**Record:**

| | |
|---|---|
| Streaming visible | yes / no |
| Code blocks styled | yes / no |
| Auto-scroll followed response | yes / no |
| Input accessible during/after | yes / no |
| Content complete (no truncation) | yes / no |
| No duplicated text | yes / no |
| Result | PASS / FAIL |
| Screenshot | attach |

---

## 5. Visual Acceptance Checklist

Complete this after running the scenarios. Mark each item ✅ (pass) or ❌ (fail/missing). If ❌, describe what you saw.

### Header / Branding

- [ ] Pearl gem logo visible in sidebar
- [ ] "Pearl AI" branding displayed
- [ ] Logo has violet/purple gem gradient
- [ ] "AI" badge next to name
- [ ] Connection status dot visible (green = connected)
- [ ] Connection dot pulses/animates

### Sidebar — Navigation

- [ ] "+ New Conversation" button visible
- [ ] Navigation items visible: Chat, Projects, Repository, Timeline, Sessions, Settings
- [ ] Active nav item is highlighted (purple/violet)
- [ ] Nav items have icons
- [ ] Clicking a nav item changes the active highlight

### Sidebar — Recent Sessions

- [ ] "RECENT SESSIONS" section label visible
- [ ] At least one session item shows after first conversation
- [ ] Session items show title (first message or topic)
- [ ] Session items show timestamp
- [ ] "View all sessions →" link visible
- [ ] Active session is highlighted

### Sidebar — User Profile

- [ ] User avatar circle at bottom of sidebar
- [ ] User name displayed
- [ ] "Pro" (or plan) badge visible
- [ ] Connection indicator visible

### Welcome Screen

- [ ] Greeting message: "👋 Hi …, how can I help you today?"
- [ ] Subtitle text visible
- [ ] Input box centered with rounded border
- [ ] @ Mention, Attach, Code icon buttons below input
- [ ] Four suggestion chips: "Explain this code", "Find and fix a bug", "Add a new feature", "Write tests"
- [ ] Clicking a chip sends that message
- [ ] Welcome screen disappears once first message is sent

### Chat Area

- [ ] User messages appear right-aligned with user avatar
- [ ] Pearl messages appear left-aligned with gem avatar
- [ ] Gem avatar has purple gradient
- [ ] User avatar is a person icon
- [ ] Timestamps shown on each message (HH:MM format)
- [ ] Message bubbles have rounded corners
- [ ] User bubbles have distinct background (purple tint)
- [ ] Pearl bubbles have distinct background (surface color)
- [ ] Error messages have red/warning styling

### Compact Input Bar (appears after first message)

- [ ] Input bar appears below messages after first send
- [ ] Has @ / attachment / code icon buttons
- [ ] Text field accepts typing
- [ ] Enter key sends message
- [ ] Send button (↑) visible
- [ ] Input is disabled/greyed during execution
- [ ] Input re-enables after execution completes

### Plan Card

- [ ] "Proposed Plan" title visible
- [ ] Step count shown (e.g. "3 steps — approve to execute")
- [ ] Each step has a number badge
- [ ] Each step shows tool name
- [ ] Step arguments visible (or expandable via `<details>`)
- [ ] **"Execute Plan"** button present (exact text)
- [ ] **"Cancel"** button present (exact text)
- [ ] Buttons are disabled after a decision is made

### Approval Card (Pending Changes)

- [ ] "Pending Changes" title visible
- [ ] File name(s) shown in monospace
- [ ] `+N -M` line count badges visible
- [ ] Diff preview shows added lines in green
- [ ] Diff preview shows removed lines in red
- [ ] "Copy diff" button visible
- [ ] **"Approve"** button present
- [ ] **"Reject"** button present (styled in red/danger)
- [ ] Buttons disabled after decision
- [ ] Approval card is clearly distinct from plan card

### Right Panel — Current Run

- [ ] "Current Run" header visible
- [ ] Badge shows "Idle" when no run active
- [ ] Badge changes to "Active" (green) during execution
- [ ] Run name / description shown
- [ ] Progress bar visible
- [ ] Progress bar advances during multi-step execution
- [ ] Badge changes to "Done" (purple) after completion

### Right Panel — Tools

- [ ] "TOOLS" section label visible
- [ ] Tool rows animate in as each tool executes
- [ ] Each row shows: icon + tool name + path/argument + status icon
- [ ] Status changes from ⏳ to ✅ on completion
- [ ] Count updates: "TOOLS (2/3)"

### Right Panel — Files Changed

- [ ] "FILES CHANGED" section visible
- [ ] File rows appear when patch batch arrives
- [ ] Each row shows: icon + filename + `+N -N` stats
- [ ] Count updates: "FILES CHANGED (1)"

### Right Panel — Diff Preview

- [ ] "DIFF PREVIEW" section appears when there are file changes
- [ ] Filename shown at top of diff
- [ ] Added lines highlighted green
- [ ] Removed lines highlighted red
- [ ] Hunk headers (`@@ …`) styled distinctly

### Right Panel — Activity Feed

- [ ] "ACTIVITY" section visible
- [ ] Events appear as execution progresses
- [ ] Events have icon + description + timestamp
- [ ] Most recent event appears at top
- [ ] Events include: planning, tool execution, approval, completion

### Progress Indicators

- [ ] Spinning indicator visible while waiting for response
- [ ] Animated loading dots appear while Pearl is thinking
- [ ] Progress bar in right panel moves during execution
- [ ] Loading state is clearly visible (not subtle)

### Error States

- [ ] Error messages have red/warning styling
- [ ] Error text explains what went wrong
- [ ] Error does not break the layout
- [ ] UI remains usable after an error
- [ ] Input re-enables after error

### Markdown Rendering

- [ ] **Bold text** renders bold
- [ ] `inline code` renders in monospace with background
- [ ] Code blocks render in distinct block with monospace font
- [ ] Bullet lists render as actual lists
- [ ] No raw `**` or `` ` `` symbols visible in rendered output

### Scrolling

- [ ] Long conversation scrolls correctly
- [ ] Auto-scroll follows new messages during streaming
- [ ] Manual scroll up works (stops auto-scroll)
- [ ] Input bar always remains at the bottom and accessible
- [ ] Right panel scrolls independently of main chat

---

## 6. Responsive Tests

Perform each test and record whether layout breaks.

**How to resize:** Drag the VS Code window edges or drag the panel split handles.

### R1 — Normal desktop size (~1280×800 or larger)

Expected: All three panels visible, no overflow, no clipping.

| Item | Result | Notes |
|---|---|---|
| All three panels visible | PASS / FAIL | |
| No horizontal scroll on page body | PASS / FAIL | |
| Sidebar content readable | PASS / FAIL | |
| Right panel content readable | PASS / FAIL | |
| Input accessible | PASS / FAIL | |

### R2 — Narrow window (~800px wide)

Drag VS Code to be narrower. The sidebar and right panel may compress.

Expected: Core functionality (chat input, messages, response) remains accessible. Layout may adapt — panels may become thinner but must not overlap or clip critical elements.

| Item | Result | Notes |
|---|---|---|
| Input bar accessible | PASS / FAIL | |
| Messages readable | PASS / FAIL | |
| Buttons clickable | PASS / FAIL | |
| No element overlaps another | PASS / FAIL | |
| No clipped/missing text | PASS / FAIL | |

### R3 — Very long single message

Send:
```
Please write a very detailed explanation of the Python asyncio event loop, covering: how the event loop works internally, the role of coroutines and futures, how tasks are scheduled, the difference between asyncio.run and loop.run_until_complete, how cancellation works with asyncio.Task, and common pitfalls developers encounter. Be as thorough as possible.
```

| Item | Result | Notes |
|---|---|---|
| Long response scrolls correctly | PASS / FAIL | |
| No horizontal overflow | PASS / FAIL | |
| Code blocks don't break layout | PASS / FAIL | |
| Input remains at bottom | PASS / FAIL | |

### R4 — Many tool calls

After running TEST 5 (multi-tool task), inspect the right panel with multiple tool rows.

| Item | Result | Notes |
|---|---|---|
| All tool rows visible | PASS / FAIL | |
| Right panel scrollable | PASS / FAIL | |
| No rows clipped | PASS / FAIL | |
| Tool names readable | PASS / FAIL | |

### R5 — Long execution plan

Send a task that will produce a 5+ step plan:
```
Read src/agent/executor.py, find all the public methods, then search for any tests that reference those methods, and summarize what test coverage exists.
```

| Item | Result | Notes |
|---|---|---|
| All plan steps visible | PASS / FAIL | |
| Plan card scrollable if long | PASS / FAIL | |
| Execute and Cancel buttons visible | PASS / FAIL | |
| Step numbers readable | PASS / FAIL | |

---

## 7. Log Collection

When something goes wrong, collect these logs before closing VS Code.

### VS Code Developer Tools (JavaScript console)

1. `Help` menu → `Toggle Developer Tools`  
   Or: `Ctrl+Shift+I` (Windows/Linux), `Cmd+Option+I` (macOS)
2. Click the **Console** tab
3. Look for red error messages
4. Right-click → **Save as…** to export the log

**Include in bug report:** Any `ERROR` or `Uncaught` messages, plus the 5 lines before each error.

### Extension Host Log

1. `View` → `Output` (or `Ctrl+Shift+U`)
2. Click the dropdown at the top right of the Output panel
3. Select **Extension Host**
4. Look for lines starting with `[Pearl]` or `[pearl-vscode]`
5. Select all text (`Ctrl+A`) → copy and paste into your bug report

### Pearl MCP Server Log

If Pearl's Python backend was started manually in a terminal, copy the terminal output.

If Pearl starts it automatically: check `View` → `Output` → select **Pearl MCP** from the dropdown.

**Look for:**
- `ERROR` lines
- Stack traces (lines starting with `File "`, `Traceback`)
- Tool execution failures

### What to Include in a Bug Report

For every bug, provide:

1. **Steps to reproduce** — exact inputs, exact sequence
2. **Expected** — what should have happened
3. **Observed** — what actually happened
4. **Screenshot** — the UI at the moment of failure
5. **Console log** — from VS Code Developer Tools
6. **Extension Host log** — from VS Code Output panel
7. **MCP server log** — terminal output from the Python backend
8. **Git commit** — `git log --oneline -1`
9. **VS Code version** — `Help` → `About`
10. **OS** — Windows version or macOS version

---

## 8. Results Table

Fill in this table as you complete each scenario. Leave cells blank until you have tested — do not pre-fill.

| Test | Input | Expected | Observed | Tools Executed | UI Result | Screenshot |
|---|---|---|---|---|---|---|
| T1 — Greeting | `hello` | Conversational reply, no tools | | | PASS / FAIL | |
| T2 — Hi | `hi Pearl` | Conversational reply, no tools | | | PASS / FAIL | |
| T3 — Technical | asyncio question | Streaming Markdown, no tools | | | PASS / FAIL | |
| T4 — ParserRegistry | `Find where ParserRegistry is defined` | Plan → execute → correct file + line | | | PASS / FAIL | |
| T5 — Multi-tool | Read + explain + count methods | Multiple tool rows, correct answer | | | PASS / FAIL | |
| T6a — Reject | Create test_approval.txt | Approval card → Reject → file absent | | | PASS / FAIL | |
| T6b — Approve | Create test_approval.txt | Approval card → Approve → file present | | | PASS / FAIL | |
| T7 — Failure | Read nonexistent file | Error card, UI usable, session intact | | | PASS / FAIL | |
| T8 — Long response | BST implementation | Streaming, code blocks, scroll | | | PASS / FAIL | |

---

## Final Acceptance Decision

Complete this block after all tests are done.

| Area | Result |
|---|---|
| Backend (Python MCP) | |
| M1 Streaming (`pearl/progress`) | |
| M2 Chat (`pearl/chat`, `pearl/chatChunk`) | |
| M3 Repository Intelligence | |
| M4 Multi-file Refactoring | |
| M5 Git Intelligence | |
| M6 Model Routing | |
| M7 Web Intelligence | |
| Actual VS Code UI | |

**Summary:**

| Metric | Count |
|---|---|
| Scenarios run | / 9 |
| Scenarios passed | |
| Scenarios failed | |
| Visual checks passed | / ~60 |
| Visual checks failed | |
| Responsive tests passed | / 5 |
| Responsive tests failed | |
| Bugs discovered | |
| Bugs fixed | |
| Bugs remaining | |

**Verdict:** PASS / PASS WITH KNOWN ISSUES / FAIL / NOT VERIFIED

**Signed off by:**  
**Date:**  
**VS Code version:**  
**OS:**  
**Git commit:** `ae4cf94`
