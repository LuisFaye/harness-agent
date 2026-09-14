const STREAM_VERSION = "2.0";

const STREAM_EVENT_TYPES = new Set([
  "run_started",
  "status_updated",
  "trace_updated",
  "tool_started",
  "tool_finished",
  "response_started",
  "response_delta",
  "response_block",
  "response_completed",
  "run_waiting",
  "run_failed",
  "run_cancel_requested",
  "run_cancelled",
  "run_completed",
]);

export function normalizeStreamEvent(event, cursor = { runId: "", lastSequence: 0 }) {
  if (event?.version !== STREAM_VERSION) {
    return { legacy: true, ignored: false, event, cursor };
  }

  const runId = String(event.run_id || "").trim();
  const sequence = Number(event.sequence);
  const type = String(event.type || "").trim();
  const payloadIsObject = event.payload && typeof event.payload === "object" && !Array.isArray(event.payload);
  if (!runId || !Number.isInteger(sequence) || sequence < 1 || !payloadIsObject) {
    throw new Error("流式事件格式不正确：run_id、sequence 或 payload 无效。");
  }
  if (!STREAM_EVENT_TYPES.has(type)) {
    throw new Error(`流式事件类型不受支持：${type || "空"}。`);
  }

  const currentRunId = String(cursor?.runId || "").trim();
  const lastSequence = Number(cursor?.lastSequence || 0);
  if (currentRunId && currentRunId !== runId) {
    throw new Error("收到其他运行的流式事件，已停止渲染。");
  }
  if (sequence <= lastSequence) {
    return { legacy: false, ignored: true, event: null, cursor };
  }
  if (sequence > lastSequence + 1) {
    throw new Error(`流式事件缺少第 ${lastSequence + 1} 段，请稍后从运行监控恢复。`);
  }

  return {
    legacy: false,
    ignored: false,
    event: { type, runId, sequence, payload: event.payload },
    cursor: { runId, lastSequence: sequence },
  };
}
