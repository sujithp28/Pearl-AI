// The conversation: composing, running, approving, rendering, storing.
//
// These concerns live in one module because they are mutually
// dependent, not for convenience. A run appends message DOM, message
// DOM writes to the conversation store, the store re-renders the
// sidebar, and the sidebar's regenerate action starts a run. Splitting
// that ring across files would buy four import cycles and no clarity.

import { get, post, sse } from "./api.js";
import {
  bestOutput,
  escapeHtml as esc,
  nowTime,
  relativeTime as relTs,
  timeString as tsStr,
  toolLabel,
} from "./format.js";
import { renderMd } from "./markdown.js";
import {
  chatTtl,
  emptySt,
  g,
  inputTxt,
  MAX_CONVS,
  mk,
  msgInner,
  S,
  sbList,
  sendBtn,
  STORE_KEY,
  stopBtn,
} from "./state.js";

// ─── Composer ─────────────────────────────────────────────────
export function resize() {
  inputTxt.style.height = 'auto';
  inputTxt.style.height = Math.min(inputTxt.scrollHeight, 200) + 'px';
}

export function submit() {
  const text = inputTxt.value.trim();
  if (!text || S.streaming) return;
  inputTxt.value = ''; resize();
  hideEmpty(); ensureConv();
  addUserMsg(text);
  if (S.mode === 'chat') doChat(text);
  else doRun(text);
}

function hideEmpty() { if (emptySt.parentNode) emptySt.remove(); }

function setStreaming(v) {
  S.streaming = v;
  sendBtn.disabled = v;
  stopBtn.classList.toggle('show', v);
}

export async function cancel() {
  if (S.ctrl) S.ctrl.abort();
  try { await post('/api/cancel'); } catch {}
  setStreaming(false);
}

// ─── Chat ─────────────────────────────────────────────────────
async function doChat(message) {
  setStreaming(true);
  S.ctrl = new AbortController();
  const { setText } = addPearlBubble();
  let full = '';
  let errored = false;
  try {
    const resp = await fetch('/api/chat', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ message }), signal: S.ctrl.signal,
    });
    for await (const ev of sse(resp)) {
      if (ev.event === 'chunk') { full += ev.data.text || ''; setText(full, true); }
      else if (ev.event === 'error') { errored = true; setText(ev.data.text || 'Error.', false); break; }
      else if (ev.event === 'done') break;
    }
    if (!errored) { setText(full, false); saveMsg('assistant', full); }
  } catch (e) { if (e.name !== 'AbortError') addErr(e.message); }
  finally { setStreaming(false); }
}

// ─── Agent run ────────────────────────────────────────────────
async function doRun(prompt) {
  setStreaming(true);
  S.ctrl = new AbortController();
  const groupEl = makeGroup();
  const stepMap = new Map();

  try {
    const resp = await fetch('/api/run', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ prompt }), signal: S.ctrl.signal,
    });
    for await (const ev of sse(resp)) {
      if (ev.event === 'progress') onProgress(ev.data, groupEl, stepMap);
      else if (ev.event === 'result') await onResult(ev.data, groupEl, stepMap);
      else if (ev.event === 'error') addErr(ev.data.message || 'Run failed.');
      else if (ev.event === 'context_condensed') onContextCondensed(ev.data);
      else if (ev.event === 'run_failed') { /* handled by error event */ }
      else if (ev.event === 'run_complete') { /* handled by result event */ }
      else if (ev.event === 'done') break;
    }
  } catch (e) { if (e.name !== 'AbortError') addErr(e.message); groupHdr(groupEl,'err','✕ Stopped',null,null); }
  finally { setStreaming(false); }
}

function onProgress(d, grp, map) {
  const { status, currentStep: cs, totalSteps: ts, currentAction: ca } = d;
  if (status === 'planning') groupHdr(grp,'run','⚡ Planning…',cs,ts);
  else if (status === 'executing_step') {
    groupHdr(grp,'run','⚡ Pearl is working',cs,ts);
    if (ca) { const k = ca + (cs||0); if (!map.has(k)) { map.set(k, addStep(grp,ca,'run','')); } }
  } else if (status === 'step_completed') {
    if (ca) { const k = ca + (cs||0); const el = map.get(k); if (el) doneStep(el, true); }
  } else if (status === 'awaiting_approval') groupHdr(grp,'warn','📋 Review required',cs,ts);
  else if (status === 'task_completed') groupHdr(grp,'ok','✓ Done',cs,ts);
  // A run that ended in failure must not also render a green "Done" —
  // the error card below it says the opposite.
  else if (status === 'task_failed') groupHdr(grp,'err','✕ Failed',cs,ts);
  scrollBot();
}

async function onResult(d, grp, map) {
  const r = d.report;
  if (!r) return;
  if (r.steps?.length) {
    refreshSteps(grp, r.steps);
    const ok = r.steps.every(s => s.succeeded);
    groupHdr(grp, ok ? 'ok' : 'warn', ok ? `✓ Done — ${r.steps.length} step(s)` : '⚠ Some steps failed', r.steps.length, r.steps.length);
  }
  if (r.stop_reason === 'awaiting_approval') {
    groupHdr(grp,'warn','📋 Changes ready for review',null,null);
    await showDiff();
  } else if (!r.succeeded) {
    // Prefer a reason the user can act on. A planning failure produces no
    // steps, so `fail` is undefined and the old fallback printed the bare
    // stop_reason — "fatal_error" explains nothing. r.error carries what
    // actually went wrong.
    const fail = r.steps?.find(s => !s.succeeded);
    addErr(r.error || fail?.error || `Stopped: ${r.stop_reason}`);
    // The synthesizer's explanation was previously computed and discarded
    // on every failure path, which is exactly when it is most useful.
    if (r.final_answer) {
      const { setText } = addPearlBubble();
      setText(r.final_answer, false);
      saveMsg('assistant', r.final_answer);
    }
  } else {
    const txt = r.final_answer || bestOutput(r.steps);
    if (txt) { const { setText } = addPearlBubble(); setText(txt, false); saveMsg('assistant', txt); }
  }
}

function onContextCondensed(d) {
  const saved = d.tokens_before && d.tokens_after
    ? ` (~${Math.round((1 - d.tokens_after / d.tokens_before) * 100)}% tokens freed)`
    : '';
  addInfo(`Session history condensed${saved} to fit within context window.`);
}

// ─── Approval ─────────────────────────────────────────────────
async function showDiff() {
  let diff = '', files = [];
  try { const p = await get('/api/patches'); diff = p.diff || ''; files = p.files || []; } catch {}
  if (!files.length && !diff) return;
  addDiffCard(diff, files);
}
async function approve(card) {
  const ab = card.querySelector('.btn-approve');
  ab.disabled = true; ab.textContent = 'Applying…';
  try {
    const r = await post('/api/approve');
    card.remove();
    if (r.steps !== undefined) addResultCard(r);
    else addErr(r.error || 'Failed.');
    if (r.final_answer) { const { setText } = addPearlBubble(); setText(r.final_answer, false); saveMsg('assistant', r.final_answer); }
    else saveMsg('system','[Changes approved]');
  } catch (e) { addErr(e.message); }
}
async function reject(card) {
  try { await post('/api/reject'); } catch {}
  card.remove();
  addInfo('Changes rejected. Nothing written to disk.');
  saveMsg('system','[Changes rejected]');
}

// ─── Tool group DOM ───────────────────────────────────────────
function makeGroup() {
  const w = mk('div','tool-group open msg-row msg-system');
  w.innerHTML = `
    <div class="tg-head" role="button" aria-expanded="true">
      <span class="tg-icon spin">⟳</span>
      <span class="tg-title">⚡ Pearl is working…</span>
      <span class="tg-count"></span>
      <span class="tg-chev" aria-hidden="true"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 6l4 4 4-4"/></svg></span>
    </div>
    <div class="tg-body" role="list"></div>`;
  w.querySelector('.tg-head').addEventListener('click', () => {
    const o = w.classList.toggle('open');
    w.querySelector('.tg-head').setAttribute('aria-expanded', String(o));
  });
  msgInner.appendChild(w);
  scrollBot();
  return w;
}
function groupHdr(w, st, title, cur, tot) {
  const ic = w.querySelector('.tg-icon'), ti = w.querySelector('.tg-title'), cn = w.querySelector('.tg-count');
  ti.textContent = title;
  if (st === 'run') { ic.className = 'tg-icon spin'; ic.textContent = '⟳'; }
  else if (st === 'ok')   { ic.className = 'tg-icon'; ic.textContent = '✓'; ic.style.color = 'var(--suc)'; }
  else if (st === 'warn') { ic.className = 'tg-icon'; ic.textContent = '⚠'; ic.style.color = 'var(--warn)'; }
  else if (st === 'err')  { ic.className = 'tg-icon'; ic.textContent = '✕'; ic.style.color = 'var(--err)'; }
  if (cur != null && tot != null) cn.textContent = `${cur}/${tot}`;
}
function addStep(w, name, status, summary) {
  const body = w.querySelector('.tg-body');
  const el = mk('div','t-step'); el.setAttribute('role','listitem');
  const icon = status === 'run' ? '<span class="ts-ic spin" style="color:var(--acc-l)">⟳</span>'
             : status === 'ok'  ? '<span class="ts-ic" style="color:var(--suc)">✓</span>'
             :                    '<span class="ts-ic" style="color:var(--err)">✕</span>';
  el.innerHTML = `${icon}<span class="ts-nm">${esc(toolLabel(name))}</span><span class="ts-sm">${esc(summary||'')}</span>`;
  body.appendChild(el);
  return el;
}
function doneStep(el, ok) {
  const ic = el.querySelector('.ts-ic');
  if (ic) { ic.className = 'ts-ic'; ic.style.color = ok ? 'var(--suc)' : 'var(--err)'; ic.textContent = ok ? '✓' : '✕'; }
}
function refreshSteps(w, steps) {
  const body = w.querySelector('.tg-body'); body.innerHTML = '';
  steps.forEach(s => {
    const el = mk('div','t-step'); el.setAttribute('role','listitem');
    const ok = s.succeeded;
    const out = s.result || s.error || '';
    const hasOut = typeof out === 'string' && out.length > 0;
    el.innerHTML = `
      <span class="ts-ic" style="color:var(--${ok?'suc':'err'})">${ok?'✓':'✕'}</span>
      <span class="ts-nm">${esc(toolLabel(s.tool_name))}</span>
      <span class="ts-sm">${esc(s.summary||'')}</span>
      ${hasOut ? `<button class="ts-exp" aria-label="Toggle output">▾</button>` : ''}`;
    if (hasOut) {
      const outEl = mk('div','ts-out');
      outEl.textContent = typeof out === 'string' ? out : JSON.stringify(out,null,2);
      el.querySelector('.ts-exp').addEventListener('click', ev => {
        ev.stopPropagation();
        outEl.classList.toggle('show');
        ev.target.textContent = outEl.classList.contains('show') ? '▴' : '▾';
      });
      el.appendChild(outEl);
    }
    body.appendChild(el);
  });
}

// ─── Message DOM helpers ──────────────────────────────────────
function addUserMsg(text) {
  const r = mk('div','msg-row msg-user');
  r.innerHTML = `<div class="user-bub">${esc(text)}</div>`;
  msgInner.appendChild(r); scrollBot();
  saveMsg('user', text);
}
function addPearlBubble() {
  const r = mk('div','msg-row msg-pearl');
  r.innerHTML = `
    <div class="pearl-label"><span class="pearl-dot" aria-hidden="true"></span>Pearl</div>
    <div class="pearl-bub md"><span class="cursor" aria-hidden="true"></span></div>
    <div class="pearl-acts">
      <button class="pa-btn pa-copy" aria-label="Copy response">
        <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><rect x="5" y="5" width="9" height="9" rx="1.5"/><path d="M4 11H3a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1h7a1 1 0 0 1 1 1v1"/></svg>
        Copy
      </button>
      <button class="pa-btn pa-regen" aria-label="Regenerate response">
        <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M2 8a6 6 0 1 1 1.5 4"/><path d="M2 13V9h4" stroke-linejoin="round"/></svg>
        Regenerate
      </button>
    </div>
    <div class="msg-meta">${nowTime()}</div>`;
  msgInner.appendChild(r); scrollBot();
  const bub = r.querySelector('.pearl-bub');
  let _rawMd = '';
  function setText(md, streaming) {
    _rawMd = md || '';
    bub.innerHTML = md ? renderMd(md) : '';
    if (streaming) { const c = mk('span','cursor'); c.setAttribute('aria-hidden','true'); bub.appendChild(c); }
    scrollBot();
  }
  r.querySelector('.pa-copy').addEventListener('click', e => {
    const btn = e.currentTarget;
    navigator.clipboard.writeText(_rawMd).then(() => {
      btn.classList.add('copied'); btn.childNodes[btn.childNodes.length-1].textContent = ' Copied!';
      setTimeout(() => { btn.classList.remove('copied'); btn.childNodes[btn.childNodes.length-1].textContent = ' Copy'; }, 2000);
    }).catch(() => {});
  });
  r.querySelector('.pa-regen').addEventListener('click', () => {
    const c = S.convs.find(x => x.id === S.id);
    if (!c || S.streaming) return;
    const lastUser = [...c.msgs].reverse().find(m => m.role === 'user');
    if (!lastUser) return;
    // Remove this bubble from DOM and re-run
    r.remove();
    if (S.mode === 'chat') doChat(lastUser.content);
    else doRun(lastUser.content);
  });
  return { setText };
}
function addDiffCard(diff, files) {
  const card = mk('div','diff-card msg-row msg-system');
  const fileHtml = files.map(f => `<div class="diff-file"><span class="dft dft-m">M</span>${esc(f)}</div>`).join('');
  const diffHtml = diff.split('\n').map(line => {
    let cls = 'ctx';
    if (line.startsWith('+') && !line.startsWith('+++')) cls = 'add';
    else if (line.startsWith('-') && !line.startsWith('---')) cls = 'del';
    else if (line.startsWith('@@')) cls = 'hunk';
    return `<span class="dl ${cls}">${esc(line)}</span>`;
  }).join('');
  card.innerHTML = `
    <div class="diff-hd"><span aria-hidden="true">📄</span><span>Proposed changes${files.length ? ` — ${files.length} file${files.length!==1?'s':''}` : ''}</span></div>
    ${files.length ? `<div class="diff-files">${fileHtml}</div>` : ''}
    ${diff ? `<div class="diff-body">${diffHtml}</div>` : ''}
    <div class="diff-acts">
      <span class="diff-note">Pearl is asking for approval before writing to disk.</span>
      <button class="btn-reject">Reject</button>
      <button class="btn-approve">✓ Approve &amp; Apply</button>
    </div>`;
  card.querySelector('.btn-approve').addEventListener('click', () => approve(card));
  card.querySelector('.btn-reject').addEventListener('click', () => reject(card));
  msgInner.appendChild(card); scrollBot();
}
function addResultCard(r) {
  const ok = r.succeeded;
  const card = mk('div',`result-card ${ok?'ok':'fail'}`);
  const steps = r.steps || [];
  const vr = r.verification;
  const vrHtml = vr ? (() => {
    const st = vr.status || '';
    const stCls = st === 'SUCCESS' ? 'rs-ok' : (st === 'FAILED' ? 'rs-fail' : 'rs-warn');
    const conf = vr.confidence != null ? ` · confidence ${Math.round(vr.confidence*100)}%` : '';
    const risk = vr.risk ? ` · risk ${vr.risk}` : '';
    const tests = vr.tests_run ? ` · ${vr.tests_passed}/${vr.tests_run} tests passed` : '';
    const unexpected = vr.unexpected_files?.length ? `<div class="rs-item"><span class="rs-fail" aria-hidden="true">!</span><span class="rs-txt">Unexpected files: ${vr.unexpected_files.map(esc).join(', ')}</span></div>` : '';
    const changed = vr.changed_files?.length ? `<div class="rs-item"><span class="rs-ok" aria-hidden="true">↑</span><span class="rs-txt">Changed: ${vr.changed_files.map(esc).join(', ')}</span></div>` : '';
    const evidence = (vr.evidence?.length && st !== 'SUCCESS') ? vr.evidence.slice(0,3).map(e => `<div class="rs-item"><span class="rs-warn" aria-hidden="true">·</span><span class="rs-txt" style="color:var(--tx2)">${esc(String(e))}</span></div>`).join('') : '';
    return `<div class="rs-item" style="border-top:1px solid var(--b1);margin-top:6px;padding-top:6px">
      <span class="${stCls}" aria-hidden="true">⬡</span>
      <span class="rs-txt">Verification: ${esc(st)}${conf}${risk}${tests}</span>
    </div>${changed}${unexpected}${evidence}`;
  })() : '';
  // Reflection — the agent's own judgement of whether the task actually
  // completed, based on verification evidence rather than tool success.
  const rf = r.reflection;
  const rfHtml = rf ? (() => {
    const st = rf.status || '';
    const stCls = st === 'complete' ? 'rs-ok' : (st === 'blocked' ? 'rs-fail' : 'rs-warn');
    const conf = rf.confidence != null ? ` · confidence ${Math.round(rf.confidence*100)}%` : '';
    const reason = rf.reason ? `<div class="rs-item"><span class="rs-warn" aria-hidden="true">·</span><span class="rs-txt" style="color:var(--tx2)">${esc(rf.reason)}</span></div>` : '';
    const missing = rf.missing_requirements?.length
      ? `<div class="rs-item"><span class="rs-warn" aria-hidden="true">!</span><span class="rs-txt">Still needed: ${rf.missing_requirements.map(esc).join(', ')}</span></div>`
      : '';
    return `<div class="rs-item" style="border-top:1px solid var(--b1);margin-top:6px;padding-top:6px">
      <span class="${stCls}" aria-hidden="true">◈</span>
      <span class="rs-txt">Reflection: ${esc(st)}${conf}</span>
    </div>${reason}${missing}`;
  })() : '';
  const replanHtml = r.replans_used
    ? `<div class="rs-item"><span class="rs-warn" aria-hidden="true">↻</span><span class="rs-txt">Replanned ${r.replans_used} time${r.replans_used!==1?'s':''}</span></div>`
    : '';
  card.innerHTML = `
    <div class="result-hd">
      <span aria-hidden="true">${ok?'✓':'✕'}</span>
      <span>${ok?'Completed':'Failed'} — ${steps.length} step${steps.length!==1?'s':''}</span>
      <span style="margin-left:auto;font-size:11px;color:var(--tx3)">${esc(r.stop_reason||'')}</span>
    </div>
    <div class="result-steps">${steps.map(s=>`
      <div class="rs-item">
        <span class="${s.succeeded?'rs-ok':'rs-fail'}" aria-hidden="true">${s.succeeded?'✓':'✕'}</span>
        <span class="rs-txt">${esc(toolLabel(s.tool_name))} — ${esc(s.summary||'')}</span>
      </div>`).join('')}${replanHtml}${vrHtml}${rfHtml}
    </div>`;
  msgInner.appendChild(card); scrollBot();
}
export function addErr(msg) {
  const el = mk('div','err-note'); el.setAttribute('role','alert');
  el.innerHTML = `<span aria-hidden="true">⚠</span><span>${esc(msg)}</span>`;
  msgInner.appendChild(el); scrollBot();
}
export function addInfo(msg) {
  const el = mk('div','info-note');
  el.textContent = msg;
  msgInner.appendChild(el); scrollBot();
}
function scrollBot() { const m = g('messages'); m.scrollTop = m.scrollHeight; }

// ─── Sidebar & storage ────────────────────────────────────────
// Sessions are created/renamed/deleted on the server when available.
// localStorage stores message content (server doesn't have it).
// Hybrid: server is source of truth for session existence; local for messages.

export async function loadStore() {
  // Load local message data
  let localMap = {};
  try {
    const local = JSON.parse(localStorage.getItem(STORE_KEY) || '[]');
    local.forEach(c => { localMap[c.id] = c; });
  } catch {}

  // Try to merge server sessions in
  try {
    const data = await get('/api/sessions');
    const srv = (data.sessions || []).map(s => {
      const local = localMap[s.session_id] || {};
      return {
        id: s.session_id,
        title: s.title || local.title || 'New conversation',
        ts: s.created_at ? new Date(s.created_at).getTime() : (local.ts || Date.now()),
        msgs: local.msgs || [],
        _server: true,
      };
    });
    const srvIds = new Set(srv.map(c => c.id));
    // Keep local-only conversations (not yet synced) too
    const localOnly = Object.values(localMap).filter(c => !srvIds.has(c.id));
    S.convs = [...srv, ...localOnly].sort((a, b) => b.ts - a.ts);
  } catch {
    // Server unavailable — fall back to local only
    S.convs = Object.values(localMap).sort((a, b) => b.ts - a.ts);
  }
}
function save() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(S.convs.slice(-MAX_CONVS))); } catch {}
}
function ensureConv() { if (!S.id) newChat(false); }
export async function newChat(render = true) {
  const id = 'c' + Date.now();
  const conv = { id, title: 'New conversation', ts: Date.now(), msgs: [] };
  S.convs.unshift(conv);
  S.id = id; save();
  // Register on server (best-effort, fire-and-forget)
  try {
    const r = await post('/api/sessions', {});
    if (r.session_id && r.session_id !== id) {
      // Server gave us a different ID — update
      conv.id = r.session_id;
      if (S.id === id) S.id = r.session_id;
      save();
    }
  } catch { /* server unavailable, keep local-only conv */ }
  if (render) { clearMsgs(); chatTtl.textContent = 'Pearl AI'; }
  renderSidebar();
}
function clearMsgs() {
  while (msgInner.firstChild) msgInner.removeChild(msgInner.firstChild);
  msgInner.appendChild(emptySt); emptySt.style.display = '';
}
function saveMsg(role, content) {
  const c = S.convs.find(x => x.id === S.id); if (!c) return;
  if (role === 'user' && c.title === 'New conversation' && content.length > 2) {
    c.title = content.slice(0,54) + (content.length > 54 ? '…' : '');
    chatTtl.textContent = c.title;
    renderSidebar();
    // Sync auto-generated title to server (best-effort)
    try { fetch(`/api/sessions/${c.id}`, { method: 'PATCH', headers: {'Content-Type':'application/json'}, body: JSON.stringify({title: c.title}) }); } catch {}
  }
  c.msgs.push({ role, content, ts: Date.now() }); save();
}
function loadConv(id) {
  const c = S.convs.find(x => x.id === id); if (!c) return;
  S.id = id; chatTtl.textContent = c.title; clearMsgs();
  c.msgs.filter(m => m.role === 'user' || m.role === 'assistant').forEach(m => {
    if (m.role === 'user') {
      const r = mk('div','msg-row msg-user');
      r.innerHTML = `<div class="user-bub">${esc(m.content)}</div>`;
      msgInner.appendChild(r);
    } else {
      const r = mk('div','msg-row msg-pearl');
      const rawMd = m.content;
      r.innerHTML = `
        <div class="pearl-label"><span class="pearl-dot" aria-hidden="true"></span>Pearl</div>
        <div class="pearl-bub md">${renderMd(rawMd)}</div>
        <div class="pearl-acts">
          <button class="pa-btn pa-copy" aria-label="Copy response">
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><rect x="5" y="5" width="9" height="9" rx="1.5"/><path d="M4 11H3a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1h7a1 1 0 0 1 1 1v1"/></svg>
            Copy
          </button>
        </div>
        <div class="msg-meta">${tsStr(m.ts)}</div>`;
      r.querySelector('.pa-copy').addEventListener('click', e => {
        const btn = e.currentTarget;
        navigator.clipboard.writeText(rawMd).then(() => {
          btn.classList.add('copied'); btn.childNodes[btn.childNodes.length-1].textContent = ' Copied!';
          setTimeout(() => { btn.classList.remove('copied'); btn.childNodes[btn.childNodes.length-1].textContent = ' Copy'; }, 2000);
        }).catch(() => {});
      });
      msgInner.appendChild(r);
    }
  });
  if (!msgInner.firstChild || msgInner.firstChild === emptySt) {
    if (!emptySt.parentNode) msgInner.appendChild(emptySt);
  } else if (emptySt.parentNode) emptySt.remove();
  renderSidebar(); scrollBot();
}
export function renderSidebar(filter = '') {
  sbList.innerHTML = '';
  const groups = [['Today',0,86400000],['Yesterday',86400000,172800000],['Last 7 days',172800000,604800000],['Older',604800000,Infinity]];
  const now = Date.now();
  groups.forEach(([label, lo, hi]) => {
    const items = S.convs.filter(c => {
      const age = now - c.ts;
      return age >= lo && age < hi && (!filter || c.title.toLowerCase().includes(filter));
    });
    if (!items.length) return;
    const gl = mk('div','sb-group-label'); gl.textContent = label; sbList.appendChild(gl);
    items.forEach(conv => {
      const item = mk('div',`sb-item${conv.id === S.id ? ' active' : ''}`);
      item.setAttribute('role','button'); item.setAttribute('tabindex','0'); item.setAttribute('aria-label', conv.title);
      item.innerHTML = `
        <span class="sb-item-title">${esc(conv.title)}</span>
        <span class="sb-item-ts">${relTs(conv.ts)}</span>
        <span class="sb-item-actions">
          <button class="sb-act rename" aria-label="Rename conversation" title="Rename">✎</button>
          <button class="sb-act del" aria-label="Delete conversation" title="Delete">✕</button>
        </span>`;
      item.addEventListener('click', e => { if (!e.target.closest('.sb-act')) loadConv(conv.id); });
      item.addEventListener('keydown', e => { if (e.key === 'Enter') loadConv(conv.id); });
      item.querySelector('.rename').addEventListener('click', async e => {
        e.stopPropagation();
        const t = prompt('Rename:', conv.title);
        if (!t?.trim()) return;
        conv.title = t.trim(); save(); renderSidebar(filter);
        // Sync rename to server (best-effort)
        try { await fetch(`/api/sessions/${conv.id}`, { method: 'PATCH', headers: {'Content-Type':'application/json'}, body: JSON.stringify({title: conv.title}) }); } catch {}
      });
      item.querySelector('.del').addEventListener('click', async e => {
        e.stopPropagation();
        if (!confirm('Delete this conversation?')) return;
        const delId = conv.id;
        S.convs = S.convs.filter(x => x.id !== delId);
        if (S.id === delId) { S.id = null; clearMsgs(); chatTtl.textContent = 'Pearl AI'; }
        save(); renderSidebar(filter);
        // Sync delete to server (best-effort)
        try { await fetch(`/api/sessions/${delId}`, { method: 'DELETE' }); } catch {}
      });
      sbList.appendChild(item);
    });
  });
}
