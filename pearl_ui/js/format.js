// Pure formatting helpers: no DOM, no network, no module state.
//
// Everything here is a function of its arguments, which is what makes
// this the one module in pearl_ui/js that can be tested without a
// browser. See format.test.js.

// Escape text destined for an innerHTML assignment, quotes included.
//
// Use this by default. `escapeText` below is the weaker one and exists
// only for markdown rendering, where the result is placed as element
// text and a literal quote must survive.
export function escapeHtml(s) {
  if (!s) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// Escape the three characters that can open a tag, leaving quotes.
//
// The ampersand must be replaced first. Doing it after < would turn the
// "&" of an already-emitted "&lt;" back into an entity prefix and the
// escaping would undo itself.
export function escapeText(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function nowTime() {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function timeString(ts) {
  if (!ts) return "";
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

// Age of a timestamp, at the coarsest useful unit.
//
// Floors to a minimum of one minute: "0m" on a conversation saved a
// second ago reads as a bug rather than as freshness.
export function relativeTime(ts) {
  const d = Date.now() - ts;
  if (d < 3600000) return Math.max(1, Math.floor(d / 60000)) + "m";
  if (d < 86400000) return Math.floor(d / 3600000) + "h";
  return Math.floor(d / 86400000) + "d";
}

export function toolLabel(name) {
  if (!name) return "Tool";
  const m = {
    read_file: "Read file",
    write_file: "Write file",
    search_text: "Search",
    find_symbol: "Find symbol",
    execute_shell: "Run command",
    index_repository: "Index repo",
    explain_file: "Explain file",
    summarize_project: "Summarize project",
    find_references: "Find references",
    rename_symbol: "Rename symbol",
    batch_write_files: "Write files",
    create_file: "Create file",
    replace_in_file: "Replace in file",
  };
  return m[name] || name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

// Pick the most useful text a finished run produced, for when the
// synthesizer gave no final answer.
//
// Walks backwards on the assumption that later steps supersede earlier
// ones, and prefers tools whose output is prose a human would want to
// read over tools whose output is a status. The 5000-character cap stops
// one `read_file` on a large file from burying the whole transcript.
export function bestOutput(steps) {
  if (!steps?.length) return null;
  const textTools = [
    "explain_file",
    "summarize_project",
    "search_text",
    "find_symbol",
    "find_references",
    "read_file",
  ];
  for (let i = steps.length - 1; i >= 0; i--) {
    const s = steps[i];
    if (!s.succeeded) continue;
    if (textTools.some((t) => s.tool_name?.includes(t))) {
      if (typeof s.result === "string" && s.result.length > 50) return s.result.slice(0, 5000);
      if (s.summary?.length > 20) return s.summary;
    }
  }
  for (let i = steps.length - 1; i >= 0; i--) {
    const s = steps[i];
    if (s.succeeded && typeof s.result === "string" && s.result.length > 80) {
      return s.result.slice(0, 5000);
    }
  }
  return null;
}
