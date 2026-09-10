// Entry point: publish the two inline-handler globals, bind the
// document's controls, and boot.

import {
  closeCheckpoints,
  initCheckpoints,
  openCheckpoints,
} from "./checkpoints.js";
import { applyMode, initCodeMode } from "./code.js";
import { closeMemory, openMemory } from "./memory.js";
import {
  cancel,
  loadStore,
  newChat,
  renderSidebar,
  resize,
  submit,
} from "./conversation.js";
import { doCopy } from "./markdown.js";
import {
  closeSettings,
  loadProvider,
  openSettings,
  pollStatus,
  saveSettings,
  selectProvider,
} from "./settings.js";
import { g, inputTxt, POLL_MS, S, sbSearch, sendBtn, sidebar, stopBtn } from "./state.js";

// Module scope is not global scope. Two handlers are written as inline
// onclick attributes — `doCopy` in the markup renderMd generates for a
// code block, and `selectProvider` in the settings dialog — so they must
// be reachable by name from the document. Converting both to delegated
// listeners would mean changing generated markup, which this split is
// deliberately not doing.
window.doCopy = doCopy;
window.selectProvider = selectProvider;

// ─── Boot ─────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', async () => {
  await loadStore();
  renderSidebar();
  wire();
  initCodeMode();
  initCheckpoints();
  applyMode();
  pollStatus();
  loadProvider();
  setInterval(pollStatus, POLL_MS);
  resize();
});

// ─── Wire ─────────────────────────────────────────────────────
function wire() {
  g('sb-toggle').addEventListener('click', () => {
    const c = sidebar.classList.toggle('collapsed');
    g('sb-toggle').setAttribute('aria-expanded', !c);
  });
  g('new-chat-btn').addEventListener('click', () => newChat());
  g('ws-badge').addEventListener('click', openSettings);
  g('settings-btn').addEventListener('click', openSettings);
  g('cp-btn').addEventListener('click', openCheckpoints);
  g('mem-btn').addEventListener('click', openMemory);
  g('mem-close').addEventListener('click', closeMemory);
  g('mem-modal').addEventListener('click', e => { if (e.target === g('mem-modal')) closeMemory(); });
  g('cp-close').addEventListener('click', closeCheckpoints);
  g('cp-modal').addEventListener('click', e => { if (e.target === g('cp-modal')) closeCheckpoints(); });
  g('sd-cancel').addEventListener('click', closeSettings);
  g('sd-save').addEventListener('click', saveSettings);
  g('settings-modal').addEventListener('click', e => { if (e.target === g('settings-modal')) closeSettings(); });
  g('theme-btn').addEventListener('click', () => {
    const t = document.documentElement.getAttribute('data-theme');
    document.documentElement.setAttribute('data-theme', t === 'dark' ? 'light' : 'dark');
  });
  document.querySelectorAll('.mode-btn').forEach(b => b.addEventListener('click', () => {
    S.mode = b.dataset.mode;
    document.querySelectorAll('.mode-btn').forEach(x => {
      x.classList.toggle('active', x.dataset.mode === S.mode);
      x.setAttribute('aria-pressed', String(x.dataset.mode === S.mode));
    });
    applyMode();
  }));
  sendBtn.addEventListener('click', submit);
  stopBtn.addEventListener('click', cancel);
  inputTxt.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); } });
  inputTxt.addEventListener('input', resize);
  sbSearch.addEventListener('input', () => renderSidebar(sbSearch.value.trim().toLowerCase()));
  document.querySelectorAll('.es-chip').forEach(c => {
    c.addEventListener('click', () => { inputTxt.value = c.dataset.prompt; resize(); submit(); });
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') { closeSettings(); closeCheckpoints(); closeMemory(); }
    if (e.key === '/' && document.activeElement !== inputTxt && !g('settings-modal').classList.contains('open')) {
      e.preventDefault(); inputTxt.focus();
    }
  });
  g('sd-ws').addEventListener('keydown', e => { if (e.key === 'Enter') saveSettings(); });
}
