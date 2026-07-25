/**
 * Shared request timeout for MCP calls backed by a real LLM call
 * (`pearl/chat`, `pearl/planOnly`, `pearl/runAutonomous`,
 * `pearl/approvePatches`, `pearl/rejectPatches`).
 *
 * Local models (the default, privacy-first path) can take anywhere
 * from a few seconds to several minutes depending on hardware and
 * memory pressure — measured directly against this project's own MCP
 * server: a single `pearl/planOnly` call took ~114s end to end under
 * constrained conditions. `MCPConnection`'s short default (tuned for
 * cheap, non-LLM round trips like `tools/list`) is nowhere near
 * enough for any call that goes through the LLM, regardless of which
 * one it is — so they all share this one, generous budget rather
 * than each guessing its own (a `pearl/chat` request and a
 * `pearl/planOnly` request cost the same order of work: one LLM
 * call).
 */
export const LLM_CALL_TIMEOUT_MS = 300000;
