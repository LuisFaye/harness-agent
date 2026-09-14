import { Cable, ChevronDown, Pencil, Plus, Power, RefreshCw, ServerCog, Trash2, Unplug } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/features/management/confirm-dialog";
import type { McpServerWritePayload } from "@/lib/api/management";
import type { McpServer } from "@/lib/api/management-schemas";

import { mcpStatus } from "./capability-format";
import { McpImportCard } from "./mcp-import-card";
import { McpServerForm } from "./mcp-server-form";

interface McpPanelProps {
  items: McpServer[];
  busyKey: string | null;
  onAction: (server: McpServer, action: "connect" | "disconnect" | "sync" | "toggle") => void;
  onDelete: (server: McpServer) => void;
  onImport: (payloads: McpServerWritePayload[], connectAfterImport: boolean) => void;
  onSave: (payload: McpServerWritePayload, serverId?: string) => void;
}

export function McpPanel({ items, busyKey, onAction, onDelete, onImport, onSave }: McpPanelProps) {
  const [editing, setEditing] = useState<McpServer | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<McpServer | null>(null);
  const [addMode, setAddMode] = useState<"import" | "manual">("import");

  return (
    <>
      <div className="space-y-6">
        <section aria-labelledby="add-mcp-title">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className="font-semibold" id="add-mcp-title">添加 MCP</h2>
              <p className="mt-1 text-sm text-muted-foreground">粘贴现有配置最快，也可以手动填写连接参数。</p>
            </div>
            <div aria-label="添加方式" className="flex rounded-lg border bg-muted/30 p-1" role="group">
              <button
                aria-pressed={addMode === "import"}
                className={`min-h-11 rounded-md px-3 text-sm font-medium ${addMode === "import" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground"}`}
                onClick={() => {
                  setEditing(null);
                  setAddMode("import");
                }}
                type="button"
              >
                粘贴配置
              </button>
              <button
                aria-pressed={addMode === "manual"}
                className={`min-h-11 rounded-md px-3 text-sm font-medium ${addMode === "manual" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground"}`}
                onClick={() => {
                  setEditing(null);
                  setAddMode("manual");
                }}
                type="button"
              >
                手动配置
              </button>
            </div>
          </div>
          <div className="mt-4">
            {addMode === "import" && !editing ? (
              <McpImportCard
                existingNames={items.map((item) => item.name)}
                onImport={onImport}
                pending={busyKey === "import"}
              />
            ) : (
              <McpServerForm
                editing={editing}
                onCancel={() => {
                  setEditing(null);
                  setAddMode("import");
                }}
                onSave={(payload, serverId) => {
                  onSave(payload, serverId);
                  setEditing(null);
                  setAddMode("import");
                }}
                pending={busyKey === "save"}
              />
            )}
          </div>
        </section>

        <section aria-labelledby="mcp-server-list-title" className="min-w-0">
          <div className="mb-4 flex flex-wrap items-end justify-between gap-3 border-t pt-6">
            <div>
              <h2 className="font-semibold" id="mcp-server-list-title">已配置 Server</h2>
              <p className="mt-1 text-sm text-muted-foreground">连接后同步 Tool、Resource 和 Prompt，启用不等于已连接。</p>
            </div>
            <span className="text-sm text-muted-foreground">{items.length} 个配置</span>
          </div>
          {items.length ? (
            <div className="grid min-w-0 gap-3">
              {items.map((server) => {
                const status = mcpStatus(server.status);
                const busy = busyKey?.endsWith(`:${server.server_id}`) ?? false;
                const connected = server.status === "connected";
                return (
                  <article className="min-w-0 rounded-xl border bg-card p-4" key={server.server_id}>
                    <div className="flex min-w-0 flex-col gap-4 lg:flex-row lg:items-center">
                      <span aria-hidden="true" className="grid size-10 shrink-0 place-items-center rounded-lg bg-accent text-accent-foreground">
                        <ServerCog className="size-5" />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 className="truncate font-semibold" title={server.name}>{server.name}</h3>
                          <span className={`rounded-full border px-2 py-0.5 text-xs font-semibold ${status.className}`}>{status.label}</span>
                          {!server.enabled ? <span className="rounded-full border px-2 py-0.5 text-xs text-muted-foreground">已停用</span> : null}
                        </div>
                        <p className="mt-1 truncate font-mono text-xs text-muted-foreground" title={connectionSummary(server)}>
                          {connectionSummary(server)}
                        </p>
                      </div>
                      <div className="grid min-w-0 grid-cols-3 gap-3 text-center text-xs text-muted-foreground lg:w-56">
                        <Count label="Tools" value={server.tools.length} />
                        <Count label="Resources" value={server.resources.length} />
                        <Count label="Prompts" value={server.prompts.length} />
                      </div>
                      <div className="flex flex-wrap gap-2 lg:justify-end">
                        <Button
                          disabled={busy || !server.enabled}
                          className="min-h-11"
                          onClick={() => onAction(server, connected ? "sync" : "connect")}
                          size="sm"
                          variant={connected ? "outline" : "default"}
                        >
                          {connected ? <RefreshCw aria-hidden="true" /> : <Cable aria-hidden="true" />}
                          {connected ? "同步能力" : "连接"}
                        </Button>
                        <details className="relative">
                          <summary className="flex min-h-11 cursor-pointer list-none items-center gap-1 rounded-lg border px-3 text-sm font-medium">
                            更多<ChevronDown aria-hidden="true" className="size-4" />
                          </summary>
                          <div className="mt-2 flex flex-wrap gap-1 rounded-lg border bg-background p-2 lg:absolute lg:right-0 lg:z-10 lg:w-64 lg:shadow-lg">
                            {connected ? <Button className="min-h-11" disabled={busy} onClick={() => onAction(server, "disconnect")} size="sm" variant="ghost"><Unplug aria-hidden="true" />断开</Button> : null}
                            <Button className="min-h-11" disabled={busy} onClick={() => onAction(server, "toggle")} size="sm" variant="ghost"><Power aria-hidden="true" />{server.enabled ? "停用" : "启用"}</Button>
                            <Button
                              className="min-h-11"
                              disabled={busy}
                              onClick={() => {
                                setEditing(server);
                                setAddMode("manual");
                              }}
                              size="sm"
                              variant="ghost"
                            >
                              <Pencil aria-hidden="true" />编辑
                            </Button>
                            <Button className="min-h-11 text-destructive hover:text-destructive" disabled={busy} onClick={() => setDeleteTarget(server)} size="sm" variant="ghost"><Trash2 aria-hidden="true" />删除</Button>
                          </div>
                        </details>
                      </div>
                    </div>
                    {server.last_error ? <p className="mt-3 rounded-lg bg-destructive/5 px-3 py-2 text-xs text-destructive">{server.last_error}</p> : null}
                  </article>
                );
              })}
            </div>
          ) : (
            <div className="grid min-h-44 place-items-center rounded-xl border border-dashed bg-card p-6 text-center">
              <div>
                <Plus aria-hidden="true" className="mx-auto size-6 text-muted-foreground" />
                <strong className="mt-3 block text-sm">还没有 MCP Server</strong>
                <p className="mt-1 text-sm text-muted-foreground">在上方粘贴已有配置即可开始。</p>
              </div>
            </div>
          )}
        </section>
      </div>
      <ConfirmDialog
        confirmLabel="删除 Server"
        description={`删除后会移除 ${deleteTarget?.name || "这个 Server"} 的配置及已同步能力，且无法从页面恢复。`}
        onConfirm={() => {
          if (deleteTarget) {
            onDelete(deleteTarget);
            setDeleteTarget(null);
          }
        }}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        open={Boolean(deleteTarget)}
        pending={Boolean(deleteTarget && busyKey === `delete:${deleteTarget.server_id}`)}
        title="确认删除 MCP Server？"
      />
    </>
  );
}

function Count({ label, value }: { label: string; value: number }) {
  return <span><strong className="block text-base text-foreground">{value}</strong>{label}</span>;
}

function connectionSummary(server: McpServer): string {
  return server.transport_type === "stdio"
    ? [server.command, ...server.args].filter(Boolean).join(" ")
    : server.url || "未配置服务地址";
}
