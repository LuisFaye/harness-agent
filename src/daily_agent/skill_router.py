from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from daily_agent.config import load_settings
from daily_agent.skill_catalog import SkillRecord


@dataclass(frozen=True)
class SkillRouteDecision:
    skill_name: str
    confidence: float
    reason: str
    route_type: str = "llm"
    inputs: dict[str, Any] = field(default_factory=dict)


SkillSelector = Callable[[str, list[dict[str, Any]]], SkillRouteDecision | None]

_GENERIC_MATCH_TOKENS = {
    "帮我",
    "请帮",
    "麻烦",
    "一下",
    "看看",
    "看下",
    "帮忙",
    "查询",
    "整理",
}


class SkillRouter:
    """把已启用的 skill 元数据转成模型可选择的候选工具。"""

    def __init__(self, selector: SkillSelector | None = None, min_confidence: float = 0.22) -> None:
        self.selector = selector or DashScopeSkillSelector()
        self.min_confidence = min_confidence

    def route(self, *, query: str, candidates: list[SkillRecord]) -> SkillRouteDecision | None:
        tool_candidates = [skill_to_candidate(item) for item in candidates]
        if not tool_candidates:
            return None

        decision = self.selector(query, tool_candidates)
        if decision and _is_known_skill(decision.skill_name, candidates) and decision.confidence >= self.min_confidence:
            return decision

        fallback = LocalSkillSelector()(query, tool_candidates)
        if fallback and _is_known_skill(fallback.skill_name, candidates) and fallback.confidence >= self.min_confidence:
            return fallback
        return None


class DashScopeSkillSelector:
    """通过 OpenAI 兼容接口让模型选择 skill；失败时返回 None，由本地兜底接管。"""

    def __init__(self, timeout_seconds: float = 8.0) -> None:
        self.timeout_seconds = timeout_seconds

    def __call__(self, query: str, candidates: list[dict[str, Any]]) -> SkillRouteDecision | None:
        enabled = os.getenv("SB_AGENT_ENABLE_LLM_SKILL_ROUTER") or os.getenv("DAILY_AGENT_ENABLE_LLM_SKILL_ROUTER", "")
        if enabled.strip().lower() not in {"1", "true", "yes"}:
            return None
        settings = load_settings()
        if not settings.model_api_key:
            return None
        try:
            from openai import OpenAI
        except ImportError:
            return None

        prompt = {
            "user_query": query,
            "candidate_skills": candidates,
            "instructions": (
                "只在用户请求明显适合某个 skill 时选择它。"
                "主要依据 description 和 input_schema，triggers/examples 只能作为辅助。"
                "如果没有合适 skill，返回 skill_name=null。"
            ),
            "output_schema": {
                "skill_name": "string|null",
                "confidence": "0到1之间的数字",
                "reason": "简短中文原因",
                "inputs": "从用户话里能明确提取出的参数对象，不能猜",
            },
        }
        try:
            client = OpenAI(
                api_key=settings.model_api_key,
                base_url=settings.model_base_url,
                timeout=self.timeout_seconds,
            )
            response = client.chat.completions.create(
                model=settings.model_id,
                messages=[
                    {"role": "system", "content": "你是 skill router，只输出 JSON。"},
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )
            content = response.choices[0].message.content or "{}"
            payload = json.loads(content)
        except Exception:
            return None

        skill_name = payload.get("skill_name")
        if not skill_name:
            return None
        return SkillRouteDecision(
            skill_name=str(skill_name),
            confidence=_to_float(payload.get("confidence"), default=0.0),
            reason=str(payload.get("reason") or "模型选择 skill"),
            route_type="llm",
            inputs=dict(payload.get("inputs") or {}),
        )


class LocalSkillSelector:
    """无模型或模型失败时的保守兜底，避免基础用例不可用。"""

    def __call__(self, query: str, candidates: list[dict[str, Any]]) -> SkillRouteDecision | None:
        normalized = query.lower()
        best: tuple[float, dict[str, Any], str] | None = None
        for candidate in candidates:
            score, reason = _score_candidate(normalized, candidate)
            if score <= 0:
                continue
            if best is None or score > best[0]:
                best = (score, candidate, reason)
        if best is None:
            return None
        score, candidate, reason = best
        return SkillRouteDecision(
            skill_name=str(candidate["name"]),
            confidence=min(0.9, score),
            reason=reason,
            route_type="local",
            inputs={},
        )


def skill_to_candidate(skill: SkillRecord) -> dict[str, Any]:
    return {
        "name": skill.name,
        "tool_name": skill.tool_name or skill.name,
        "description": skill.description,
        "input_schema": skill.input_schema or {"type": "object", "properties": {}},
        "requires_confirmation": skill.requires_confirmation,
        "triggers": list(skill.triggers),
        "intent_examples": list(skill.intent_examples),
    }


def _score_candidate(query: str, candidate: dict[str, Any]) -> tuple[float, str]:
    score = 0.0
    matched: list[str] = []
    trigger_matches: list[str] = []
    for trigger in candidate.get("triggers", []):
        text = str(trigger or "").strip()
        if text and not text.startswith("/") and not _is_generic_match_token(text) and text.lower() in query:
            score += 0.35
            matched.append(text)
            trigger_matches.append(text)
    for text in [candidate.get("description"), *candidate.get("intent_examples", [])]:
        for token in _tokens(str(text or "")):
            if token in query:
                score += 0.22
                matched.append(token)
    schema = candidate.get("input_schema") or {}
    if isinstance(schema, dict):
        properties = schema.get("properties") or {}
        if isinstance(properties, dict):
            for name, meta in properties.items():
                if str(name).lower() in query:
                    score += 0.08
                    matched.append(str(name))
                if isinstance(meta, dict):
                    description = str(meta.get("description") or "")
                    for token in _tokens(description):
                        if token in query:
                            score += 0.03
                            matched.append(token)
    if not matched:
        return 0.0, ""
    reason_matches = trigger_matches or matched
    return score, "本地匹配：" + "、".join(_deduplicate(reason_matches)[:4])


def _tokens(text: str) -> list[str]:
    normalized = text.lower()
    words = re.findall(r"[a-zA-Z0-9_-]{2,}", normalized)
    chinese = re.findall(r"[\u4e00-\u9fff]{2,}", normalized)
    tokens = words
    result: list[str] = []
    for token in tokens:
        if len(token) <= 12:
            result.append(token)
            continue
        result.extend(token[index : index + 4] for index in range(0, len(token) - 3, 2))
    for token in chinese:
        result.append(token)
        if len(token) > 2:
            result.extend(token[index : index + 2] for index in range(0, len(token) - 1))
        if len(token) > 4:
            result.extend(token[index : index + 4] for index in range(0, len(token) - 3, 2))
    return [item for item in _deduplicate(result) if not _is_generic_match_token(item)]


def _is_generic_match_token(token: str) -> bool:
    normalized = token.strip().lower()
    if normalized in _GENERIC_MATCH_TOKENS:
        return True
    if re.fullmatch(r"[\u4e00-\u9fff]{1,2}", normalized) and normalized in {"帮我", "请", "查", "看", "做", "给我"}:
        return True
    return False


def _deduplicate(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _is_known_skill(skill_name: str, candidates: list[SkillRecord]) -> bool:
    return any(item.name == skill_name for item in candidates)


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
