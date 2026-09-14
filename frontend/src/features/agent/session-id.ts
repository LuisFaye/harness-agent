export function createSessionId(): string {
  const suffix = typeof crypto.randomUUID === "function"
    ? crypto.randomUUID().slice(0, 8)
    : Math.random().toString(36).slice(2, 10);
  return `session-${Date.now()}-${suffix}`;
}
