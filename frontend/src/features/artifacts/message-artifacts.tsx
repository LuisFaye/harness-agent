import type { Artifact } from "@/lib/api/schemas";

import { ArtifactCard } from "./artifact-card";

interface MessageArtifactsProps {
  artifacts: Artifact[];
}

export function MessageArtifacts({ artifacts }: MessageArtifactsProps) {
  if (!artifacts.length) {
    return null;
  }
  return (
    <section aria-label="本轮产物" className="mt-3">
      <ul className="artifact-grid grid gap-3">
        {artifacts.map((item) => (
          <ArtifactCard artifact={item} key={item.artifact_id} />
        ))}
      </ul>
    </section>
  );
}

export function artifactsForRun(artifacts: Artifact[], runId: string | null | undefined): Artifact[] {
  if (!runId) {
    return [];
  }
  return artifacts
    .filter((item) => item.run_id === runId)
    .slice()
    .sort((left, right) => left.created_at.localeCompare(right.created_at));
}
