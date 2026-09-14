import { Braces, Check, PlugZap, ShieldCheck } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import type { McpServerWritePayload } from "@/lib/api/management";

import { parseMcpImport, type McpImportItem } from "./mcp-import";

interface McpImportCardProps {
  existingNames: string[];
  onImport: (payloads: McpServerWritePayload[], connectAfterImport: boolean) => void;
  pending: boolean;
}

const example = `{
  "mcpServers": {
    "mysql-agent-front": {
      "command": "mysql_mcp_server_pro",
      "args": ["--mode", "stdio", "--envfile", "/path/to/.env", "--oauth", "false"]
    }
  }
}`;

export function McpImportCard({ existingNames, onImport, pending }: McpImportCardProps) {
  const [source, setSource] = useState("");
  const [items, setItems] = useState<McpImportItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [connectAfterImport, setConnectAfterImport] = useState(true);
  const existing = useMemo(() => new Set(existingNames), [existingNames]);
  const importable = items.filter((item) => !existing.has(item.name));

  function preview() {
    try {
      setItems(parseMcpImport(source));
      setError(null);
    } catch (reason) {
      setItems([]);
      setError(reason instanceof Error ? reason.message : "配置解析失败。");
    }
  }

  return (
    <section aria-labelledby="mcp-import-title" className="rounded-xl border bg-card p-5">
      <header className="flex items-start gap-3">
        <span aria-hidden="true" className="grid size-10 shrink-0 place-items-center rounded-lg bg-accent text-accent-foreground">
          <Braces className="size-5" />
        </span>
        <div>
          <h2 className="font-semibold" id="mcp-import-title">粘贴已有 MCP 配置</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            支持 Claude、Codex 常见的 <code>mcpServers</code> JSON，也可以只粘贴一个名称配置片段。
          </p>
        </div>
      </header>

      <label className="mt-5 block text-sm font-medium" htmlFor="mcp-import-source">
        MCP JSON
      </label>
      <textarea
        aria-describedby="mcp-import-help"
        aria-invalid={Boolean(error)}
        className="mt-1.5 min-h-52 w-full resize-y rounded-lg border bg-background px-3 py-3 font-mono text-base leading-6 outline-none focus:border-ring focus:ring-3 focus:ring-ring/20 sm:text-xs"
        id="mcp-import-source"
        onBlur={() => source.trim() && preview()}
        onChange={(event) => {
          setSource(event.target.value);
          setError(null);
          setItems([]);
        }}
        placeholder={example}
        spellCheck={false}
        value={source}
      />
      <p className="mt-2 text-xs text-muted-foreground" id="mcp-import-help">
        不会导入 env 或 Header 中的明文密钥，只会保留环境变量键名。
      </p>
      {error ? <p className="mt-2 text-sm text-destructive" role="alert">{error}</p> : null}

      {items.length ? (
        <div className="mt-4 space-y-2" role="status">
          {items.map((item) => {
            const duplicated = existing.has(item.name);
            return (
              <article className="rounded-lg border bg-background px-3 py-3" key={item.name}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="flex min-w-0 items-center gap-2 text-sm font-medium">
                    <Check aria-hidden="true" className="size-4 shrink-0 text-success" />
                    <span className="truncate">{item.name}</span>
                  </span>
                  <span className={`rounded-full border px-2 py-0.5 text-xs ${duplicated ? "border-warning/25 bg-warning/10 text-warning" : "text-muted-foreground"}`}>
                    {duplicated ? "已存在，跳过" : item.payload.transport_type === "stdio" ? "stdio" : "HTTP"}
                  </span>
                </div>
                <p className="mt-2 truncate font-mono text-xs text-muted-foreground" title={connectionSummary(item.payload)}>
                  {connectionSummary(item.payload)}
                </p>
                {item.warnings.map((warning) => (
                  <p className="mt-2 text-xs text-warning" key={warning}>{warning}</p>
                ))}
              </article>
            );
          })}
        </div>
      ) : null}

      <label className="mt-4 flex min-h-11 items-center gap-3 rounded-lg border bg-muted/30 px-3 text-sm">
        <input checked={connectAfterImport} onChange={(event) => setConnectAfterImport(event.target.checked)} type="checkbox" />
        <span><strong className="font-medium">导入后自动连接</strong><span className="ml-2 text-xs text-muted-foreground">只完成握手和能力同步，不会调用 Tool</span></span>
      </label>

      <div className="mt-4 flex flex-wrap gap-2">
        <Button className="min-h-11" disabled={pending || !source.trim()} onClick={preview} type="button" variant="outline">
          <ShieldCheck aria-hidden="true" />解析并预览
        </Button>
        <Button
          className="min-h-11"
          disabled={pending || !importable.length}
          onClick={() => onImport(importable.map((item) => item.payload), connectAfterImport)}
          type="button"
        >
          <PlugZap aria-hidden="true" />
          {pending ? "正在导入" : importable.length ? `导入 ${importable.length} 个 MCP` : "导入 MCP"}
        </Button>
      </div>
    </section>
  );
}

function connectionSummary(payload: McpServerWritePayload): string {
  return payload.transport_type === "stdio"
    ? [payload.command, ...payload.args].filter(Boolean).join(" ")
    : payload.url || "未配置服务地址";
}
