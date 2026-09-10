// The checkpoints panel.
//
// Restore is never one click. It can delete files created since the
// snapshot, so the panel asks the server what would change, shows that,
// and only then offers a confirmation. Same reason Pearl shows a diff
// before it writes.
//
// On a shared instance every mutating route answers 403, because all
// users share one workspace and therefore one store. The panel shows
// that refusal rather than hiding the controls, so the reason is
// visible instead of mysterious.

import { get, post } from "./api.js";
import { escapeHtml as esc, relativeTime as relTs } from "./format.js";
import { g, mk } from "./state.js";

async function send(method, path) {
  const r = await fetch(path, { method });
  if (!r.ok) throw new Error((await r.text()) || `HTTP ${r.status}`);
  return r.json();
}

function message(text, cls = "cp-empty") {
  const el = mk("div", cls);
  el.textContent = text;
  return el;
}

function fileList(title, files) {
  if (!files.length) return "";
  const shown = files.slice(0, 8).map((f) => `<li>${esc(f)}</li>`).join("");
  const more = files.length > 8 ? `<li>and ${files.length - 8} more</li>` : "";
  return `<div class="cp-files"><strong>${esc(title)}</strong><ul>${shown}${more}</ul></div>`;
}

// Replace a row with the restore report and a confirmation.
function confirmRestore(row, cp, report) {
  const box = mk("div", "cp-confirm");

  if (!report.changedAnything) {
    box.innerHTML = `
      <p class="cp-confirm-txt">Nothing to restore. The workspace already
      matches <strong>${esc(cp.label)}</strong>.</p>`;
    const back = mk("button", "cp-act");
    back.textContent = "Close";
    back.addEventListener("click", () => row.replaceChildren(...rowParts(cp)));
    box.appendChild(back);
    row.replaceChildren(box);
    return;
  }

  box.innerHTML = `
    <p class="cp-confirm-txt">Restoring <strong>${esc(cp.label)}</strong> will
    change ${report.restored.length + report.removed.length} file(s).</p>
    ${fileList("Restored", report.restored)}
    ${fileList("Deleted", report.removed)}`;

  const actions = mk("div", "cp-confirm-acts");
  const cancel = mk("button", "cp-act");
  cancel.textContent = "Cancel";
  cancel.addEventListener("click", () => row.replaceChildren(...rowParts(cp)));

  const go = mk("button", "cp-act cp-danger");
  go.textContent = "Restore";
  go.addEventListener("click", async () => {
    go.disabled = true;
    go.textContent = "Restoring…";
    try {
      await post(`/api/checkpoints/${cp.id}/restore`);
      await loadCheckpoints();
    } catch (e) {
      box.appendChild(message(e.message, "cp-error"));
    }
  });

  actions.append(cancel, go);
  box.appendChild(actions);
  row.replaceChildren(box);
}

function rowParts(cp) {
  const label = mk("span", "cp-label");
  label.textContent = cp.label;

  const when = mk("span", "cp-when");
  when.textContent = relTs(Date.parse(cp.createdAt));

  const id = mk("span", "cp-id");
  id.textContent = cp.shortId;

  const acts = mk("span", "cp-acts");

  const restore = mk("button", "cp-act");
  restore.textContent = "Restore…";
  restore.setAttribute("aria-label", `Preview restoring ${cp.label}`);

  const rename = mk("button", "cp-act");
  rename.textContent = "Rename";

  const del = mk("button", "cp-act");
  del.textContent = "Delete";

  acts.append(restore, rename, del);
  return [label, when, id, acts];
}

function row(cp) {
  const el = mk("div", "cp-row");
  el.replaceChildren(...rowParts(cp));

  el.addEventListener("click", async (e) => {
    const btn = e.target.closest(".cp-act");
    if (!btn) return;

    try {
      if (btn.textContent.startsWith("Restore")) {
        const report = await get(`/api/checkpoints/${cp.id}/restore-preview`);
        confirmRestore(el, cp, report);
      } else if (btn.textContent === "Rename") {
        const label = prompt("Rename checkpoint:", cp.label);
        if (!label?.trim()) return;
        await fetch(`/api/checkpoints/${cp.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ label: label.trim() }),
        }).then(async (r) => {
          if (!r.ok) throw new Error((await r.text()) || `HTTP ${r.status}`);
        });
        await loadCheckpoints();
      } else if (btn.textContent === "Delete") {
        if (!confirm(`Delete checkpoint "${cp.label}"?`)) return;
        await send("DELETE", `/api/checkpoints/${cp.id}`);
        await loadCheckpoints();
      }
    } catch (err) {
      el.appendChild(message(err.message, "cp-error"));
    }
  });

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

async function createCheckpoint() {
  const btn = g("cp-new");
  btn.disabled = true;
  try {
    const { checkpoint } = await post("/api/checkpoints", { label: "Manual checkpoint" });
    if (!checkpoint) {
      // create() returns nothing when the workspace has not changed.
      // Saying so beats a list that silently does not grow.
      g("cp-list").prepend(
        message("Nothing new to capture since the last checkpoint.", "cp-error"),
      );
      return;
    }
    await loadCheckpoints();
  } catch (e) {
    g("cp-list").prepend(message(e.message, "cp-error"));
  } finally {
    btn.disabled = false;
  }
}

export function openCheckpoints() {
  g("cp-modal").classList.add("open");
  loadCheckpoints();
}

export function closeCheckpoints() {
  g("cp-modal").classList.remove("open");
}

export function initCheckpoints() {
  g("cp-new").addEventListener("click", createCheckpoint);
}
