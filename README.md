# 🦪 Pearl

> **Production-Quality AI Coding Agent built from scratch using Python**

Pearl is an open-source AI Coding Agent designed to evolve into a fully autonomous developer assistant similar to **Codex**, **Claude Code**, and **Cursor**, while remaining modular, extensible, and self-hostable.

This is **not** a chatbot project.

The goal is to build a real coding agent capable of:

- Understanding codebases
- Reading and modifying files
- Executing tools
- Planning tasks
- Maintaining memory
- Communicating through MCP (Model Context Protocol)
- Integrating with IDEs like VS Code and Cursor

---

# 🚀 Vision

Pearl should eventually be able to accept prompts such as:

> "Find all TODOs in this repository and fix them."

or

> "Create a FastAPI application with Docker support and deploy it using Terraform."

without manual intervention.

---

# 🛠 Technology Stack

- Python 3.12
- Hugging Face Transformers
- PyTorch
- CUDA
- Qwen2.5-1.5B-Instruct
- Ubuntu 24.04 (WSL)
- VS Code

---

# 📁 Project Structure

```
my-mcp/

├── docs/
├── models/
├── tests/

├── src/
│
├── agent/
│   └── dispatcher.py
│
├── config/
│   └── settings.py
│
├── llm/
│   ├── model.py
│   └── generator.py
│
├── memory/
│
├── mcp/
│
├── prompts/
│
├── tools/
│   ├── registry.py
│   ├── file_tools.py
│   └── shell_tools.py
│
└── main.py

README.md
requirements.txt
.gitignore
```

---

# ✅ Completed Features

## Project Foundation

- [x] Project structure
- [x] Configuration system
- [x] CUDA detection
- [x] Hugging Face model loading
- [x] Chat generation
- [x] Tool registry

---

## File Tools

Implemented:

- Read file
- Write file
- Append file
- List directory
- Check file existence
- Create directory

Status:

✅ Tested

---

## Shell Tools

Implemented:

- Execute shell commands
- Execute Python code
- Current working directory
- Directory listing
- Locate executable
- Check command availability

Status:

✅ Tested

---

# 🚧 Current Sprint

## Sprint 3

Building:

- Tool Dispatcher

Goal:

Allow Pearl to execute tools dynamically through the registry.

Architecture:

```
User

↓

LLM

↓

Tool Dispatcher

↓

Tool Registry

↓

Tool
```

Status:

🚧 In Progress

---

# 📅 Roadmap

## Phase 1

- [x] Configuration
- [x] LLM Loading
- [x] Chat Generation
- [x] Tool Registry
- [x] File Tools
- [x] Shell Tools
- [ ] Tool Dispatcher
- [ ] Tool Calling

---

## Phase 2

Memory

- Conversation History
- Sliding Window
- Context Management
- Summarization

---

## Phase 3

Planner

- Task decomposition
- Sequential execution
- Multi-step reasoning

---

## Phase 4

Custom MCP Server

No FastMCP.

Implement from scratch:

- JSON-RPC
- Tools
- Resources
- Prompts

---

## Phase 5

IDE Integrations

- Cursor
- Claude Desktop
- VS Code

---

## Phase 6

Developer Tools

- Git
- Docker
- Kubernetes
- Terraform
- AWS CLI

---

## Phase 7

Advanced AI

- Codebase indexing
- Vector memory
- Multi-agent architecture
- Autonomous planning

---

# 🧪 Development Principles

Every feature follows the same lifecycle:

1. Design
2. Implement
3. Test
4. Verify
5. Commit
6. Push

No feature is considered complete until it has been tested.

---

# 📌 Current Progress

```
██████████████████░░░░░░░░

Project Foundation      ✅
LLM                     ✅
Generation              ✅
Tool Registry           ✅
File Tools              ✅
Shell Tools             ✅
Dispatcher              🚧
Tool Calling            ⏳
Memory                  ⏳
Planner                 ⏳
MCP                     ⏳
Integrations            ⏳
```

---

# 🎯 Ultimate Goal

Pearl should become a production-quality AI Coding Agent capable of assisting software engineers with real-world development tasks while remaining fully modular, extensible, and self-hosted.

---

# 🦪 Pearl Motto

> **"Build once. Scale forever."**