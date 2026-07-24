import assert from "node:assert/strict";
import { test } from "node:test";
import { renderMarkdownToHtml } from "../chat/markdown";

test("renders a plain paragraph", () => {
  const html = renderMarkdownToHtml("Hello world");

  assert.equal(html, "<p>Hello world</p>");
});

test("renders bold and italic text", () => {
  const html = renderMarkdownToHtml("**bold** and *italic*");

  assert.match(html, /<strong>bold<\/strong>/);
  assert.match(html, /<em>italic<\/em>/);
});

test("renders inline code without applying bold/italic inside it", () => {
  const html = renderMarkdownToHtml("Use `**not bold**` here");

  assert.match(html, /<code>\*\*not bold\*\*<\/code>/);
  assert.doesNotMatch(html, /<strong>/);
});

test("renders a fenced code block with a language class", () => {
  const html = renderMarkdownToHtml("```python\nprint('hi')\n```");

  assert.match(html, /<pre><code class="language-python">/);
  assert.match(html, /print\(&#39;hi&#39;\)/);
});

test("renders a fenced code block with no language", () => {
  const html = renderMarkdownToHtml("```\nplain code\n```");

  assert.match(html, /<pre><code>plain code<\/code><\/pre>/);
});

test("renders an unordered list", () => {
  const html = renderMarkdownToHtml("- one\n- two\n- three");

  assert.equal(
    html,
    "<ul><li>one</li><li>two</li><li>three</li></ul>"
  );
});

test("renders an ordered list", () => {
  const html = renderMarkdownToHtml("1. first\n2. second");

  assert.equal(html, "<ol><li>first</li><li>second</li></ol>");
});

test("renders a table with a header and body rows", () => {
  const html = renderMarkdownToHtml(
    "| Name | Age |\n| --- | --- |\n| Alice | 30 |\n| Bob | 25 |"
  );

  assert.match(html, /<table>/);
  assert.match(html, /<th>Name<\/th><th>Age<\/th>/);
  assert.match(html, /<td>Alice<\/td><td>30<\/td>/);
  assert.match(html, /<td>Bob<\/td><td>25<\/td>/);
});

test("escapes HTML in plain text to prevent injection", () => {
  const html = renderMarkdownToHtml("<script>alert(1)</script>");

  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;script&gt;/);
});

test("escapes HTML inside inline code and code blocks too", () => {
  const inline = renderMarkdownToHtml("`<b>not bold</b>`");
  assert.match(inline, /&lt;b&gt;not bold&lt;\/b&gt;/);

  const block = renderMarkdownToHtml("```\n<b>raw</b>\n```");
  assert.match(block, /&lt;b&gt;raw&lt;\/b&gt;/);
});

test("joins multiple paragraph lines with <br>", () => {
  const html = renderMarkdownToHtml("line one\nline two");

  assert.equal(html, "<p>line one<br>line two</p>");
});

test("handles mixed content: paragraph, list, and code block in one message", () => {
  const html = renderMarkdownToHtml(
    "Here is a summary:\n\n- point one\n- point two\n\n```js\nconsole.log(1);\n```"
  );

  assert.match(html, /<p>Here is a summary:<\/p>/);
  assert.match(html, /<ul><li>point one<\/li><li>point two<\/li><\/ul>/);
  assert.match(html, /<pre><code class="language-js">/);
});
