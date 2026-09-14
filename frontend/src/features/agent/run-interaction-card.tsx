import {
  AlertTriangle,
  CircleStop,
  HelpCircle,
  RotateCcw,
  ShieldAlert,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import type { DatabaseConnectionWritePayload } from "@/lib/api/management";
import type { RunProjection } from "@/lib/stream/run-projection";
import { DatabaseConnectionForm } from "@/features/capabilities/database-connection-form";

interface RunInteractionCardProps {
  projection: RunProjection | null;
  errorMessage: string | null;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  onRetry: () => void;
  onDismissError: () => void;
  onDatabaseConnect?: (payload: DatabaseConnectionWritePayload, requestId: string) => Promise<void>;
}

export function RunInteractionCard({
  projection,
  errorMessage,
  busy,
  onConfirm,
  onCancel,
  onRetry,
  onDismissError,
  onDatabaseConnect,
}: RunInteractionCardProps) {
  if (projection?.status === "waiting_confirmation") {
    return (
      <section className="m-4 rounded-xl border border-warning/40 bg-warning/10 p-4 sm:mx-6" role="group" aria-label="高风险操作确认">
        <div className="flex items-start gap-3">
          <ShieldAlert aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-warning" />
          <div>
            <h2 className="font-semibold">等待执行确认</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              确认后 Agent 会继续刚才暂停的操作；取消会结束这次操作。
            </p>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button disabled={busy} onClick={onConfirm}>确认执行</Button>
          <Button disabled={busy} onClick={onCancel} variant="outline">取消</Button>
        </div>
      </section>
    );
  }

  if (projection?.status === "waiting_input") {
    if (projection.interaction?.type === "database_connection") {
      const interaction = projection.interaction;
      return (
        <section className="m-4 rounded-xl border border-primary/25 bg-primary/5 p-4 sm:mx-6" aria-labelledby={`database-connection-${interaction.request_id}`}>
          <div className="mb-4 flex items-start gap-3">
            <HelpCircle aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-primary" />
            <div>
              <h2 className="font-semibold" id={`database-connection-${interaction.request_id}`}>{interaction.title}</h2>
              <p className="mt-1 text-sm text-muted-foreground">{interaction.description}</p>
              <p className="mt-1 text-xs text-muted-foreground">保存范围：当前 Agent 的所有后续会话</p>
            </div>
          </div>
          <DatabaseConnectionForm
            initial={interaction}
            onSubmit={async (payload) => {
              if (!onDatabaseConnect) {
                throw new Error("当前页面没有启用数据库连接提交能力。");
              }
              await onDatabaseConnect(payload, interaction.request_id);
            }}
          />
        </section>
      );
    }
    return (
      <section className="m-4 rounded-xl border border-warning/40 bg-warning/10 p-4 sm:mx-6">
        <div className="flex items-start gap-3">
          <HelpCircle aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-warning" />
          <div>
            <h2 className="font-semibold">需要补充信息</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              {projection.missingInfo.length
                ? projection.missingInfo.join("；")
                : "请在输入框补充 Agent 继续执行所需的信息。"}
            </p>
          </div>
        </div>
      </section>
    );
  }

  if (errorMessage || projection?.status === "failed") {
    return (
      <section className="m-4 rounded-xl border border-destructive/40 bg-destructive/10 p-4 sm:mx-6" role="alert">
        <div className="flex items-start gap-3">
          <AlertTriangle aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-destructive" />
          <div className="min-w-0 flex-1">
            <h2 className="font-semibold">本次运行没有完成</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              {errorMessage || projection?.error?.message || "请稍后重试。"}
            </p>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button disabled={busy} onClick={onRetry} variant="outline">
            <RotateCcw aria-hidden="true" />
            重试
          </Button>
          <Button onClick={onDismissError} variant="ghost">关闭提示</Button>
        </div>
      </section>
    );
  }

  if (projection?.status === "cancel_requested" || projection?.status === "cancelled") {
    return (
      <p className="m-4 flex items-center gap-2 rounded-lg bg-muted px-3 py-2 text-sm text-muted-foreground sm:mx-6" role="status">
        <CircleStop aria-hidden="true" className="size-4" />
        {projection.status === "cancelled" ? "任务已取消。" : "停止请求已提交，正在等待当前步骤安全结束。"}
      </p>
    );
  }

  return null;
}
