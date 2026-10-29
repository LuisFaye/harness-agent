import { CheckCircle2, FileCog, KeyRound, ShieldCheck } from "lucide-react";

import type { ModelConfigResponse } from "@/lib/api/management-schemas";

interface SettingsStatusPanelProps {
  config: ModelConfigResponse;
}

const fieldLabels: Record<string, string> = {
  provider: "Provider",
  model_id: "模型 ID",
  api_key: "API Key",
  base_url: "Base URL",
  agno_telemetry: "Agno 遥测",
  thinking_enabled: "深度思考",
  reasoning_effort: "思考强度",
  context_token_budget: "上下文预算",
};

export function SettingsStatusPanel({ config }: SettingsStatusPanelProps) {
  const overrides = Object.entries(config.env_overrides).filter(([, active]) => active);

  return (
    <aside className="space-y-4">
      <section className="rounded-2xl border border-border/85 bg-card p-5 shadow-2xs">
        <h2 className="flex items-center gap-2 font-semibold tracking-tight"><CheckCircle2 aria-hidden="true" className="size-4 text-success" />当前生效</h2>
        <dl className="mt-4 grid gap-3 text-sm">
          <Meta label="Provider" value={config.effective.provider} />
          <Meta label="模型 ID" value={config.effective.model_id} />
          <Meta label="Base URL" value={config.effective.base_url} />
          <Meta label="API Key" value={config.effective.api_key_configured ? "已配置" : "未配置"} />
          <Meta label="思考" value={`${config.effective.thinking_enabled ? "开启" : "关闭"} · ${config.effective.reasoning_effort}`} />
          <Meta label="上下文预算" value={`${config.effective.context_token_budget} tokens`} />
        </dl>
      </section>

      <section className="rounded-2xl border border-border/85 bg-card p-5 shadow-2xs">
        <h2 className="flex items-center gap-2 font-semibold tracking-tight"><FileCog aria-hidden="true" className="size-4 text-primary" />配置来源</h2>
        <p className="mt-3 break-all rounded-xl border border-border/60 bg-muted/40 p-3 font-mono text-xs text-muted-foreground">{config.config_path}</p>
        <div className="mt-3 text-xs text-muted-foreground">
          {overrides.length
            ? `环境变量正在覆盖：${overrides.map(([key]) => fieldLabels[key] || key).join("、")}`
            : "当前没有环境变量覆盖模型字段。"}
        </div>
      </section>

      <section className="rounded-2xl border border-success/20 bg-success/6 p-5 text-sm">
        <h2 className="flex items-center gap-2 font-semibold tracking-tight text-success"><ShieldCheck aria-hidden="true" className="size-4" />密钥保护</h2>
        <p className="mt-2 text-muted-foreground">页面只读取是否已配置，不显示 Key 内容；保存时留空会保留原值。</p>
        <span className="mt-3 inline-flex items-center gap-1.5 rounded-full border border-success/20 bg-success/10 px-2.5 py-0.5 text-xs font-medium text-success"><KeyRound aria-hidden="true" className="size-3.5" />API 返回值已脱敏</span>
      </section>
    </aside>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[6rem_1fr] gap-3 border-b border-border/40 pb-2.5 last:border-b-0 last:pb-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-all text-xs font-medium">{value}</dd>
    </div>
  );
}
