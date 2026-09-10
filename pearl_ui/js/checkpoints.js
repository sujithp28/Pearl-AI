// The checkpoints panel.
//
// Read-only for now: list and nothing else. Creating, renaming,
// deleting and restoring arrive in the next step, behind the shared
// instance gate described in section 9 of the spec.

import { get } from "./api.js";
import { escapeHtml as esc, relativeTime as relTs } from "./format.js";
import { g, mk } from "./state.js";

function row(cp) {
  const el = mk("div", "cp-row");
  el.innerHTML = `
    <span class="cp-label">${esc(cp.label)}</span>
    <span class="cp-when">${relTs(Date.parse(cp.createdAt))}</span>
    <span class="cp-id">${esc(cp.shortId)}</span>`;
  return el;
}

function message(text) {
  const el = mk("div", "cp-empty");
  el.textContent = text;
  return el;
}

export async function loadCheckpoints() {
  const list = g("cp-list");
  list.replaceChildren(message("Loading…"));

  let checkpoints;
  try {
    ({ checkpoints } = await get("/api/checkpoints"));
  } catch (e) {
    // Say what failed. "Could not load" alone sends the user looking in
    // the wrong place when the real answer is that the server is down.
    list.replaceChildren(message(`Could not load checkpoints. ${e.message}`));
    return;
  }

  if (!checkpoints.length) {
    list.replaceChildren(
      message("No checkpoints yet. Pearl takes one before it writes to disk."),
    );
    return;
  }

  list.replaceChildren(...checkpoints.map(row));
}

export function openCheckpoints() {
  g("cp-modal").classList.add("open");
  loadCheckpoints();
}

export function closeCheckpoints() {
  g("cp-modal").classList.remove("open");
}
