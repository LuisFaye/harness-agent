export function downloadMarkdownResult(content, baseName) {
  const safeName = String(baseName || "agent-result").replace(/[^a-zA-Z0-9_-]+/g, "-").slice(0, 80) || "agent-result";
  const blob = new Blob([String(content || "")], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${safeName}.md`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
