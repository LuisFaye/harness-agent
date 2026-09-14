const FAVORITE_SESSIONS_STORAGE_PREFIX = "sb-agent-favorite-sessions:v2:";

export function readFavoriteSessionIds(userId: string): string[] {
  if (typeof window === "undefined") {
    return [];
  }
  try {
    const raw = window.localStorage.getItem(storageKey(userId));
    if (!raw) {
      return [];
    }
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed.filter(
      (item): item is string => typeof item === "string" && item.trim().length > 0,
    );
  } catch {
    return [];
  }
}

export function writeFavoriteSessionIds(userId: string, sessionIds: string[]): boolean {
  try {
    window.localStorage.setItem(
      storageKey(userId),
      JSON.stringify(sessionIds),
    );
    return true;
  } catch {
    return false;
  }
}

function storageKey(userId: string): string {
  return `${FAVORITE_SESSIONS_STORAGE_PREFIX}${userId}`;
}
