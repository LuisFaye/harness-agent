import { FileText } from "lucide-react";

import type { Artifact } from "@/lib/api/schemas";
import { ArtifactCard } from "@/features/artifacts/artifact-card";

interface RunArtifactsSectionProps {
  artifacts: Artifact[];
}

/**
 * 运行详情里的“运行产物”区块。
 *
 * 无产物时整体不渲染；卡片复用对话页同一套预览/下载交互。
 */
export function RunArtifactsSection({ artifacts }: RunArtifactsSectionProps) {
  if (!artifacts.length) {
    return null;
  }
  return (
    <section aria-labelledby="run-artifacts-title" className="run-detail-section">
      <h2 className="flex items-center gap-2" id="run-artifacts-title">
        <FileText aria-hidden="true" className="size-4 text-primary" />
        运行产物
        <span className="text-xs font-normal text-muted-foreground">{artifacts.length}</span>
      </h2>
      <ul className="mt-3 grid gap-2">
        {artifacts.map((item) => (
          <ArtifactCard artifact={item} key={item.artifact_id} />
        ))}
      </ul>
    </section>
  );
}
