import { Database, Pencil, PlugZap, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import type {
  DatabaseConnectionWritePayload,
} from "@/lib/api/management";
import type { DatabaseConnection } from "@/lib/api/management-schemas";

import { DatabaseConnectionForm } from "./database-connection-form";

interface DatabaseConnectionsPanelProps {
  items: DatabaseConnection[];
  busy: boolean;
  onSave: (payload: DatabaseConnectionWritePayload, connectionId?: string) => Promise<void>;
  onTest: (connection: DatabaseConnection) => void;
  onDelete: (connection: DatabaseConnection) => void;
}

export function DatabaseConnectionsPanel({
  items,
  busy,
  onSave,
  onTest,
  onDelete,
}: DatabaseConnectionsPanelProps) {
  const [editing, setEditing] = useState<DatabaseConnection | null | "new">(null);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-semibold">数据库连接</h2>
          <p className="mt-1 text-sm text-muted-foreground">连接属于当前 Agent，后续会话可以直接使用。</p>
        </div>
        <Button onClick={() => setEditing("new")} variant="outline"><Plus aria-hidden="true" />新增 MySQL 连接</Button>
      </div>

      {editing ? (
        <section className="rounded-2xl border border-border/85 bg-card p-5 shadow-xs" aria-label={editing === "new" ? "新增数据库连接" : "编辑数据库连接"}>
          <DatabaseConnectionForm
            imported={editing !== "new" && editing.imported}
            {...(editing === "new" ? {} : { initial: editing })}
            onCancel={() => setEditing(null)}
            onSubmit={async (payload) => {
              await onSave(payload, editing === "new" ? undefined : editing.connection_id);
              setEditing(null);
            }}
            passwordConfigured={editing !== "new" && editing.password_configured}
          />
        </section>
      ) : null}

      {!items.length ? (
        <div className="grid min-h-48 place-items-center rounded-2xl border border-dashed border-border/80 bg-card/50 text-center">
          <div>
            <span className="mx-auto grid size-11 place-items-center rounded-xl bg-muted/60 text-muted-foreground">
              <Database aria-hidden="true" className="size-5" />
            </span>
            <p className="mt-3 text-sm text-muted-foreground">还没有保存数据库连接。</p>
          </div>
        </div>
      ) : (
        <div className="grid gap-3.5 lg:grid-cols-2">
          {items.map((item) => (
            <article className="group rounded-2xl border border-border/85 bg-card p-4.5 shadow-2xs transition-all duration-150 hover:border-primary/30 hover:shadow-sm" key={item.connection_id}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="truncate font-semibold tracking-tight">{item.name}</h3>
                    <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${item.status === "connected" ? "border-success/20 bg-success/10 text-success" : "border-warning/20 bg-warning/10 text-warning"}`}>
                      <span className={`size-1.5 rounded-full ${item.status === "connected" ? "bg-success" : "bg-warning"}`} />
                      {item.status === "connected" ? "已连接" : item.status}
                    </span>
                    {item.imported ? <span className="rounded-full border border-border/70 bg-muted/60 px-2 py-0.5 text-xs text-muted-foreground">外部 envfile</span> : null}
                  </div>
                  <p className="mt-2 break-all font-mono text-xs text-muted-foreground">{item.username}@{item.host}:{item.port}/{item.database}</p>
                  <p className="mt-1.5 text-xs text-muted-foreground">{item.tool_names.length} 个 Agent 数据库 Tool</p>
                  {item.last_error ? <p className="mt-2 rounded-lg bg-destructive/8 px-2.5 py-1.5 text-xs text-destructive" role="alert">{item.last_error}</p> : null}
                </div>
                <span className="grid size-9 shrink-0 place-items-center rounded-xl border border-primary/15 bg-primary-soft text-primary">
                  <Database aria-hidden="true" className="size-4" />
                </span>
              </div>
              <div className="mt-4 flex flex-wrap gap-2 border-t border-border/50 pt-3">
                <Button disabled={busy} onClick={() => onTest(item)} size="sm" variant="outline"><PlugZap aria-hidden="true" />测试连接</Button>
                {!item.imported ? <Button disabled={busy} onClick={() => setEditing(item)} size="sm" variant="ghost"><Pencil aria-hidden="true" />编辑</Button> : null}
                <Button disabled={busy} onClick={() => onDelete(item)} size="sm" variant="ghost"><Trash2 aria-hidden="true" />删除</Button>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
