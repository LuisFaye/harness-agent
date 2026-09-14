import { Save, SlidersHorizontal, X } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import type { McpServerWritePayload } from "@/lib/api/management";
import type { McpServer } from "@/lib/api/management-schemas";

import { parseHeaderMap, parseLines } from "./capability-format";

interface McpServerFormProps {
  editing: McpServer | null;
  pending: boolean;
  onCancel: () => void;
  onSave: (payload: McpServerWritePayload, serverId?: string) => void;
}

const inputClass = "mt-1.5 h-11 w-full rounded-lg border bg-background px-3 text-base outline-none focus:border-ring focus:ring-3 focus:ring-ring/20 sm:text-sm";
const textareaClass = "mt-1.5 min-h-24 w-full resize-y rounded-lg border bg-background px-3 py-2 font-mono text-base leading-6 outline-none focus:border-ring focus:ring-3 focus:ring-ring/20 sm:text-xs";

export function McpServerForm({ editing, pending, onCancel, onSave }: McpServerFormProps) {
  const [name, setName] = useState("");
  const [transport, setTransport] = useState<"stdio" | "streamable_http">("stdio");
  const [command, setCommand] = useState("");
  const [args, setArgs] = useState("");
  const [url, setUrl] = useState("");
  const [environmentKeys, setEnvironmentKeys] = useState("");
  const [headerMap, setHeaderMap] = useState("");
  const [enabled, setEnabled] = useState(true);

  useEffect(() => {
    setName(editing?.name ?? "");
    setTransport(editing?.transport_type ?? "stdio");
    setCommand(editing?.command ?? "");
    setArgs((editing?.args ?? []).join("\n"));
    setUrl(editing?.url ?? "");
    setEnvironmentKeys((editing?.environment_keys ?? []).join("\n"));
    setHeaderMap(Object.entries(editing?.header_env_map ?? {}).map(([key, value]) => `${key}=${value}`).join("\n"));
    setEnabled(editing?.enabled ?? true);
  }, [editing]);

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSave({
      name: name.trim(),
      transport_type: transport,
      command: transport === "stdio" ? command.trim() || null : null,
      args: transport === "stdio" ? parseLines(args) : [],
      url: transport === "streamable_http" ? url.trim() || null : null,
      environment_keys: parseLines(environmentKeys),
      header_env_map: parseHeaderMap(headerMap),
      enabled,
    }, editing?.server_id);
  }

  return (
    <form className="rounded-xl border bg-card p-5" onSubmit={submit}>
      <header className="flex items-start gap-3">
        <span aria-hidden="true" className="grid size-10 shrink-0 place-items-center rounded-lg bg-accent text-accent-foreground">
          <SlidersHorizontal className="size-5" />
        </span>
        <div>
          <h2 className="font-semibold">{editing ? `编辑 ${editing.name}` : "手动配置 MCP"}</h2>
          <p className="mt-1 text-sm text-muted-foreground">适合从零创建或调整连接参数。</p>
        </div>
      </header>

      <div className="mt-5 grid gap-4 md:grid-cols-2">
        <label className="text-sm font-medium" htmlFor="mcp-server-name">
          名称
          <input className={inputClass} id="mcp-server-name" maxLength={64} onChange={(event) => setName(event.target.value)} required value={name} />
        </label>
        <label className="text-sm font-medium" htmlFor="mcp-transport-type">
          连接方式
          <select className={inputClass} id="mcp-transport-type" onChange={(event) => setTransport(event.target.value as typeof transport)} value={transport}>
            <option value="stdio">本地进程（stdio）</option>
            <option value="streamable_http">远程服务（Streamable HTTP）</option>
          </select>
        </label>
        {transport === "stdio" ? (
          <>
            <label className="text-sm font-medium md:col-span-2" htmlFor="mcp-command">
              启动命令
              <input className={`${inputClass} font-mono`} id="mcp-command" maxLength={500} onChange={(event) => setCommand(event.target.value)} placeholder="mysql_mcp_server_pro" required value={command} />
              <span className="mt-1.5 block text-xs font-normal text-muted-foreground">找不到命令时请填写可执行文件的绝对路径。</span>
            </label>
            <label className="text-sm font-medium md:col-span-2" htmlFor="mcp-args">
              命令参数 <span className="font-normal text-muted-foreground">· 每行一个</span>
              <textarea className={textareaClass} id="mcp-args" onChange={(event) => setArgs(event.target.value)} placeholder={"--mode\nstdio\n--envfile\n/path/to/.env"} value={args} />
            </label>
          </>
        ) : (
          <label className="text-sm font-medium md:col-span-2" htmlFor="mcp-url">
            服务地址
            <input className={inputClass} id="mcp-url" maxLength={1000} onChange={(event) => setUrl(event.target.value)} placeholder="https://example.com/mcp" required type="url" value={url} />
          </label>
        )}
        <details className="rounded-lg border bg-muted/20 md:col-span-2">
          <summary className="min-h-11 cursor-pointer px-3 py-3 text-sm font-medium">高级设置与密钥映射</summary>
          <div className="grid gap-4 border-t p-3 md:grid-cols-2">
            <label className="text-sm font-medium" htmlFor="mcp-environment-keys">
              环境变量键名
              <textarea className={textareaClass} id="mcp-environment-keys" onChange={(event) => setEnvironmentKeys(event.target.value)} placeholder="每行一个，例如 MCP_TOKEN" value={environmentKeys} />
              <span className="mt-1.5 block text-xs font-normal text-muted-foreground">只保存键名，值由 Agent 运行环境提供。</span>
            </label>
            <label className="text-sm font-medium" htmlFor="mcp-header-map">
              HTTP Header 映射
              <textarea className={textareaClass} id="mcp-header-map" onChange={(event) => setHeaderMap(event.target.value)} placeholder={"Authorization=MCP_AUTHORIZATION\nX-Tenant=TENANT_ID"} value={headerMap} />
              <span className="mt-1.5 block text-xs font-normal text-muted-foreground">格式为 Header=环境变量键名。</span>
            </label>
          </div>
        </details>
        <label className="flex min-h-11 items-center gap-3 rounded-lg border bg-muted/30 px-3 text-sm md:col-span-2">
          <input checked={enabled} onChange={(event) => setEnabled(event.target.checked)} type="checkbox" />
          保存后启用 Server
        </label>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <Button className="min-h-11" disabled={pending} size="sm" type="submit"><Save aria-hidden="true" />{pending ? "正在保存" : "保存 MCP"}</Button>
        {editing ? (
          <Button className="min-h-11" disabled={pending} onClick={onCancel} size="sm" type="button" variant="outline"><X aria-hidden="true" />取消编辑</Button>
        ) : null}
      </div>
    </form>
  );
}
