import { PageHeader } from "@/components/layout/page-header";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, LoaderCircle, RefreshCw, Settings2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  getModelConfig,
  saveModelConfig,
  testModelConnection,
  type ModelConfigWritePayload,
} from "@/lib/api/management";

import { ModelConfigForm } from "./model-config-form";
import { SettingsStatusPanel } from "./settings-status-panel";

export function SettingsPage() {
  const queryClient = useQueryClient();
  const configQuery = useQuery({ queryKey: ["model-config"], queryFn: getModelConfig });
  const saveMutation = useMutation({
    mutationFn: saveModelConfig,
    onSuccess: async (payload) => {
      queryClient.setQueryData(["model-config"], payload);
      await queryClient.invalidateQueries({ queryKey: ["model-config"] });
    },
  });
  const testMutation = useMutation({ mutationFn: testModelConnection });

  function save(payload: ModelConfigWritePayload) {
    testMutation.reset();
    return saveMutation.mutateAsync(payload);
  }

  return (
    <section aria-labelledby="settings-title" className="mx-auto w-full max-w-6xl">
      <PageHeader title="设置" description="管理模型连接与运行参数，让 Agent 按你的方式工作。" icon={Settings2} id="settings-title">
        <div className="flex flex-wrap gap-2">
          <Button
            disabled={testMutation.isPending || !configQuery.data}
            onClick={() => testMutation.mutate()}
            variant="outline"
          >
            {testMutation.isPending
              ? <LoaderCircle aria-hidden="true" className="animate-spin" />
              : <FlaskConical aria-hidden="true" />}
            测试已保存连接
          </Button>
          <Button disabled={configQuery.isFetching} onClick={() => configQuery.refetch()} variant="outline">
            <RefreshCw aria-hidden="true" className={configQuery.isFetching ? "animate-spin" : ""} />刷新
          </Button>
        </div>
      </PageHeader>

      {saveMutation.isSuccess ? (
        <div className="mt-5 rounded-lg border border-success/25 bg-success/5 px-4 py-3 text-sm text-success" role="status">模型配置已保存并重新读取。</div>
      ) : null}
      {saveMutation.error ? (
        <div className="mt-5 rounded-lg border border-destructive/25 bg-destructive/5 px-4 py-3 text-sm text-destructive" role="alert">{saveMutation.error.message}</div>
      ) : null}
      {testMutation.data ? (
        <div className={`mt-5 rounded-lg border px-4 py-3 text-sm ${testMutation.data.status === "ok" ? "border-success/25 bg-success/5 text-success" : "border-destructive/25 bg-destructive/5 text-destructive"}`} role="status">
          {testMutation.data.detail || testMutation.data.message || testMutation.data.error || `连接状态：${testMutation.data.status}`}
          {testMutation.data.duration_ms !== undefined ? `（${testMutation.data.duration_ms} ms）` : ""}
        </div>
      ) : null}
      {testMutation.error ? (
        <div className="mt-5 rounded-lg border border-destructive/25 bg-destructive/5 px-4 py-3 text-sm text-destructive" role="alert">{testMutation.error.message}</div>
      ) : null}

      {configQuery.isLoading ? (
        <div className="mt-6 grid min-h-72 place-items-center rounded-xl border bg-card text-sm text-muted-foreground" role="status">
          <span className="inline-flex items-center gap-2"><LoaderCircle aria-hidden="true" className="size-4 animate-spin" />正在读取模型配置</span>
        </div>
      ) : configQuery.error ? (
        <div className="mt-6 rounded-xl border border-destructive/25 bg-destructive/5 p-5 text-sm text-destructive" role="alert">{configQuery.error.message}</div>
      ) : configQuery.data ? (
        <div className="mt-6 grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_21rem]">
          <ModelConfigForm config={configQuery.data} onSave={save} pending={saveMutation.isPending} />
          <SettingsStatusPanel config={configQuery.data} />
        </div>
      ) : (
        <div className="mt-6 grid min-h-72 place-items-center rounded-xl border border-dashed bg-card text-sm text-muted-foreground">
          <span className="inline-flex items-center gap-2"><Settings2 aria-hidden="true" />没有可展示的模型配置</span>
        </div>
      )}
    </section>
  );
}
