/**
 * A small, self-contained Markdown-to-HTML renderer for chat
 * messages: fenced code blocks, inline code, bold/italic, ordered
 * and unordered lists, tables, and paragraphs.
 *
 * Deliberately not a dependency on an external Markdown library —
 * this keeps the extension free of new runtime dependencies and
 * avoids any webview CSP/network concerns. All user/model text is
 * HTML-escaped before any markup is applied, so the output is safe
 * to insert via `innerHTML` in the webview.
 *
 * Pure (no `vscode` dependency), so directly unit testable.
 */

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderInline(rawLine: string): string {
  // Split out inline code spans first so their contents are never
  // treated as further markup (bold/italic) and are escaped as raw
  // text, not interpreted.
  const parts = rawLine.split(/(`[^`]+`)/g);

  return parts
    .map((part) => {
      if (part.length >= 2 && part.startsWith("`") && part.endsWith("`")) {
        return `<code>${escapeHtml(part.slice(1, -1))}</code>`;
      }

      let escaped = escapeHtml(part);
      escaped = escaped.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
      escaped = escaped.replace(/\*([^*]+)\*/g, "<em>$1</em>");
      return escaped;
    })
    .join("");
}

function splitTableRow(row: string): string[] {
  return row
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

const FENCE_RE = /^```(\w*)\s*$/;
const TABLE_SEPARATOR_RE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
const UNORDERED_ITEM_RE = /^\s*[-*]\s+/;
const ORDERED_ITEM_RE = /^\s*\d+\.\s+/;

export function renderMarkdownToHtml(text: string): string {
  const lines = text.split("\n");
  const html: string[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    const fenceMatch = line.match(FENCE_RE);

    if (fenceMatch) {
      const lang = fenceMatch[1];
      const codeLines: string[] = [];
      i += 1;

      while (i < lines.length && !/^```\s*$/.test(lines[i])) {
        codeLines.push(lines[i]);
        i += 1;
      }

      i += 1; // skip the closing fence, if present

      const codeHtml = escapeHtml(codeLines.join("\n"));
      const langClass = lang ? ` class="language-${escapeHtml(lang)}"` : "";
      html.push(`<pre><code${langClass}>${codeHtml}</code></pre>`);
      continue;
    }

    if (
      line.includes("|") &&
      i + 1 < lines.length &&
      TABLE_SEPARATOR_RE.test(lines[i + 1])
    ) {
      const headerCells = splitTableRow(line);
      i += 2;

      const bodyRows: string[][] = [];
      while (i < lines.length && lines[i].includes("|")) {
        bodyRows.push(splitTableRow(lines[i]));
        i += 1;
      }

      const thead = `<thead><tr>${headerCells
        .map((cell) => `<th>${renderInline(cell)}</th>`)
        .join("")}</tr></thead>`;
      const tbody = `<tbody>${bodyRows
        .map(
          (row) =>
            `<tr>${row
              .map((cell) => `<td>${renderInline(cell)}</td>`)
              .join("")}</tr>`
        )
        .join("")}</tbody>`;

      html.push(`<table>${thead}${tbody}</table>`);
      continue;
    }

    if (UNORDERED_ITEM_RE.test(line)) {
      const items: string[] = [];
      while (i < lines.length && UNORDERED_ITEM_RE.test(lines[i])) {
        items.push(lines[i].replace(UNORDERED_ITEM_RE, ""));
        i += 1;
      }
      html.push(
        `<ul>${items.map((item) => `<li>${renderInline(item)}</li>`).join("")}</ul>`
      );
      continue;
    }

    if (ORDERED_ITEM_RE.test(line)) {
      const items: string[] = [];
      while (i < lines.length && ORDERED_ITEM_RE.test(lines[i])) {
        items.push(lines[i].replace(ORDERED_ITEM_RE, ""));
        i += 1;
      }
      html.push(
        `<ol>${items.map((item) => `<li>${renderInline(item)}</li>`).join("")}</ol>`
      );
      continue;
    }

    if (line.trim() === "") {
      i += 1;
      continue;
    }

    const paragraphLines: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !FENCE_RE.test(lines[i]) &&
      !UNORDERED_ITEM_RE.test(lines[i]) &&
      !ORDERED_ITEM_RE.test(lines[i])
    ) {
      paragraphLines.push(lines[i]);
      i += 1;
    }

    html.push(`<p>${paragraphLines.map(renderInline).join("<br>")}</p>`);
  }

  return html.join("\n");
}
