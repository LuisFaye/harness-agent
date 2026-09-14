const SAFE_LINK_PROTOCOLS = new Set(["http:", "https:", "mailto:"]);

export function renderMarkdown(source) {
  const lines = String(source || "").replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }

    const fence = line.match(/^\s*```([^`]*)$/);
    if (fence) {
      const code = [];
      index += 1;
      while (index < lines.length && !/^\s*```\s*$/.test(lines[index])) {
        code.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      const language = fence[1].trim().split(/\s+/)[0] || "";
      const languageClass = language ? ` class="language-${escapeAttribute(language)}"` : "";
      blocks.push(`<pre><code${languageClass}>${escapeHtml(code.join("\n"))}</code></pre>`);
      continue;
    }

    const heading = line.match(/^\s*(#{1,6})\s+(.+?)\s*#*\s*$/);
    if (heading) {
      const level = Math.min(6, heading[1].length + 1);
      blocks.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
      index += 1;
      continue;
    }

    if (/^\s{0,3}((\*\s*){3,}|(-\s*){3,}|(_\s*){3,})$/.test(line)) {
      blocks.push("<hr>");
      index += 1;
      continue;
    }

    if (/^\s*>/.test(line)) {
      const quoteLines = [];
      while (index < lines.length && /^\s*>/.test(lines[index])) {
        quoteLines.push(lines[index].replace(/^\s*>\s?/, ""));
        index += 1;
      }
      blocks.push(`<blockquote>${renderMarkdown(quoteLines.join("\n"))}</blockquote>`);
      continue;
    }

    if (isTableStart(lines, index)) {
      const headers = splitTableRow(lines[index]);
      const alignments = splitTableRow(lines[index + 1]).map(tableAlignment);
      index += 2;
      const rows = [];
      while (index < lines.length && isTableRow(lines[index])) {
        rows.push(splitTableRow(lines[index]));
        index += 1;
      }
      blocks.push(renderTable(headers, alignments, rows));
      continue;
    }

    const listMatch = matchListItem(line);
    if (listMatch) {
      const ordered = listMatch.ordered;
      const items = [];
      while (index < lines.length) {
        const item = matchListItem(lines[index]);
        if (!item || item.ordered !== ordered) break;
        items.push(item.content);
        index += 1;
      }
      const tag = ordered ? "ol" : "ul";
      blocks.push(`<${tag}>${items.map(renderListItem).join("")}</${tag}>`);
      continue;
    }

    const paragraph = [line.trim()];
    index += 1;
    while (index < lines.length && lines[index].trim() && !startsBlock(lines, index)) {
      paragraph.push(lines[index].trim());
      index += 1;
    }
    blocks.push(`<p>${paragraph.map(renderInline).join("<br>")}</p>`);
  }

  return blocks.join("");
}

function startsBlock(lines, index) {
  const line = lines[index];
  return /^\s*```/.test(line)
    || /^\s*#{1,6}\s+/.test(line)
    || /^\s*>/.test(line)
    || Boolean(matchListItem(line))
    || isTableStart(lines, index)
    || /^\s{0,3}((\*\s*){3,}|(-\s*){3,}|(_\s*){3,})$/.test(line);
}

function matchListItem(line) {
  const match = String(line || "").match(/^\s*(?:(\d+)[.)]|[-+*])\s+(.+)$/);
  if (!match) return null;
  return { ordered: Boolean(match[1]), content: match[2] };
}

function renderListItem(content) {
  const task = content.match(/^\[([ xX])\]\s+(.+)$/);
  if (!task) return `<li>${renderInline(content)}</li>`;
  const checked = task[1].toLowerCase() === "x";
  return `<li class="task-list-item"><input type="checkbox" disabled${checked ? " checked" : ""}>${renderInline(task[2])}</li>`;
}

function isTableStart(lines, index) {
  if (index + 1 >= lines.length || !isTableRow(lines[index])) return false;
  const separators = splitTableRow(lines[index + 1]);
  return separators.length > 0 && separators.every((cell) => /^:?-{3,}:?$/.test(cell.trim()));
}

function isTableRow(line) {
  return String(line || "").includes("|") && String(line || "").trim() !== "|";
}

function splitTableRow(line) {
  let value = String(line || "").trim();
  if (value.startsWith("|")) value = value.slice(1);
  if (value.endsWith("|") && !value.endsWith("\\|")) value = value.slice(0, -1);
  const cells = [];
  let cell = "";
  let escaped = false;
  for (const character of value) {
    if (escaped) {
      cell += character;
      escaped = false;
    } else if (character === "\\") {
      escaped = true;
    } else if (character === "|") {
      cells.push(cell.trim());
      cell = "";
    } else {
      cell += character;
    }
  }
  if (escaped) cell += "\\";
  cells.push(cell.trim());
  return cells;
}

function tableAlignment(separator) {
  const value = separator.trim();
  if (value.startsWith(":") && value.endsWith(":")) return "center";
  if (value.endsWith(":")) return "right";
  return "left";
}

function renderTable(headers, alignments, rows) {
  const head = headers.map((cell, index) => (
    `<th style="text-align:${alignments[index] || "left"}">${renderInline(cell)}</th>`
  )).join("");
  const body = rows.map((row) => `<tr>${headers.map((_, index) => (
    `<td style="text-align:${alignments[index] || "left"}">${renderInline(row[index] || "")}</td>`
  )).join("")}</tr>`).join("");
  return `<div class="markdown-table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function renderInline(source) {
  const tokens = [];
  const stash = (html) => {
    const marker = `\u0000${tokens.length}\u0000`;
    tokens.push(html);
    return marker;
  };
  let value = String(source || "");
  value = value.replace(/`([^`\n]+)`/g, (_, code) => stash(`<code>${escapeHtml(code)}</code>`));
  value = value.replace(/\[([^\]\n]+)\]\(([^)\s]+)(?:\s+["'][^"']*["'])?\)/g, (match, label, href) => {
    const safeHref = sanitizeLink(href);
    if (!safeHref) return match;
    const external = /^https?:/i.test(safeHref);
    const attributes = external ? " target=\"_blank\" rel=\"noopener noreferrer\"" : "";
    return stash(`<a href="${escapeAttribute(safeHref)}"${attributes}>${renderInline(label)}</a>`);
  });

  let html = escapeHtml(value);
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/__(.+?)__/g, "<strong>$1</strong>");
  html = html.replace(/~~(.+?)~~/g, "<del>$1</del>");
  html = html.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  html = html.replace(/(^|[^_])_([^_\n]+)_/g, "$1<em>$2</em>");
  return html.replace(/\u0000(\d+)\u0000/g, (_, tokenIndex) => tokens[Number(tokenIndex)] || "");
}

function sanitizeLink(href) {
  const value = String(href || "").trim();
  if (!value) return "";
  if (value.startsWith("#") || value.startsWith("/") || value.startsWith("./") || value.startsWith("../")) {
    return value;
  }
  try {
    const parsed = new URL(value);
    return SAFE_LINK_PROTOCOLS.has(parsed.protocol) ? value : "";
  } catch {
    return "";
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttribute(value) {
  return escapeHtml(value).replaceAll("`", "&#096;");
}
