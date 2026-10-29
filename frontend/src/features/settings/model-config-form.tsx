import { useMutation } from "@tanstack/react-query";
import { KeyRound, List, LoaderCircle, RotateCcw, Save } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { listAvailableModels, type ModelConfigWritePayload } from "@/lib/api/management";
import type { ModelConfigResponse } from "@/lib/api/management-schemas";

interface ModelConfigFormProps {
  config: ModelConfigResponse;
  pending: boolean;
  onSave: (payload: ModelConfigWritePayload) => Promise<ModelConfigResponse>;
}

const inputClass = "mt-1.5 h-10 w-full rounded-xl border border-border/85 bg-background px-3.5 text-sm shadow-2xs outline-none transition-colors focus:border-primary/45 focus:ring-3 focus:ring-ring/20";

export function ModelConfigForm({ config, pending, onSave }: ModelConfigFormProps) {
  const [provider, setProvider] = useState<"openai_compatible" | "dashscope">(config.config.provider === "dashscope" ? "dashscope" : "openai_compatible");
  const [modelId, setModelId] = useState(config.config.model_id);
  const [baseUrl, setBaseUrl] = useState(config.config.base_url);
  const [apiKey, setApiKey] = useState("");
  const [clearApiKey, setClearApiKey] = useState(false);
  const [telemetry, setTelemetry] = useState(config.config.agno_telemetry);
  const [thinking, setThinking] = useState(config.config.thinking_enabled);
  const [reasoningEffort, setReasoningEffort] = useState<"high" | "max">(config.config.reasoning_effort === "max" ? "max" : "high");
  const [contextBudget, setContextBudget] = useState(config.config.context_token_budget);
  const dirtyRef = useRef(false);
  const dirty = provider !== config.config.provider || modelId !== config.config.model_id
    || baseUrl !== config.config.base_url || Boolean(apiKey) || clearApiKey
    || telemetry !== config.config.agno_telemetry || thinking !== config.config.thinking_enabled
    || reasoningEffort !== config.config.reasoning_effort
    || contextBudget !== config.config.context_token_budget;
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const fetchModelsMutation = useMutation({ mutationFn: listAvailableModels });

  const modelOptions = useMemo(() => {
    const options = [...availableModels];
    if (modelId && !options.includes(modelId)) {
      options.unshift(modelId);
    }
    return options;
  }, [availableModels, modelId]);

  function buildPayload(nextModelId = modelId): ModelConfigWritePayload {
    return {
      provider,
      model_id: nextModelId.trim(),
      api_key: apiKey,
      base_url: baseUrl.trim(),
      clear_api_key: clearApiKey,
      agno_telemetry: telemetry,
      thinking_enabled: thinking,
      reasoning_effort: reasoningEffort,
      context_token_budget: contextBudget,
    };
  }

  function syncFromConfig(config: ModelConfigResponse) {
    setProvider(config.config.provider === "dashscope" ? "dashscope" : "openai_compatible");
    setModelId(config.config.model_id);
    setBaseUrl(config.config.base_url);
    setApiKey("");
    setClearApiKey(false);
    setTelemetry(config.config.agno_telemetry);
    setThinking(config.config.thinking_enabled);
    setReasoningEffort(config.config.reasoning_effort === "max" ? "max" : "high");
    setContextBudget(config.config.context_token_budget);
  }

  function resetAll() {
    syncFromConfig(config);
    dirtyRef.current = false;
    setAvailableModels([]);
    fetchModelsMutation.reset();
  }

  useEffect(() => {
    // 后台刷新只更新干净表单，不能覆盖用户尚未保存的输入。
    if (!dirtyRef.current) syncFromConfig(config);
  }, [config]);

  useEffect(() => {
    dirtyRef.current = dirty;
  }, [dirty]);

  useEffect(() => {
    if (!dirty) return;
    const preventLoss = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", preventLoss);
    return () => window.removeEventListener("beforeunload", preventLoss);
  }, [dirty]);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    try {
      const saved = await onSave(buildPayload());
      syncFromConfig(saved);
      dirtyRef.current = false;
    } catch {
      // 保存错误由页面统一展示，保留原表单以便修正后重试。
    }
  }

  function fetchModels() {
    // 使用表单当前填写的地址/Key（未保存也可），留空的 Key 由后端沿用已保存值。
    fetchModelsMutation.mutate({ provider, base_url: baseUrl.trim(), api_key: apiKey.trim() }, {
      onSuccess: (payload) => {
        if (payload.status === "ok") {
          setAvailableModels(payload.models);
        }
      },
    });
  }

  return (
    <form className="rounded-2xl border border-border/85 bg-card p-5 shadow-2xs sm:p-6" onSubmit={submit}>
      <header>
        <p className="text-xs font-semibold uppercase tracking-[0.12em] text-primary">Model</p>
        <h2 className="mt-1 font-semibold tracking-tight">模型配置</h2>
        <p className="mt-1 text-sm text-muted-foreground">保存后写入本地配置；环境变量仍具有更高优先级。</p>
      </header>

      <p className="mt-3 inline-flex items-center gap-1.5 rounded-full border border-border/70 bg-muted/40 px-2.5 py-1 text-xs text-muted-foreground" role="status">
        <span className={`size-1.5 rounded-full ${dirty ? "bg-warning" : "bg-success"}`} />
        {dirty ? "有未保存的修改，点击保存配置后生效。" : "当前配置已保存。"}
      </p>
      <fieldset disabled={pending}>
      <div className="mt-6 grid gap-5 sm:grid-cols-2">
        <label className="text-sm font-medium">
          Provider
          <select className={inputClass} onChange={(event) => setProvider(event.target.value as typeof provider)} value={provider}>
            <option value="openai_compatible">OpenAI 兼容</option>
            <option value="dashscope">DashScope</option>
          </select>
        </label>
        <div className="text-sm font-medium sm:col-span-2">
          <label htmlFor="model-selection">模型</label>
          <div className="mt-1.5 flex flex-col gap-2 sm:flex-row">
            <select
              className={`${inputClass} mt-0 min-w-0 flex-1`}
              disabled={pending || fetchModelsMutation.isPending}
              id="model-selection"
              onChange={(event) => setModelId(event.target.value)}
              value={modelId}
            >
              {modelOptions.length
                ? modelOptions.map((option) => <option key={option} value={option}>{option}</option>)
                : <option value={modelId}>{modelId || "请先获取模型"}</option>}
            </select>
            <Button
              className="shrink-0"
              disabled={pending || fetchModelsMutation.isPending}
              onClick={fetchModels}
              type="button"
              variant="outline"
            >
              {fetchModelsMutation.isPending
                ? <LoaderCircle aria-hidden="true" className="animate-spin" />
                : <List aria-hidden="true" />}
              获取模型列表
            </Button>
          </div>
          {fetchModelsMutation.data?.status === "ok" ? (
            <span className="mt-1 block text-xs text-muted-foreground">
              已获取 {fetchModelsMutation.data.models.length} 个模型
              {fetchModelsMutation.data.duration_ms !== undefined ? `（${fetchModelsMutation.data.duration_ms} ms）` : ""}
              ，选择后需点击保存配置。
            </span>
          ) : null}
          {fetchModelsMutation.data?.status === "error" ? (
            <span className="mt-1 block text-xs text-destructive">{fetchModelsMutation.data.detail || "获取模型失败"}</span>
          ) : null}
          {fetchModelsMutation.error ? (
            <span className="mt-1 block text-xs text-destructive">{fetchModelsMutation.error.message}</span>
          ) : null}
        </div>
        <label className="text-sm font-medium sm:col-span-2">
          API Base URL
          <input className={inputClass} onChange={(event) => setBaseUrl(event.target.value)} required type="url" value={baseUrl} />
        </label>
        <label className="text-sm font-medium sm:col-span-2">
          <span className="flex items-center gap-2"><KeyRound aria-hidden="true" className="size-4" />API Key</span>
          <input
            autoComplete="new-password"
            className={inputClass}
            onChange={(event) => setApiKey(event.target.value)}
            placeholder={config.effective.api_key_configured ? "留空保留已保存的 Key" : "尚未配置"}
            type="password"
            value={apiKey}
          />
          <span className="mt-1 block text-xs text-muted-foreground">查询接口不会回显 Key；留空表示保留当前值。</span>
        </label>
        <label className="text-sm font-medium">
          思考强度
          <select className={inputClass} onChange={(event) => setReasoningEffort(event.target.value as typeof reasoningEffort)} value={reasoningEffort}>
            <option value="high">High</option>
            <option value="max">Max</option>
          </select>
        </label>
        <label className="text-sm font-medium">
          上下文 Token 预算
          <input className={inputClass} min={1} onChange={(event) => setContextBudget(Number(event.target.value))} required step={1} type="number" value={contextBudget} />
          <span className="mt-1 block text-xs text-muted-foreground">当前默认模型窗口为 1,000,000 Token；此处控制用户上下文的注入上限。</span>
        </label>
      </div>

      <fieldset className="mt-6 grid gap-3 rounded-xl border bg-muted/25 p-4">
        <legend className="px-1 text-sm font-semibold">运行选项</legend>
        <Option checked={thinking} label="开启模型深度思考模式" onChange={setThinking} />
        <Option checked={telemetry} label="允许 Agno 发送运行遥测" onChange={setTelemetry} />
        <Option checked={clearApiKey} danger label="明确清空已保存的 API Key" onChange={setClearApiKey} />
      </fieldset>

      <div className="mt-6 flex flex-wrap gap-2">
        <Button disabled={pending || !dirty} type="submit"><Save aria-hidden="true" />{pending ? "正在保存" : "保存配置"}</Button>
        <Button disabled={pending} onClick={resetAll} type="button" variant="outline"><RotateCcw aria-hidden="true" />恢复已保存值</Button>
      </div>
      </fieldset>
    </form>
  );
}

function Option({
  checked,
  label,
  danger = false,
  onChange,
}: {
  checked: boolean;
  label: string;
  danger?: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className={`flex items-center gap-3 text-sm ${danger ? "text-destructive" : ""}`}>
      <input checked={checked} onChange={(event) => onChange(event.target.checked)} type="checkbox" />
      {label}
    </label>
  );
}
