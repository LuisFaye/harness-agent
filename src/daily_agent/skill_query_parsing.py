from __future__ import annotations

import re
from datetime import date, timedelta

ISO_DATE_RE = re.compile(r"20\d{2}-\d{1,2}-\d{1,2}")
CHINESE_DATE_RE = re.compile(r"(20\d{2})年(\d{1,2})月(\d{1,2})日?")
WEEKDAY_MAP = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


def extract_dates(query: str, today: date | None = None) -> list[str]:
    current_day = today or date.today()
    dates: list[str] = []
    for match in ISO_DATE_RE.finditer(query):
        _append_date(dates, _normalize_iso_date(match.group(0)))
    for match in CHINESE_DATE_RE.finditer(query):
        year, month, day = match.groups()
        _append_date(dates, date(int(year), int(month), int(day)).isoformat())

    if "今天" in query:
        _append_date(dates, current_day.isoformat())
    if "明天" in query:
        _append_date(dates, (current_day + timedelta(days=1)).isoformat())
    if "后天" in query:
        _append_date(dates, (current_day + timedelta(days=2)).isoformat())

    for match in re.finditer(r"下周([一二三四五六日天])", query):
        next_monday = current_day + timedelta(days=7 - current_day.weekday())
        _append_date(dates, (next_monday + timedelta(days=WEEKDAY_MAP[match.group(1)])).isoformat())
    for match in re.finditer(r"(?<!下)周([一二三四五六日天])", query):
        _append_date(dates, _next_weekday(current_day, WEEKDAY_MAP[match.group(1)]).isoformat())
    return dates


def contains_any(text: str, needles: list[str]) -> bool:
    return any(needle in text for needle in needles)


def _append_date(dates: list[str], value: str) -> None:
    if value not in dates:
        dates.append(value)


def _normalize_iso_date(value: str) -> str:
    year, month, day = value.split("-")
    return date(int(year), int(month), int(day)).isoformat()


def _next_weekday(today: date, weekday: int) -> date:
    delta = (weekday - today.weekday()) % 7
    return today + timedelta(days=delta or 7)
