// The memory viewer.
//
// Read-only, and deliberately plain: this is the answer to "why did
// Pearl do that", so it shows what is actually stored rather than a
// summary of it.

import { get } from "./api.js";
import { escapeHtml as esc, timeString as tsStr } from "./format.js";
import { g, mk } from "./state.js";

// Section order is the order a question gets asked in: what was said,
// what was asked for, what Pearl believes about the project, what it
// actually ran.
const SECTIONS = [
  ["conversation", "Conversation", (t) => `${t.role}: ${t.content}`],
  ["tasks", "Tasks", (t) => t.description ?? t.goal ?? JSON.stringify(t)],
  ["execution_history", "Executed", (r) => `${r.tool_name ?? r.tool ?? "step"}`],
];

function entry(text, meta) {
  const el = mk("div", "mem-item");
  el.innerHTML = `<span class="mem-txt">${esc(text)}</span>${
    meta ? `<span class="mem-meta">${esc(meta)}</span>` : ""
  }`;
  return el;
}

function section(title, items, render) {
  const wrap = mk("div", "mem-sec");
  const head = mk("div", "sd-lbl");
  head.textContent = `${title} (${items.length})`;
  wrap.appendChild(head);

  if (!items.length) {
    const none = mk("div", "cp-empty");
    none.textContent = "Nothing recorded yet.";
    wrap.appendChild(none);
    return wrap;
  }

  // Newest last matches how the conversation itself reads.
  items.slice(-25).forEach((item) => {
    const ts = item.timestamp ?? item.ts;
    wrap.appendChild(entry(String(render(item)), ts ? tsStr(Date.parse(ts)) : ""));
  });
  return wrap;
}

function projectFacts(project) {
  const wrap = mk("div", "mem-sec");
  const head = mk("div", "sd-lbl");
  const keys = Object.keys(project ?? {});
  head.textContent = `Project facts (${keys.length})`;
  wrap.appendChild(head);

  if (!keys.length) {
    const none = mk("div", "cp-empty");
    none.textContent = "Nothing recorded yet.";
    wrap.appendChild(none);
    return wrap;
  }

  keys.forEach((k) => wrap.appendChild(entry(`${k}: ${project[k]}`)));
  return wrap;
}

export async function loadMemory() {
  const body = g("mem-body");
  const loading = mk("div", "cp-empty");
  loading.textContent = "Loading…";
  body.replaceChildren(loading);

  let data;
  try {
    data = await get("/api/memory");
  } catch (e) {
    const err = mk("div", "cp-empty");
    err.textContent = `Could not load memory. ${e.message}`;
    body.replaceChildren(err);
    return;
  }

  body.replaceChildren(
    ...SECTIONS.map(([key, title, render]) =>
      section(title, data[key] ?? [], render),
    ),
    projectFacts(data.project),
  );
}

export function openMemory() {
  g("mem-modal").classList.add("open");
  loadMemory();
}

export function closeMemory() {
  g("mem-modal").classList.remove("open");
}
