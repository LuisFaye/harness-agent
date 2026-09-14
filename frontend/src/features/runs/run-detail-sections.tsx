import { AlertTriangle, Database, Wrench } from "lucide-react";

import type { RunDetail } from "@/lib/api/schemas";
import type { WorkingState } from "@/lib/stream/schemas";

import { formatDuration, isRecord, textValue } from "./run-format";

export function WorkingStateDetails({ state }: { state: WorkingState | null }) {
  if (!state) {
    return null;
  }
  return (
    <section aria-labelledby="working-state-detail-title" className="run-detail-section">
      <h2 id="working-state-detail-title">任务状态</h2>
      <dl className="grid gap-3 sm:grid-cols-2">
        <StateItem label="目标" value={state.goal_summary || "暂无"} />
        <StateItem label="当前动作" value={state.current_action || "暂无"} />
        <StateItem label="阻塞" value={state.blocker || "无"} />
        <StateItem label="下一步" value={state.next_action || "已结束"} />
      </dl>
      {state.completed_steps.length ? (
        <div className="mt-4">
          <h3 className="text-sm font-semibold">已完成步骤</h3>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-muted-foreground">
            {state.completed_steps.map((step) => <li key={step}>{step}</li>)}
          </ul>
        </div>
      ) : null}
      <EvidenceList items={state.evidence_summaries} />
    </section>
  );
}

function StateItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border bg-muted/30 p-3">
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-sm">{value}</dd>
    </div>
  );
}

function EvidenceList({ items }: { items: WorkingState["evidence_summaries"] }) {
  if (!items.length) {
    return null;
  }
  return (
    <div className="mt-5">
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        <Database aria-hidden="true" className="size-4 text-primary" />
        运行证据
      </h3>
      <div className="mt-2 grid gap-2">
        {items.map((item, index) => (
          <article className="rounded-lg border p-3" key={`${item.evidence_id || item.tool}-${index}`}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <strong className="text-sm">{item.tool || "执行结果"}</strong>
              <span className="text-xs text-muted-foreground">{item.status}</span>
            </div>
            <p className="mt-1 text-sm text-muted-foreground">{item.summary}</p>
            {item.evidence_id ? (
              <code className="mt-2 block truncate text-xs text-muted-foreground">
                {item.evidence_id}
              </code>
            ) : null}
          </article>
        ))}
      </div>
    </div>
  );
}

export function PlanSection({ plan }: { plan: Record<string, unknown> | null }) {
  const steps = Array.isArray(plan?.steps) ? plan.steps.filter(isRecord) : [];
  if (!steps.length) {
    return null;
  }
  return (
    <section aria-labelledby="run-plan-title" className="run-detail-section">
      <h2 id="run-plan-title">执行计划</h2>
      <ol className="mt-3 grid gap-2">
        {steps.map((step, index) => (
          <li className="flex gap-3 rounded-lg border p-3" key={`${textValue(step.name)}-${index}`}>
            <span className="grid size-7 shrink-0 place-items-center rounded-full bg-accent text-xs font-semibold text-accent-foreground">
              {index + 1}
            </span>
            <div className="min-w-0">
              <strong className="text-sm">{textValue(step.name) || `步骤 ${index + 1}`}</strong>
              {textValue(step.reason) ? (
                <p className="mt-1 text-sm text-muted-foreground">{textValue(step.reason)}</p>
              ) : null}
              {textValue(step.tool) ? (
                <span className="mt-1 inline-flex rounded bg-secondary px-1.5 py-0.5 text-xs">
                  Tool · {textValue(step.tool)}
                </span>
              ) : null}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function ObservationsSection({ items }: { items: Record<string, unknown>[] }) {
  if (!items.length) {
    return null;
  }
  return (
    <section aria-labelledby="run-observations-title" className="run-detail-section">
      <h2 className="flex items-center gap-2" id="run-observations-title">
        <Wrench aria-hidden="true" className="size-4 text-primary" />
        Tool 调用
        <span className="text-xs font-normal text-muted-foreground">{items.length}</span>
      </h2>
      <div className="mt-3 grid gap-2">
        {items.map((item, index) => {
          const error = textValue(item.error_message || item.error);
          const summary = textValue(item.output_summary || item.summary || item.output);
          return (
            <article className="rounded-lg border p-3" key={`${textValue(item.tool || item.name)}-${index}`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <strong className="text-sm">
                  {textValue(item.name || item.tool) || `Tool ${index + 1}`}
                </strong>
                <span className="text-xs text-muted-foreground">
                  {textValue(item.status) || "未知"}
                  {typeof item.duration_ms === "number" ? ` · ${formatDuration(item.duration_ms)}` : ""}
                </span>
              </div>
              {error ? (
                <p className="mt-2 text-sm text-destructive">{error}</p>
              ) : summary ? (
                <p className="mt-2 line-clamp-4 whitespace-pre-wrap text-sm text-muted-foreground">
                  {summary}
                </p>
              ) : null}
              {typeof item.attempts === "number" && item.attempts > 1 ? (
                <p className="mt-2 text-xs text-warning">共尝试 {item.attempts} 次</p>
              ) : null}
            </article>
          );
        })}
      </div>
    </section>
  );
}

export function PromptSnapshotSection({ snapshot }: { snapshot: Record<string, unknown> | null }) {
  if (!snapshot) {
    return null;
  }
  const tools = stringArray(snapshot.tool_names);
  const skills = stringArray(snapshot.skill_names);
  const notes = stringArray(snapshot.notes);
  const promptText = textValue(snapshot.prompt_text);
  return (
    <details className="run-detail-section group">
      <summary className="cursor-pointer list-none font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        开发者详情 · Prompt 快照
      </summary>
      <div className="mt-4 grid gap-4">
        <dl className="grid gap-3 sm:grid-cols-2">
          <StateItem label="模型" value={textValue(snapshot.model_id) || "未记录"} />
          <StateItem
            label="上下文预算"
            value={textValue(snapshot.context_token_budget) || "未记录"}
          />
        </dl>
        {tools.length ? <TagList label="启用 Tool" values={tools} /> : null}
        {skills.length ? <TagList label="启用 Skill" values={skills} /> : null}
        {notes.length ? (
          <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
            {notes.map((note) => <li key={note}>{note}</li>)}
          </ul>
        ) : null}
        {promptText ? (
          <details>
            <summary className="cursor-pointer text-sm font-medium">查看脱敏 Prompt 正文</summary>
            <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap rounded-lg border bg-muted p-3 text-xs">
              {promptText}
            </pre>
          </details>
        ) : null}
      </div>
    </details>
  );
}

function TagList({ label, values }: { label: string; values: string[] }) {
  return (
    <div>
      <h3 className="text-sm font-semibold">{label}</h3>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {values.map((value) => (
          <span className="rounded-md bg-secondary px-2 py-1 text-xs" key={value}>{value}</span>
        ))}
      </div>
    </div>
  );
}

export function RunErrorSection({ detail }: { detail: RunDetail }) {
  const failed = detail.status === "error" || detail.status === "max_iterations";
  const cancelled = detail.status === "cancelled" || detail.status === "cancel_requested";
  if (!detail.error_message && !detail.error_stage && !failed && !cancelled) {
    return null;
  }
  return (
    <section
      aria-labelledby="run-error-title"
      className={failed
        ? "run-detail-section border-destructive/30 bg-destructive/5"
        : "run-detail-section bg-muted/30"}
      role={failed ? "alert" : "status"}
    >
      <h2 className="flex items-center gap-2" id="run-error-title">
        <AlertTriangle
          aria-hidden="true"
          className={failed ? "size-4 text-destructive" : "size-4 text-muted-foreground"}
        />
        {failed ? "停止与错误" : "停止信息"}
      </h2>
      <dl className="mt-3 grid gap-2 text-sm">
        <div><dt className="inline font-medium">阶段：</dt><dd className="inline">{detail.error_stage || "未记录"}</dd></div>
        <div><dt className="inline font-medium">原因：</dt><dd className="inline">{detail.stop_reason || "未记录"}</dd></div>
        {detail.error_message ? (
          <div><dt className="inline font-medium">错误：</dt><dd className="inline">{detail.error_message}</dd></div>
        ) : null}
      </dl>
    </section>
  );
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}
