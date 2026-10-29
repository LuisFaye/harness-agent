import { useEffect, useMemo } from "react";
import type { RunProjection } from "@/lib/stream/run-projection";

export function useSessionRunConnections(userId: string) {
  const connections = useMemo(() => {
    const controllers = new Map<string, AbortController>();
    const projections = new Map<string, RunProjection>();
    return {
      controller: (sessionId: string) => controllers.get(sessionId) ?? null,
      projection: (sessionId: string) => projections.get(sessionId) ?? null,
      start(sessionId: string, controller: AbortController) {
        controllers.get(sessionId)?.abort();
        controllers.set(sessionId, controller);
        projections.delete(sessionId);
      },
      publish(sessionId: string, projection: RunProjection) {
        projections.set(sessionId, projection);
        if (projections.size > 30) {
          const oldest = [...projections.keys()].find((key) => key !== sessionId && !controllers.has(key));
          if (oldest) projections.delete(oldest);
        }
      },
      owns: (sessionId: string, controller: AbortController) => controllers.get(sessionId) === controller,
      finish(sessionId: string, controller: AbortController) {
        if (controllers.get(sessionId) === controller) controllers.delete(sessionId);
      },
      dispose() {
        controllers.forEach((controller) => controller.abort());
        controllers.clear();
        projections.clear();
      },
    };
  }, [userId]);
  useEffect(() => () => connections.dispose(), [connections]);
  return connections;
}
