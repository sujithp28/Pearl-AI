# 🦪 Pearl

> **Production-Quality AI Coding Agent Built from Scratch**

![Python](https://img.shields.io/badge/Python-3.12-blue)
![Status](https://img.shields.io/badge/Status-Active%20Development-success)
![License](https://img.shields.io/badge/License-MIT-yellow)

---

# 📖 Overview

Pearl is an open-source AI Coding Agent designed to become a complete software engineering assistant.

Unlike a traditional chatbot, Pearl is being built as a modular AI system capable of understanding projects, executing tools, maintaining memory, planning complex tasks, and integrating with developer environments.

The long-term vision is to build a self-hostable coding agent comparable to Codex, Claude Code, Cursor, and Cline while remaining completely extensible.

---

# 🎯 Vision

Pearl should eventually be able to:

- Understand entire codebases
- Read and modify source code
- Execute development tools
- Plan multi-step engineering tasks
- Maintain project memory
- Verify its own work
- Integrate with IDEs
- Communicate using MCP (Model Context Protocol)

Example:

> "Create a FastAPI project, Dockerize it, deploy it to AWS, run the tests, and commit the changes."

---

# 🏗 Current Architecture

```
                User
                  │
                  ▼
            Language Model
                  │
                  ▼
          Tool Dispatcher (Upcoming)
                  │
                  ▼
            Tool Registry
                  │
                  ▼
        Registered Tool Objects
                  │
                  ▼
           Tool Implementations
```

---

# 🛠 Technology Stack

- Python 3.12
- Hugging Face Transformers
- PyTorch
- CUDA
- VS Code
- Ubuntu (WSL)

Future Support

- OpenAI
- Anthropic
- Ollama
- LM Studio
- OpenRouter
- MCP

---

# 📂 Project Structure

```
my-mcp/

├── docs/
├── models/
├── tests/
│
├── src/
│
├── agent/
│
├── config/
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

## 🧠 Core Foundation

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

## 📂 File Tools

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

## 💻 Shell Tools

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

## 🛠 Tool Metadata System

Implemented

- Tool Decorator
- Tool Dataclass
- Metadata Attachment
- Automatic Tool Registration
- Tool Discovery

Example

```python
@tool("Read the contents of a UTF-8 text file.")
def read_file(path: str):
    ...
```

Status

✅ Completed

---

## 📦 Tool Registry

Current Features

- Register Tool
- Retrieve Tool
- List Tools
- Store Metadata
- Execute Stored Function

Status

✅ Completed

---

# 🚧 Current Sprint

## Sprint 4

Building

- Tool Dispatcher

Goal

Execute tools dynamically without directly referencing implementations.

Future Flow

```
User

↓

LLM

↓

Dispatcher

↓

Registry

↓

Tool

↓

Execution
```

---

# 📈 Development Roadmap

## Phase 1 — Foundation

- [x] Project Structure
- [x] Configuration
- [x] Hugging Face Integration
- [x] Text Generation
- [x] File Tools
- [x] Shell Tools
- [x] Tool Metadata
- [x] Tool Registry
- [ ] Dispatcher
- [ ] Tool Calling

---

## Phase 2 — Memory

- Conversation Memory
- Project Memory
- Context Window
- Summarization

---

## Phase 3 — Planning

- Planner
- Task Decomposition
- Multi-step Execution
- Error Recovery

---

## Phase 4 — MCP

- Custom MCP Server
- JSON-RPC
- Tool Exposure
- Resources
- Prompts

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
- Autonomous Agents
- Multi-Agent Collaboration

---

# 📊 Current Progress

```
████████████████████████░░░░░░

✅ Project Foundation
✅ Configuration
✅ LLM
✅ Text Generation
✅ File Tools
✅ Shell Tools
✅ Tool Metadata
✅ Tool Registry
⬜ Dispatcher
⬜ Tool Calling
⬜ Memory
⬜ Planner
⬜ MCP
⬜ IDE Integration
⬜ Autonomous Execution
```

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

No feature is considered complete until it has been verified.

---

# 🎯 Long-Term Goal

Pearl is being designed as a complete AI software engineering platform capable of:

- Understanding projects
- Planning tasks
- Executing code
- Using developer tools
- Maintaining long-term project memory
- Verifying its own work
- Operating locally or in the cloud

The objective is not just code generation—it is project understanding, reliable execution, and maintainable software engineering workflows.

---

# 📌 Project Status

Current Version

```
v0.1.0-dev
```

Status

```
🚧 Active Development
```

---

# 🤝 Contributing

Contributions, discussions, and ideas are welcome.

As Pearl evolves, additional documentation will be added under the `docs/` directory, including architecture guides, coding standards, roadmap details, and MCP design documentation.

---

# 📄 License

MIT License (to be added before the first public release)

---

# 🦪 Motto

> **"Understand. Plan. Execute. Verify."**

Built with ❤️ using Python.
