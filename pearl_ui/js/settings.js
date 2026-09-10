// Connection status, model provider, and the settings dialog.

import { get, post } from "./api.js";
import { addErr, addInfo } from "./conversation.js";
import { connDot, connLbl, g, S, wsName } from "./state.js";

// ─── Status ───────────────────────────────────────────────────
export async function pollStatus() {
  try {
    const d = await get('/api/status');
    // Reachable is not the same as usable. A server whose interpreter
    // lacks the model library answers /api/status perfectly while every
    // actual request fails — showing a healthy "Connected" there sends
    // the user hunting through their prompt instead of their setup.
    if (d.model_ready === false) {
      connDot.className = 'err';
      connLbl.textContent = 'Model unavailable';
      if (d.model_error && !S.modelWarned) {
        S.modelWarned = true;
        addErr(d.model_error);
      }
    } else {
      connDot.className = 'ok';
      connLbl.textContent = 'Connected';
      S.modelWarned = false;
    }
    if (d.workspace) {
      S.ws = d.workspace;
      wsName.textContent = d.workspace.name || d.workspace.path;
      g('sd-ws').value = d.workspace.path || '';
    }
  } catch {
    connDot.className = 'err';
    connLbl.textContent = 'Disconnected';
  }
}

// ─── Provider ─────────────────────────────────────────────────
let S_prov = { provider: 'pearl', configured: false, ollama_running: false };

export async function loadProvider() {
  try {
    const p = await get('/api/provider');
    S_prov = p;
    const badge = g('provider-badge');
    if (badge) badge.textContent = p.display || p.provider;
    renderProviderTiles(p);
  } catch {}
}

function renderProviderTiles(p) {
  ['pearl', 'advanced'].forEach(id => {
    const t = g('prov-' + id);
    if (t) t.classList.toggle('active', p.provider === id);
  });

  const pearlSt = g('prov-pearl-status');
  if (pearlSt) {
    if (p.configured && p.provider === 'pearl') {
      pearlSt.textContent = 'Configured';
      pearlSt.className = 'prov-status ok';
    } else if (!p.configured && p.provider === 'pearl') {
      pearlSt.textContent = 'llama-cpp-python not installed';
      pearlSt.className = 'prov-status warn';
    } else {
      pearlSt.textContent = p.configured ? '—' : 'Not configured';
      pearlSt.className = 'prov-status';
    }
  }

  const note = g('prov-note');
  if (note) {
    if (!p.configured && p.provider === 'pearl') {
      note.textContent = 'Install llama-cpp-python to enable local AI (no API key needed). See docs/PEARL_INFERENCE_CONTRACT.md.';
    } else {
      note.textContent = '';
    }
  }
}

// Called from an inline onclick in the settings markup, so main.js
// publishes it on window.
export async function selectProvider(name) {
  if (name === 'advanced') {
    const note = g('prov-note');
    if (note) note.textContent = 'Set PEARL_LLM_PROVIDER and matching credentials in your .env file, then restart.';
    const pearlT = g('prov-pearl'); if (pearlT) pearlT.classList.remove('active');
    const adv = g('prov-advanced'); if (adv) adv.classList.add('active');
    return;
  }
  try {
    const r = await post('/api/provider', { provider: name });
    if (r.ok) {
      addInfo(`Model provider: ${r.display}`);
      await loadProvider();
    }
  } catch (e) { addErr(`Could not switch provider: ${e.message}`); }
}

// ─── Settings ─────────────────────────────────────────────────
export function openSettings() {
  g('settings-modal').classList.add('open');
  g('sd-ws').focus();
  loadProvider();
}

export function closeSettings() { g('settings-modal').classList.remove('open'); }

export async function saveSettings() {
  const path = g('sd-ws').value.trim(); if (!path) return;
  try {
    const r = await post('/api/workspace', { path });
    if (r.ok) { S.ws = r.workspace; wsName.textContent = r.workspace.name; closeSettings(); addInfo(`Workspace: ${r.workspace.path}`); }
    else addErr(r.detail || 'Could not change workspace.');
  } catch (e) { addErr(e.message); }
}
