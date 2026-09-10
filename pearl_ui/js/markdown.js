// Markdown rendering and syntax highlighting for assistant messages.

import { escapeHtml as esc, escapeText as he } from "./format.js";

export function renderMd(text) {
  if (!text) return "";
  const blocks = [], inlines = [];
  // Extract fenced code blocks first
  let s = text.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
    const i = blocks.length; blocks.push({lang, code}); return `\x00B${i}\x00`;
  });
  // Inline code
  s = s.replace(/`([^`\n]+)`/g, (_, c) => { const i = inlines.length; inlines.push(c); return `\x00I${i}\x00`; });
  // HTML escape
  s = he(s);
  // Block-level
  s = s.replace(/^#{4} (.+)$/gm,'<h4>$1</h4>');
  s = s.replace(/^#{3} (.+)$/gm,'<h3>$1</h3>');
  s = s.replace(/^#{2} (.+)$/gm,'<h2>$1</h2>');
  s = s.replace(/^#{1} (.+)$/gm,'<h1>$1</h1>');
  s = s.replace(/^[-*]{3,}$/gm,'<hr>');
  s = s.replace(/^&gt; (.+)$/gm,'<blockquote>$1</blockquote>');
  // Lists
  s = s.replace(/^[*\-+] (.+)$/gm,'<li>$1</li>');
  s = s.replace(/((?:^<li>.*<\/li>\n?)+)/gm, m => `<ul>${m}</ul>`);
  s = s.replace(/^\d+\. (.+)$/gm,'<XLI>$1</XLI>');
  s = s.replace(/((?:^<XLI>.*<\/XLI>\n?)+)/gm, m => `<ol>${m.replace(/<XLI>/g,'<li>').replace(/<\/XLI>/g,'</li>')}</ol>`);
  // Inline markup
  s = s.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
  s = s.replace(/__(.+?)__/g,'<strong>$1</strong>');
  s = s.replace(/\*([^*\n]+)\*/g,'<em>$1</em>');
  s = s.replace(/~~(.+?)~~/g,'<del>$1</del>');
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g,'<a href="$2" target="_blank" rel="noopener">$1</a>');
  // Paragraphs
  const paras = s.split(/\n{2,}/);
  s = paras.map(p => {
    const t = p.trim();
    if (!t || /^<(h[1-4]|ul|ol|blockquote|hr|table)/.test(t)) return t;
    return `<p>${t.replace(/\n/g,'<br>')}</p>`;
  }).join('\n');
  // Restore inlines
  s = s.replace(/\x00I(\d+)\x00/g, (_,i) => `<code>${he(inlines[+i])}</code>`);
  // Restore blocks
  s = s.replace(/\x00B(\d+)\x00/g, (_,i) => {
    const {lang, code} = blocks[+i];
    const hl = hilight(code, lang);
    return `<div class="cbw"><div class="cbh"><span class="cb-lang">${esc(lang||'text')}</span><button class="copy-btn" onclick="doCopy(this,${JSON.stringify(code)})">Copy</button></div><pre><code>${hl}</code></pre></div>`;
  });
  return s;
}

// Called from the inline onclick above, so main.js publishes it on
// window. Rewriting that to a delegated listener would change generated
// markup, which the front-end split is deliberately not doing.
export function doCopy(btn, code) {
  navigator.clipboard.writeText(code).then(() => {
    btn.textContent = 'Copied!'; btn.classList.add('done');
    setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('done'); }, 2000);
  }).catch(() => { btn.textContent = 'Failed'; setTimeout(() => btn.textContent = 'Copy', 1500); });
}

const KW = {
  python: new Set('def class import from return if elif else for while with try except finally raise pass break continue and or not in is lambda yield async await True False None global nonlocal del assert as'.split(' ')),
  javascript: new Set('function class const let var return if else for while do switch case break continue new this super import export default async await typeof instanceof in of try catch finally throw null undefined true false void delete yield from extends static'.split(' ')),
  typescript: new Set('function class const let var return if else for while do switch case break continue new this super import export default async await typeof instanceof in of try catch finally throw null undefined true false void delete yield from extends static interface type enum namespace abstract implements declare readonly private public protected as'.split(' ')),
  rust: new Set('fn let mut const struct enum impl trait use mod pub return if else match for while loop break continue true false self super crate where type async await move ref in as'.split(' ')),
  go: new Set('func var const type struct interface package import return if else for range switch case break continue go chan select defer map make new nil true false iota fallthrough goto'.split(' ')),
  bash: new Set('if then else elif fi for while do done case esac function return in echo export local readonly shift set unset trap exit source'.split(' ')),
};

export function hilight(code, lang) {
  if (!lang || lang === 'text' || lang === 'plain') return he(code);
  if (lang === 'diff') return hDiff(code);
  if (lang === 'json') return hJson(code);
  const kws = KW[lang] || KW.javascript;
  let out = '', i = 0, n = code.length;
  while (i < n) {
    // Comments
    if ((lang==='python'||lang==='bash') && code[i]==='#') {
      let j=i; while (j<n && code[j]!=='\n') j++;
      out += `<span class="hl-cmt">${he(code.slice(i,j))}</span>`; i=j; continue;
    }
    if ((lang==='javascript'||lang==='typescript'||lang==='rust'||lang==='go') && code.slice(i,i+2)==='//') {
      let j=i; while (j<n && code[j]!=='\n') j++;
      out += `<span class="hl-cmt">${he(code.slice(i,j))}</span>`; i=j; continue;
    }
    if ((lang==='javascript'||lang==='typescript'||lang==='rust'||lang==='go') && code.slice(i,i+2)==='/*') {
      const end = code.indexOf('*/',i+2); const j = end===-1?n:end+2;
      out += `<span class="hl-cmt">${he(code.slice(i,j))}</span>`; i=j; continue;
    }
    // Triple string Python
    if (lang==='python' && (code.slice(i,i+3)==='"""'||code.slice(i,i+3)==="'''")) {
      const q=code.slice(i,i+3), end=code.indexOf(q,i+3), j=end===-1?n:end+3;
      out += `<span class="hl-str">${he(code.slice(i,j))}</span>`; i=j; continue;
    }
    // Strings
    if (code[i]==='"'||code[i]==="'"||code[i]==='`') {
      const q=code[i]; let j=i+1;
      while (j<n) { if (code[j]==='\\'){j+=2;continue} if (code[j]===q){j++;break} if (q!=='`'&&code[j]==='\n') break; j++; }
      out += `<span class="hl-str">${he(code.slice(i,j))}</span>`; i=j; continue;
    }
    // Numbers
    if (/\d/.test(code[i])&&(i===0||/\W/.test(code[i-1]))) {
      let j=i; while (j<n&&/[\d.xXa-fA-F_]/.test(code[j])) j++;
      out += `<span class="hl-num">${he(code.slice(i,j))}</span>`; i=j; continue;
    }
    // Identifiers
    if (/[a-zA-Z_$]/.test(code[i])) {
      let j=i; while (j<n&&/\w/.test(code[j])) j++;
      const w = code.slice(i,j);
      const prev = out.replace(/<[^>]*>/g,'').trimEnd();
      const prevWord = prev.split(/\s+/).pop()||'';
      if (prevWord==='class') { out += `<span class="hl-cls">${he(w)}</span>`; i=j; continue; }
      if (['def','function','fn','func'].includes(prevWord)) { out += `<span class="hl-fn">${he(w)}</span>`; i=j; continue; }
      if (lang==='python'&&i>0&&code[i-1]==='@') { out += `<span class="hl-dec">${he(w)}</span>`; i=j; continue; }
      out += kws.has(w) ? `<span class="hl-kw">${he(w)}</span>` : he(w);
      i=j; continue;
    }
    if (code[i]==='@'&&lang==='python') { out += `<span class="hl-dec">@</span>`; i++; continue; }
    out += he(code[i]); i++;
  }
  return out;
}

export function hDiff(code) {
  return code.split('\n').map(l => {
    if (l.startsWith('+')) return `<span class="hl-str">${he(l)}</span>`;
    if (l.startsWith('-')) return `<span class="hl-num">${he(l)}</span>`;
    if (l.startsWith('@@')) return `<span class="hl-kw">${he(l)}</span>`;
    return he(l);
  }).join('\n');
}

export function hJson(code) {
  return he(code)
    .replace(/(&quot;[^&]*&quot;)(\s*:)/g, '<span class="hl-fn">$1</span>$2')
    .replace(/:\s*(&quot;[^&]*&quot;)/g, (m,v) => m.replace(v, `<span class="hl-str">${v}</span>`))
    .replace(/\b(true|false|null)\b/g, '<span class="hl-kw">$1</span>')
    .replace(/\b(\d+\.?\d*)\b/g, '<span class="hl-num">$1</span>');
}
