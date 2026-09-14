export function riskLabel(value: string): string {
  if (value === "high") {
    return "高风险";
  }
  if (value === "medium") {
    return "中风险";
  }
  return "低风险";
}

export function compatibilityLabel(value: string): string {
  if (value === "native") {
    return "原生可执行";
  }
  if (value === "document_only") {
    return "仅文档";
  }
  if (value === "compatible") {
    return "兼容执行";
  }
  if (value === "unsupported") {
    return "暂不支持";
  }
  return value || "未知";
}

export function mcpStatus(value: string) {
  if (value === "connected") {
    return { label: "已连接", className: "border-success/25 bg-success/10 text-success" };
  }
  if (value === "connecting") {
    return { label: "连接中", className: "border-warning/25 bg-warning/10 text-warning" };
  }
  if (value === "error") {
    return { label: "连接异常", className: "border-destructive/25 bg-destructive/10 text-destructive" };
  }
  return { label: "未连接", className: "border-border bg-muted text-muted-foreground" };
}

export function compactCapabilityText(value: string, maxLength = 180): string {
  const text = value.replace(/\s+/g, " ").trim();
  return text.length > maxLength ? `${text.slice(0, maxLength)}…` : text;
}

export function formatFileSize(value: number): string {
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

export function parseLines(value: string): string[] {
  return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}

export function parseHeaderMap(value: string): Record<string, string> {
  return Object.fromEntries(
    parseLines(value)
      .map((line) => {
        const index = line.indexOf("=");
        return index > 0 ? [line.slice(0, index).trim(), line.slice(index + 1).trim()] : null;
      })
      .filter((item): item is [string, string] => Boolean(item?.[0] && item?.[1])),
  );
}
