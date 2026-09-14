import { LoaderCircle, LockKeyhole } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import type { DatabaseConnectionWritePayload } from "@/lib/api/management";

export interface DatabaseConnectionFormValues {
  name?: string;
  host?: string;
  port?: number;
  database?: string;
  username?: string;
}

interface DatabaseConnectionFormProps {
  initial?: DatabaseConnectionFormValues;
  imported?: boolean;
  passwordConfigured?: boolean;
  submitLabel?: string;
  onSubmit: (payload: DatabaseConnectionWritePayload) => Promise<void>;
  onCancel?: () => void;
}

const inputClass = "mt-1 min-h-11 w-full rounded-lg border bg-background px-3 text-sm outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring";

export function DatabaseConnectionForm({
  initial,
  imported = false,
  passwordConfigured = false,
  submitLabel = "保存并测试连接",
  onSubmit,
  onCancel,
}: DatabaseConnectionFormProps) {
  const [name, setName] = useState(initial?.name ?? "");
  const [host, setHost] = useState(initial?.host ?? "");
  const [port, setPort] = useState(String(initial?.port ?? 3306));
  const [database, setDatabase] = useState(initial?.database ?? "");
  const [username, setUsername] = useState(initial?.username ?? "");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setName(initial?.name ?? "");
    setHost(initial?.host ?? "");
    setPort(String(initial?.port ?? 3306));
    setDatabase(initial?.database ?? "");
    setUsername(initial?.username ?? "");
    setPassword("");
    setError(null);
  }, [initial]);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedPort = Number(port);
    if (!Number.isInteger(normalizedPort) || normalizedPort < 1 || normalizedPort > 65535) {
      setError("端口必须是 1 到 65535 之间的整数。");
      return;
    }
    if (!password && !passwordConfigured) {
      setError("请输入数据库密码。");
      return;
    }
    setPending(true);
    setError(null);
    try {
      await onSubmit({
        name: name.trim(),
        host: host.trim(),
        port: normalizedPort,
        database: database.trim(),
        username: username.trim(),
        password,
      });
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "数据库连接失败。");
    } finally {
      setPending(false);
    }
  }

  if (imported) {
    return (
      <div className="rounded-lg border bg-muted/40 p-4 text-sm text-muted-foreground">
        这个连接使用外部 envfile。请修改原文件后点击“测试连接”，本页面不会读取或改写密码。
      </div>
    );
  }

  return (
    <form className="space-y-4" onSubmit={submit}>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="连接名称" id="database-connection-name">
          <input className={inputClass} id="database-connection-name" maxLength={64} onChange={(event) => setName(event.target.value)} required value={name} />
        </Field>
        <Field label="主机" id="database-connection-host">
          <input autoCapitalize="none" className={inputClass} id="database-connection-host" maxLength={255} onChange={(event) => setHost(event.target.value)} required value={host} />
        </Field>
        <Field label="端口" id="database-connection-port">
          <input className={inputClass} id="database-connection-port" inputMode="numeric" min={1} max={65535} onChange={(event) => setPort(event.target.value)} required type="number" value={port} />
        </Field>
        <Field label="默认数据库" id="database-connection-database">
          <input autoCapitalize="none" className={inputClass} id="database-connection-database" maxLength={128} onChange={(event) => setDatabase(event.target.value)} required value={database} />
        </Field>
        <Field label="用户名" id="database-connection-username">
          <input autoCapitalize="none" className={inputClass} id="database-connection-username" maxLength={128} onChange={(event) => setUsername(event.target.value)} required value={username} />
        </Field>
        <Field label="密码" id="database-connection-password">
          <input
            autoComplete="off"
            className={inputClass}
            id="database-connection-password"
            maxLength={4096}
            onChange={(event) => setPassword(event.target.value)}
            placeholder={passwordConfigured ? "留空则保留原密码" : ""}
            required={!passwordConfigured}
            type="password"
            value={password}
          />
        </Field>
      </div>
      <p className="flex items-start gap-2 text-xs text-muted-foreground">
        <LockKeyhole aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
        密码直达本机后端并写入权限为 0600 的私密文件，不会进入模型、聊天记录或事件流。
      </p>
      {error ? <p className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive" role="alert">{error}</p> : null}
      <div className="flex flex-wrap gap-2">
        <Button disabled={pending} type="submit">
          {pending ? <LoaderCircle aria-hidden="true" className="animate-spin" /> : null}
          {pending ? "正在连接…" : submitLabel}
        </Button>
        {onCancel ? <Button disabled={pending} onClick={onCancel} type="button" variant="outline">取消</Button> : null}
      </div>
    </form>
  );
}

function Field({ children, id, label }: { children: React.ReactNode; id: string; label: string }) {
  return (
    <div>
      <label className="text-sm font-medium" htmlFor={id}>{label}</label>
      {children}
    </div>
  );
}
