// Code mode: a scratch editor with inline ghost-text completion.
//
// Mirrors the VS Code provider's behaviour against the same
// /api/complete endpoint: debounce a burst of keystrokes into one
// request, drop a response whose context is already stale, and stay
// silent on failure. A suggestion that lands after you have typed past
// it is worse than no suggestion, so late responses are discarded
// rather than rendered.

import { post } from "./api.js";
import { g, S } from "./state.js";

const CODE_DEBOUNCE_MS = 150;

const CODE = {
  timer: null,
  ghost: '',        // the suggestion currently shown
  seq: 0,           // request counter; only the newest may render
};

export function applyMode() {
  const isCode = S.mode === 'code';
  g('code-pane').hidden = !isCode;
  g('messages').hidden = isCode;
  g('composer').hidden = isCode;
  if (isCode) {
    g('code-input').focus();
    syncMirror();
  }
}

// Repaint the layer that actually shows text. The textarea's own text is
// transparent, so nothing is visible until this runs.
function syncMirror() {
  const input = g('code-input');
  g('code-typed').textContent = input.value;
  g('code-ghost').textContent = CODE.ghost;
}

function clearGhost() {
  if (!CODE.ghost) return;
  CODE.ghost = '';
  syncMirror();
  g('code-status').textContent = '';
}

// Only suggest at the very end of the buffer. Ghost text rendered
// mid-document would appear detached from the caret, since the mirror
// appends it rather than inserting at the cursor.
function atEnd(input) {
  return input.selectionStart === input.value.length &&
         input.selectionEnd === input.value.length;
}

function requestCompletion() {
  const input = g('code-input');
  const prefix = input.value;

  if (!prefix.trim() || !atEnd(input)) { clearGhost(); return; }

  const seq = ++CODE.seq;
  g('code-status').textContent = 'thinking…';

  post('/api/complete', {
    prefix,
    suffix: '',
    language: g('code-lang').value,
  }).then(res => {
    // A newer keystroke has already superseded this request, or the
    // buffer moved on while it was in flight.
    if (seq !== CODE.seq || input.value !== prefix) return;

    CODE.ghost = res.completion || '';
    syncMirror();
    g('code-status').textContent = CODE.ghost
      ? (res.cached ? 'cached' : 'suggestion ready')
      : (res.declined_reason || 'no suggestion');
  }).catch(() => {
    if (seq !== CODE.seq) return;
    clearGhost();
    // Silent by design: a failed completion must never interrupt typing.
    g('code-status').textContent = '';
  });
}

function scheduleCompletion() {
  clearTimeout(CODE.timer);
  clearGhost();
  CODE.timer = setTimeout(requestCompletion, CODE_DEBOUNCE_MS);
}

export function initCodeMode() {
  const input = g('code-input');
  const mirror = g('code-mirror');

  input.addEventListener('input', scheduleCompletion);
  // Moving the caret invalidates a suggestion computed for the old
  // position, but must not trigger a new request on its own.
  input.addEventListener('click', clearGhost);

  input.addEventListener('keydown', e => {
    if (e.key === 'Tab' && CODE.ghost) {
      e.preventDefault();
      input.value += CODE.ghost;
      clearGhost();
      syncMirror();
      input.selectionStart = input.selectionEnd = input.value.length;
      return;
    }
    if (e.key === 'Tab') {           // no suggestion: indent, don't tab away
      e.preventDefault();
      const at = input.selectionStart;
      input.value = input.value.slice(0, at) + '    ' + input.value.slice(at);
      input.selectionStart = input.selectionEnd = at + 4;
      syncMirror();
      return;
    }
    if (e.key === 'Escape' && CODE.ghost) { e.preventDefault(); clearGhost(); return; }
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight' ||
        e.key === 'ArrowUp' || e.key === 'ArrowDown') clearGhost();
  });

  // Keep the painted layer aligned while scrolling a long buffer.
  input.addEventListener('scroll', () => {
    mirror.scrollTop = input.scrollTop;
    mirror.scrollLeft = input.scrollLeft;
  });

  g('code-lang').addEventListener('change', () => { clearGhost(); input.focus(); });
  g('code-clear').addEventListener('click', () => {
    input.value = ''; clearGhost(); syncMirror(); input.focus();
  });
}
