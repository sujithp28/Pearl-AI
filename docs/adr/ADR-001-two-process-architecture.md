# ADR-001 — Two-Process Architecture: TypeScript Extension + Python Backend

**Date:** 2026-07-26  
**Status:** Accepted  
**Deciders:** Lead Architect

---

## Context

Pearl needs to run as a VS Code extension while also executing Python code, calling
LLMs, running shell commands, and managing the local filesystem. VS Code extensions
run in a TypeScript/Node.js host process. Python cannot be embedded in that process
in a way that is stable, cross-platform, and maintainable.

Two architecture options were considered: a single-process approach (everything in
TypeScript or everything in Python with a VS Code extension API bridge) and a
two-process approach (TypeScript for the extension host, Python for the backend).

The decision had to satisfy three constraints:
- The agent backend needs to be testable independently of VS Code
- The LLM integrations, tool implementations, and file operations benefit from the
  Python ecosystem (asyncio, pathlib, subprocess management)
- The VS Code UI components must use the VS Code extension API, which is TypeScript-only

---

## Decision

Pearl is implemented as two separate processes communicating over JSON-RPC 2.0 via
stdio. The TypeScript VS Code extension handles all UI concerns and spawns the Python
backend as a subprocess. All tool execution, LLM communication, file I/O, and approval
logic live in the Python process. No logic crosses the boundary except JSON-serializable
values over the MCP protocol.

---

## Alternatives Considered

| Alternative | Why rejected |
|---|---|
| Pure TypeScript (Node.js backend) | Python ecosystem advantages for async I/O, subprocess handling, and ML tooling outweigh the simplification of a single language |
| Python VS Code extension via Pylance bridge | Unstable, not officially supported, would couple Pearl's architecture to an unofficial VS Code extension mechanism |
| HTTP instead of stdio for IPC | stdio is simpler, has no port conflict risk, has no authentication requirement, and is faster for local IPC; HTTP adds network stack overhead for a local-only connection |
| gRPC instead of MCP/JSON-RPC | MCP is the emerging standard for AI agent tool protocols; adopting it positions Pearl for future tool ecosystem compatibility |

---

## Consequences

**Positive:**
- The Python backend is fully testable without VS Code: `pytest tests/` works standalone
- LLM provider integrations, async I/O, and tool implementations use the Python
  ecosystem's mature libraries
- The two processes can evolve independently as long as the MCP protocol contract is preserved
- The TypeScript extension can be replaced or supplemented (CLI, GitHub Action, web UI)
  without changing the Python backend

**Negative / Trade-offs:**
- Contributors need working knowledge of both TypeScript and Python
- Process startup has a latency cost (the Python subprocess must be spawned and the
  MCP handshake completed before the first tool call)
- Debugging requires coordinating across two process boundaries

**Risks:**
- Protocol drift: if the extension and backend diverge on MCP method shapes,
  the system breaks silently. Mitigated by: versioned `initialize` response,
  `MCP-7` backward-compatibility rule, and integration tests that exercise the
  full stdio round-trip.

---

## Notes

This decision is implemented and enforced by `01_ARCHITECTURE_RULES.md` Sections 1–2.
The boundary rules (B-1 through B-4) are the operational expression of this ADR.
Any proposal to collapse the two processes back into one must supersede this ADR.
