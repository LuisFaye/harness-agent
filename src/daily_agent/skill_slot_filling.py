from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from daily_agent.skill_catalog import SkillRecord
from daily_agent.skill_query_parsing import extract_dates


@dataclass(frozen=True)
class SlotFillingResult:
    inputs: dict[str, Any] = field(default_factory=dict)
    missing_info: list[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return not self.missing_info


class SkillSlotFiller:
    """根据 input_schema.required 做通用缺参判断。"""

    def fill(
        self,
        *,
        skill: SkillRecord,
        query: str,
        existing_inputs: dict[str, Any] | None = None,
        routed_inputs: dict[str, Any] | None = None,
        working_directory: str | None = None,
    ) -> SlotFillingResult:
        schema = skill.input_schema or {}
        if not isinstance(schema, dict):
            return SlotFillingResult(inputs=dict(existing_inputs or {}), missing_info=[])

        inputs = dict(existing_inputs or {})
        inputs.update(_clean_inputs(routed_inputs or {}))
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        required = _normalize_required(schema.get("required"))

        for name in required:
            if _has_value(inputs.get(name)):
                continue
            extracted = _extract_slot_value(
                name=name,
                meta=properties.get(name) if isinstance(properties, dict) else None,
                query=query,
                working_directory=working_directory,
            )
            if _has_value(extracted):
                inputs[name] = extracted

        missing = [name for name in required if not _has_value(inputs.get(name))]
        return SlotFillingResult(inputs=inputs, missing_info=missing)


def build_missing_info_prompt(*, skill: SkillRecord, missing_info: list[str]) -> str:
    labels = [_field_label(skill, item) for item in missing_info]
    if not labels:
        return "我还需要补充一些信息才能继续。请直接告诉我。"
    return f"要继续执行“{skill.description or skill.name}”，我还缺：{'、'.join(labels)}。请直接把这些信息告诉我。"


def _extract_slot_value(
    *,
    name: str,
    meta: Any,
    query: str,
    working_directory: str | None,
) -> Any:
    normalized_name = name.lower()
    field_type = str(meta.get("type") or "").lower() if isinstance(meta, dict) else ""
    description = str(meta.get("description") or "") if isinstance(meta, dict) else ""

    if normalized_name in {"date", "target_date", "day", "departure_date", "return_date"} or "日期" in description:
        dates = extract_dates(query, today=date.today())
        if normalized_name == "return_date" and len(dates) > 1:
            return dates[1]
        return dates[0] if dates else None

    if normalized_name in {"repo", "repos", "repository", "repositories"} or "仓库" in description:
        paths = _extract_paths(query=query, working_directory=working_directory)
        if field_type == "array" or normalized_name.endswith("s"):
            return paths
        return paths[0] if paths else None

    if field_type in {"integer", "number"}:
        return _extract_number(query)

    if field_type == "array":
        return _extract_list_after_field(name=name, query=query)

    if normalized_name in {"city", "text", "content", "message"}:
        return _extract_short_free_text(query)

    return _extract_text_after_field(name=name, query=query)


def _extract_paths(*, query: str, working_directory: str | None) -> list[str]:
    candidates: list[str] = []
    for match in re.finditer(r"(?:~|\.{1,2}|/)[^\s,，;；]+", query):
        raw_value = match.group(0).strip().strip("，,。")
        path = Path(raw_value).expanduser()
        if not path.is_absolute() and working_directory:
            path = Path(working_directory) / path
        path = path.resolve()
        if path.exists():
            candidates.append(str(path))

    if candidates:
        return _deduplicate(candidates)

    if working_directory and any(token in query for token in ["当前项目", "当前仓库", "当前目录", "这个项目", "本项目"]):
        path = Path(working_directory).resolve()
        if path.exists():
            return [str(path)]
    return []


def _extract_number(query: str) -> int | float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", query)
    if not match:
        return None
    value = match.group(0)
    if "." in value:
        return float(value)
    return int(value)


def _extract_text_after_field(*, name: str, query: str) -> str | None:
    pattern = re.compile(rf"(?:{re.escape(name)}|{re.escape(name.lower())})\s*[:：=]\s*(?P<value>[^\s,，;；]+)")
    match = pattern.search(query)
    if not match:
        return None
    value = match.group("value").strip()
    return value or None


def _extract_list_after_field(*, name: str, query: str) -> list[str]:
    text = _extract_text_after_field(name=name, query=query)
    if not text:
        return []
    return [item.strip() for item in re.split(r"[,，;；]", text) if item.strip()]


def _extract_short_free_text(query: str) -> str | None:
    normalized = query.strip().strip("，,。；;")
    if not normalized:
        return None
    if any(token in normalized for token in ["帮我", "请", "查", "查询", "看", "天气", "一下"]):
        return None
    if len(normalized) <= 20 and "\n" not in normalized:
        return normalized
    return None


def _normalize_required(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _clean_inputs(payload: dict[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in payload.items() if _has_value(value)}


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return True


def _field_label(skill: SkillRecord, name: str) -> str:
    schema = skill.input_schema or {}
    properties = schema.get("properties") if isinstance(schema, dict) else {}
    meta = properties.get(name) if isinstance(properties, dict) else None
    if isinstance(meta, dict):
        title = str(meta.get("title") or meta.get("label") or "").strip()
        if title:
            return title
        description = str(meta.get("description") or "").strip()
        if description and len(description) <= 16:
            return description
    return name


def _deduplicate(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
