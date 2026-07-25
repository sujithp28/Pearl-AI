import assert from "node:assert/strict";
import { test } from "node:test";
import { LLM_CALL_TIMEOUT_MS } from "../mcp/timeouts";

test("LLM_CALL_TIMEOUT_MS is generous enough for a real local-model call", () => {
  // Regression guard: measured directly against this project's own
  // MCP server, a single pearl/planOnly call took ~114s under
  // constrained conditions (see fix commit). This budget must stay
  // comfortably above that, not just above some arbitrary round
  // number that happens to look big.
  assert.ok(LLM_CALL_TIMEOUT_MS >= 150000, "must be at least 150s");
});
