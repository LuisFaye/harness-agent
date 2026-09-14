export type SessionTimeFilter = "all" | "today" | "yesterday" | "7d";

function localDateString(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function sessionDateRange(
  filter: SessionTimeFilter,
): { dateFrom?: string; dateTo?: string } {
  if (filter === "all") {
    return {};
  }
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const end = new Date(start.getTime() + 24 * 60 * 60 * 1000);
  if (filter === "today") {
    return { dateFrom: localDateString(start), dateTo: localDateString(end) };
  }
  const yesterdayStart = new Date(start.getTime() - 24 * 60 * 60 * 1000);
  if (filter === "yesterday") {
    return {
      dateFrom: localDateString(yesterdayStart),
      dateTo: localDateString(start),
    };
  }
  return {
    dateFrom: localDateString(new Date(start.getTime() - 6 * 24 * 60 * 60 * 1000)),
    dateTo: localDateString(end),
  };
}
