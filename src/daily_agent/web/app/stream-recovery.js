export async function recoverPersistedEvents({
  runId,
  userId,
  cursor,
  initialPayload = null,
  fetchPage,
  consumeEvent,
  wait,
  maxAttempts = 30,
}) {
  let finalPayload = initialPayload;
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    const page = await fetchPage({
      runId,
      userId,
      afterSequence: cursor.lastSequence,
      limit: 200,
    });
    for (const event of page.events || []) {
      finalPayload = consumeEvent(event, finalPayload, cursor);
      if (finalPayload) {
        return finalPayload;
      }
    }
    if (page.has_more) {
      continue;
    }
    await wait(500);
  }
  throw new Error("连接已断开，暂时无法自动恢复。任务状态可以在运行监控中查看。");
}
