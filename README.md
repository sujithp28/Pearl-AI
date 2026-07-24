# 🦪 Pearl

> **A Production-Quality AI Coding Agent Built from Scratch**

![Python](https://img.shields.io/badge/Python-3.12-blue)
![Status](https://img.shields.io/badge/Status-Active%20Development-success)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

# 📖 Overview

Pearl is an open-source AI Coding Agent built from scratch in Python.

The goal is to build a modular, extensible, and self-hostable AI software engineering assistant capable of understanding projects, executing tools, maintaining memory, planning complex tasks, and integrating with modern developer workflows.

Unlike traditional AI chatbots, Pearl is being designed around a production-quality architecture where every component has a single responsibility and can evolve independently.

---

# 🎯 Vision

Pearl aims to become an AI engineering platform capable of:

- Understanding complete codebases
- Reading and modifying files
- Executing development tools
- Planning multi-step engineering tasks
- Maintaining project memory
- Verifying its own work
- Integrating with IDEs
- Communicating through MCP (Model Context Protocol)

Example:

> "Create a FastAPI project, Dockerize it, deploy it to AWS, run tests, and commit the changes."

---

# 🏗 Current Architecture

```
                 User
                   │
                   ▼
            Language Model
                   │
                   ▼
           Tool Dispatcher
                   │
                   ▼
            Tool Registry
                   │
                   ▼
            Registered Tools
                   │
                   ▼
          File / Shell Tools
```

---

# 🛠 Technology Stack

- Python 3.12
- Hugging Face Transformers
- PyTorch
- CUDA
- VS Code
- Ubuntu (WSL)

Planned Support

- OpenAI
- Anthropic
- Ollama
- LM Studio
- OpenRouter
- MCP

---

# 📂 Project Structure

```
pearl/

├── docs/
├── models/
├── tests/
│
├── src/
│
├── agent/
│   ├── __init__.py
│   └── dispatcher.py
│
├── config/
│
├── llm/
│
├── memory/
│
├── mcp/
│
├── prompts/
│
├── tools/
│   ├── file_tools.py
│   ├── shell_tools.py
│   ├── metadata.py
│   ├── models.py
│   └── registry.py
│
├── main.py
├── README.md
├── requirements.txt
└── .gitignore
```

---

# ✅ Completed Features

## Core Foundation

- Project Structure
- Configuration System
- Settings Management
- CUDA Detection
- Hugging Face Model Loading
- Text Generation
- Modular Package Layout

Status

✅ Completed

---

## File Tools

Implemented

- Read File
- Write File
- Append File
- List Directory
- File Exists
- Create Directory

Status

✅ Tested

---

## Shell Tools

Implemented

- Execute Shell Commands
- Execute Python Code
- Current Working Directory
- List Directory
- Locate Executables
- Check Command Availability

Status

✅ Tested

---

## Tool Metadata

Implemented

- `@tool` decorator
- Automatic metadata attachment
- Tool descriptions
- Standardized registration

Example

```python
@tool("Read the contents of a UTF-8 text file.")
def read_file(path: str) -> str:
    ...
```

Status

✅ Completed

---

## Tool Registry

Implemented

- Register tools
- Store metadata
- Retrieve tools
- List available tools
- Lookup by name

Status

✅ Completed

---

## Tool Dispatcher

Implemented

- Execute tools dynamically
- Check tool availability
- List registered tools
- Retrieve tool metadata
- Error handling for unknown tools

Status

✅ Completed

---

# 📈 Current Progress

```
████████████████████████████░░░░

✅ Project Foundation
✅ Configuration
✅ LLM Loading
✅ Text Generation
✅ File Tools
✅ Shell Tools
✅ Tool Metadata
✅ Tool Registry
✅ Tool Dispatcher

⬜ LLM Tool Calling
⬜ Conversation Memory
⬜ Project Memory
⬜ Planner
✅ MCP Server
⬜ IDE Integrations
⬜ Autonomous Execution
```

---

# 🚧 Current Sprint

## Sprint 5

Next Objective

Implement LLM-driven Tool Calling.

Future execution flow:

```
User Prompt

↓

Language Model

↓

Select Tool

↓

Dispatcher

↓

Registry

↓

Execute Tool

↓

Return Result
```

This is the milestone where Pearl transitions from a collection of utilities into an AI coding agent capable of selecting and executing tools.

---

# 🗺 Roadmap

## Phase 1 — Foundation

- [x] Project Structure
- [x] Configuration
- [x] LLM Integration
- [x] Text Generation
- [x] File Tools
- [x] Shell Tools
- [x] Tool Metadata
- [x] Tool Registry
- [x] Tool Dispatcher
- [ ] LLM Tool Calling

---

## Phase 2 — Memory

- Conversation Memory
- Project Memory
- Context Window
- Summarization

---

## Phase 3 — Planning

- Task Planning
- Multi-Step Execution
- Error Recovery
- Workflow Engine

---

## Phase 4 — MCP

- [x] Custom MCP Server
- [x] JSON-RPC
- [x] Tool Exposure
- [ ] Resources
- [ ] Prompts

---

# 🔌 Running the MCP Server

Pearl includes a self-contained MCP (Model Context Protocol) server that
exposes the same tool registry used by the CLI — no tools are redefined,
they're reused directly from `src.main.build_registry()`.

## Start it

```bash
python -m src.mcp
```

This builds Pearl's tool registry (file, shell, edit, and repository
intelligence tools — everything registered in `main.py`), wraps it in a
`ToolDispatcher`, `Planner`, and `Memory`, and serves them over
newline-delimited JSON-RPC 2.0 on stdio. Logs go to stderr, so stdout stays
clean for the protocol stream — safe to pipe directly into an MCP client
that spawns Pearl as a subprocess.

## Supported methods

| Method | Description |
|---|---|
| `initialize` | Handshake — returns protocol version, server info, and capabilities. |
| `tools/list` | Lists every tool in the registry as an MCP tool descriptor (name, description, JSON Schema `inputSchema`). |
| `tools/call` | Executes a tool by name via the existing `ToolDispatcher`. Tool failures are returned as a result with `isError: true` (per the MCP spec), not a JSON-RPC error. |
| `pearl/plan` | Pearl extension: runs a natural-language request through the existing `Planner` and executes the resulting steps sequentially. |
| `pearl/planOnly` | Pearl extension: returns the steps `Planner.plan()` would take for a request, without executing or recording anything — lets a client gate each step behind its own approval step before calling `tools/call`. |
| `pearl/chat` | Pearl extension: sends a message straight to the LLM via `LLMClient.generate()` (the same pathway `PearlAgent.chat()` uses) and returns its reply. |
| `pearl/memory` | Pearl extension: returns the server's current `Memory` contents (conversation, tasks, project facts, execution history) via the existing `Memory.to_dict()`, unchanged. Read-only. |
| `shutdown` | Returns a null result; does not stop the server. |
| `exit` (notification) | Stops the read loop and ends the process. |

Every `tools/call`, `pearl/plan`, and `pearl/chat` invocation is recorded
in the server's `Memory` (execution history, tasks for planned runs, and
conversation turns for chat).

## Try it manually

```bash
printf '%s\n%s\n%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","method":"exit"}' \
  | python -m src.mcp
```

## Embedding the server in other code

```python
from src.agent.dispatcher import ToolDispatcher
from src.agent.planner import Planner
from src.main import build_registry
from src.mcp.server import MCPServer
from src.memory import Memory

registry = build_registry()
dispatcher = ToolDispatcher(registry)
server = MCPServer(
    registry,
    dispatcher=dispatcher,
    planner=Planner(registry, dispatcher),
    memory=Memory(),
)
server.run_stdio()  # or call server.handle_request(...) directly
```

---

## Phase 5 — Integrations

- VS Code
- Cursor
- Claude Desktop
- GitHub
- GitHub Actions

---

## Phase 6 — Developer Tools

- Git
- Docker
- Kubernetes
- Terraform
- AWS CLI

---

## Phase 7 — Advanced Intelligence

- Project DNA
- Codebase Indexing
- Vector Memory
- Autonomous Planning
- Multi-Agent Collaboration

---

# 🧪 Development Workflow

Every feature follows the same engineering process.

```
Design
   ↓
Implement
   ↓
Test
   ↓
Verify
   ↓
Commit
   ↓
Push
```

No feature is considered complete until it has been tested successfully.

---

# 🎯 Long-Term Goal

Pearl is being built as a production-quality AI software engineering platform capable of understanding software projects, planning engineering tasks, executing developer tools, maintaining long-term project memory, verifying results, and integrating with modern development environments.

The focus is not only on generating code, but on building an AI system that understands projects before making changes.

---

# 📌 Project Status

Current Version

```
v0.1.0-dev
```

Development Status

```
🚧 Active Development
```

---

# 🤝 Contributing

Pearl is currently under active development.

Future documentation will include:

- Architecture Guide
- Coding Standards
- Roadmap
- MCP Design
- Memory Design
- Planner Design

---

# 📄 License

MIT License (to be added before the first public release)

---

# 🦪 Motto

> **Understand. Plan. Execute. Verify.**