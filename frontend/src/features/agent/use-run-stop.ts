import { useCallback, useLayoutEffect, useMemo, useRef, useState } from "react";

import { cancelRun } from "@/lib/api/agent";
import { isTerminalProjection } from "@/lib/stream/client";
import type { RunProjection } from "@/lib/stream/run-projection";

interface StopState {
  requested: boolean;
  submitted: boolean;
  runId: string;
  error: string | null;
}

export function useRunStop(userId: string, sessionId: string, onError: (message: string) => void) {
  const [isStopping, setIsStopping] = useState(false);
  const states = useMemo(() => new Map<string, StopState>(), [userId]);
  const currentSessionRef = useRef(sessionId);
  const getState = useCallback(() => {
    let state = states.get(sessionId);
    if (!state) {
      state = { requested: false, submitted: false, runId: "", error: null };
      states.set(sessionId, state);
    }
    return state;
  }, [sessionId, states]);

  useLayoutEffect(() => {
    currentSessionRef.current = sessionId;
    setIsStopping(states.get(sessionId)?.requested ?? false);
  }, [sessionId, states]);

  const resetStop = useCallback(() => {
    states.delete(sessionId);
    if (currentSessionRef.current === sessionId) setIsStopping(false);
  }, [sessionId, states]);

  const submitStop = useCallback(async () => {
    const state = getState();
    if (!state.requested || !state.runId || state.submitted) return;
    state.submitted = true;
    try {
      await cancelRun(userId, state.runId);
    } catch (error) {
      if (states.get(sessionId) !== state) return;
      state.requested = false;
      state.submitted = false;
      state.error = error instanceof Error ? error.message : "停止请求提交失败，请重试。";
      if (currentSessionRef.current === sessionId) {
        setIsStopping(false);
        onError(state.error);
      }
    }
  }, [getState, onError, sessionId, states, userId]);

  const observeStop = useCallback((projection: RunProjection) => {
    if (isTerminalProjection(projection)) {
      resetStop();
      return;
    }
    const state = getState();
    state.runId = projection.runId || state.runId;
    if (projection.status === "cancel_requested") {
      state.requested = true;
      state.submitted = true;
    }
    if (currentSessionRef.current === sessionId) {
      setIsStopping(state.requested);
      if (state.error) { onError(state.error); state.error = null; }
    }
    if (state.requested) void submitStop();
  }, [getState, onError, resetStop, sessionId, submitStop]);

  const stopRun = useCallback(async () => {
    const state = getState();
    if (state.requested) return;
    state.requested = true;
    state.error = null;
    setIsStopping(true);
    await submitStop();
  }, [getState, submitStop]);

  return { isStopping, stopRun, observeStop, resetStop };
}
