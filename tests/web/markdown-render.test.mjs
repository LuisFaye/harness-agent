import assert from "node:assert/strict";
import test from "node:test";

import { renderMarkdown } from "../../src/daily_agent/web/app/markdown.js";

test("常用 Markdown 块级格式会正确渲染", () => {
  const html = renderMarkdown(`# 标题

- 列表一
- 列表二

> 引用内容

| 名称 | 状态 |
| --- | :---: |
| Agent | 完成 |

\`\`\`js
const ok = true;
\`\`\``);

  assert.match(html, /<h2>标题<\/h2>/);
  assert.match(html, /<ul><li>列表一<\/li><li>列表二<\/li><\/ul>/);
  assert.match(html, /<blockquote><p>引用内容<\/p><\/blockquote>/);
  assert.match(html, /<table>/);
  assert.match(html, /text-align:center/);
  assert.match(html, /<pre><code class="language-js">const ok = true;<\/code><\/pre>/);
});

test("行内格式和安全链接会正确渲染", () => {
  const html = renderMarkdown("**粗体**、*斜体*、~~删除~~、`code`、[官网](https://example.com)");

  assert.match(html, /<strong>粗体<\/strong>/);
  assert.match(html, /<em>斜体<\/em>/);
  assert.match(html, /<del>删除<\/del>/);
  assert.match(html, /<code>code<\/code>/);
  assert.match(html, /href="https:\/\/example.com"/);
});

test("HTML 和危险链接不会进入消息 DOM", () => {
  const html = renderMarkdown('<script>alert(1)</script> [危险](javascript:alert(1))');

  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;script&gt;/);
  assert.doesNotMatch(html, /href="javascript:/);
});
