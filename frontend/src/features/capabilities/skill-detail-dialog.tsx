import { FileCode2, FolderKey, LoaderCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { SkillDetailResponse, SkillSummary } from "@/lib/api/management-schemas";

import { compatibilityLabel, formatFileSize, riskLabel } from "./capability-format";

interface SkillDetailDialogProps {
  skill: SkillSummary | null;
  detail: SkillDetailResponse | undefined;
  loading: boolean;
  error: string | null;
  pending: boolean;
  onAction: (action: "enable" | "disable" | "adopt" | "delete", skill: SkillSummary) => void;
  onOpenChange: (open: boolean) => void;
}

export function SkillDetailDialog({
  skill,
  detail,
  loading,
  error,
  pending,
  onAction,
  onOpenChange,
}: SkillDetailDialogProps) {
  return (
    <Dialog onOpenChange={onOpenChange} open={Boolean(skill)}>
      <DialogContent className="max-h-[calc(100svh-2rem)] overflow-y-auto sm:max-w-3xl">
        {skill ? (
          <>
            <DialogHeader className="pr-8">
              <DialogTitle>{skill.name}</DialogTitle>
              <DialogDescription>{skill.description || "暂无说明"}</DialogDescription>
            </DialogHeader>
            <div className="grid gap-3 rounded-xl border bg-muted/30 p-4 sm:grid-cols-2">
              <Meta label="来源" value={skill.source} />
              <Meta label="兼容性" value={compatibilityLabel(skill.compatibility.status)} />
              <Meta label="执行方式" value={skill.executable ? skill.interaction_mode : "不可执行"} />
              <Meta label="风险" value={`${riskLabel(skill.risk_level)}${skill.requires_confirmation ? " · 执行前确认" : ""}`} />
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                disabled={pending}
                onClick={() => onAction(skill.enabled ? "disable" : "enable", skill)}
                size="sm"
              >
                {skill.enabled ? "停用 Skill" : "启用 Skill"}
              </Button>
              {skill.can_adopt ? (
                <Button disabled={pending} onClick={() => onAction("adopt", skill)} size="sm" variant="outline">迁移接管</Button>
              ) : null}
              {skill.can_remove ? (
                <Button disabled={pending} onClick={() => onAction("delete", skill)} size="sm" variant="destructive">删除 Skill</Button>
              ) : null}
            </div>

            {skill.unavailable_reason || skill.error_message ? (
              <div className="rounded-lg border border-warning/25 bg-warning/5 p-3 text-sm text-warning">
                {skill.unavailable_reason || skill.error_message}
              </div>
            ) : null}

            <section>
              <h3 className="flex items-center gap-2 font-semibold"><FolderKey aria-hidden="true" className="size-4" />安全内部文件</h3>
              <p className="mt-1 text-xs text-muted-foreground">接口只读取约定目录并对常见密钥做脱敏。</p>
              {loading ? (
                <div className="mt-3 flex min-h-24 items-center justify-center gap-2 text-sm text-muted-foreground" role="status">
                  <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />正在读取
                </div>
              ) : error ? (
                <div className="mt-3 rounded-lg border border-destructive/25 p-3 text-sm text-destructive" role="alert">{error}</div>
              ) : detail?.files.length ? (
                <div className="mt-3 space-y-2">
                  {detail.files.map((file) => (
                    <details className="overflow-hidden rounded-lg border bg-card" key={file.path}>
                      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2">
                        <span className="flex min-w-0 items-center gap-2">
                          <FileCode2 aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" />
                          <strong className="truncate text-xs">{file.path}</strong>
                        </span>
                        <span className="shrink-0 text-xs text-muted-foreground">{formatFileSize(file.size_bytes)}</span>
                      </summary>
                      {file.content !== null ? (
                        <pre className="max-h-80 overflow-auto border-t bg-muted/40 p-3 text-xs whitespace-pre-wrap"><code>{file.content}</code></pre>
                      ) : (
                        <p className="border-t p-3 text-xs text-muted-foreground">二进制资源只展示名称和大小。</p>
                      )}
                    </details>
                  ))}
                </div>
              ) : (
                <p className="mt-3 rounded-lg border border-dashed p-4 text-sm text-muted-foreground">没有可展示的约定文件。</p>
              )}
            </section>
          </>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return <div><span className="block text-xs text-muted-foreground">{label}</span><strong className="mt-1 block text-sm">{value}</strong></div>;
}
