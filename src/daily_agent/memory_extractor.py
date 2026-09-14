"""从用户自然语言中提取明确、稳定且适合长期保存的信息。

这里只处理用户主动表达的偏好或“请记住”内容，不猜测用户隐含习惯，也不保存
密码、Token 等敏感信息。这样虽然比“每句话都交给模型判断”保守，但不会因为一次
临时请求污染后续所有对话。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import re

from daily_agent.memory import DEFAULT_USER_ID, MySQLMemoryStore

MEMORY_KIND_PREFERENCE = "preference"
MEMORY_KIND_FACT = "fact"
MEMORY_ACTION_UPSERT = "upsert"
MEMORY_ACTION_DELETE = "delete"
_SENSITIVE_RE = re.compile(
    r"(?:密码|口令|验证码|身份证|银行卡|私钥|助记词|api[_ -]?key|access[_ -]?token|secret)",
    re.IGNORECASE,
)
_EXPLICIT_MEMORY_RE = re.compile(
    r"(?:请(?:你)?|帮我)?记住(?:一下)?(?:未来|接下来)?\s*"
    r"(?:(\d{1,4})\s*(天|小时|分钟))?[：:，,\s]+(.+)$",
    re.IGNORECASE,
)
_EXPLICIT_FORGET_RE = re.compile(r"(?:请(?:你)?|帮我)?(?:忘记|删除|清除)(?:掉)?[：:，,\s]+(.+)$")
_NAME_RE = re.compile(r"(?:我叫|请叫我|以后叫我|称呼我为)\s*([\u4e00-\u9fffA-Za-z0-9_-]{1,32})")
_SELF_IDENTITY_RE = re.compile(
    r"^我是\s*(?!(?:想|要|在|用|来|说|问|做|觉得|认为|希望|需要|可以|不是|为了|刚))"
    r"([\u4e00-\u9fffA-Za-z0-9_-]{1,32})[。！!]?$"
)
_NON_IDENTITY_VALUES = {"谁", "什么", "哪位", "哪个", "哪一个"}
_LANGUAGE_RES = (
    re.compile(r"(?:以后|之后)?(?:请)?(?:都)?(?:用|使用)(中文|英文)(?:来)?(?:回答|回复)"),
    re.compile(r"(?:回答|回复)(?:时)?(?:请)?(?:都)?(?:用|使用)(中文|英文)"),
)
_STYLE_RE = re.compile(r"(?:回答|回复).{0,12}?(简洁|详细|直接|通俗|专业|少用术语)")
_PROJECT_PATH_RE = re.compile(
    r"(?:我的)?(?:常用|默认)?项目路径(?:是|为|：|:)\s*"
    r"((?:/[A-Za-z0-9._~\-/]+)|(?:[A-Za-z]:\\[^\s，。；;]+))"
)
_TIMEZONE_RE = re.compile(r"(?:我的|默认)?时区(?:是|为|：|:)\s*([A-Za-z_+\-/0-9:]{2,64})")
_FORGET_PREFERENCE_PATTERNS = (
    (re.compile(r"(?:忘记|删除|清除)(?:掉)?(?:我的)?(?:名字|称呼)"), "preferred_name"),
    (re.compile(r"(?:忘记|删除|清除)(?:掉)?(?:我的)?(?:身份|自我介绍)"), "self_identity"),
    (re.compile(r"(?:忘记|删除|清除)(?:掉)?(?:我的)?(?:回答语言|语言偏好)"), "response_language"),
    (re.compile(r"(?:忘记|删除|清除)(?:掉)?(?:我的)?(?:回答风格|回复风格)"), "response_style"),
    (re.compile(r"(?:忘记|删除|清除)(?:掉)?(?:我的)?(?:默认项目路径|项目路径)"), "default_project_path"),
    (re.compile(r"(?:忘记|删除|清除)(?:掉)?(?:我的)?时区"), "timezone"),
)


@dataclass(frozen=True)
class ExtractedMemory:
    key: str | None
    value: str
    memory_text: str = ""
    aliases: dict[str, str] = field(default_factory=dict)
    kind: str = MEMORY_KIND_FACT
    action: str = MEMORY_ACTION_UPSERT
    expires_at: datetime | None = None


class RuleBasedMemoryExtractor:
    """只提取高置信度通用信息，不内置任何业务领域规则。"""

    def extract(self, text: str) -> list[ExtractedMemory]:
        normalized = " ".join(str(text or "").split()).strip()
        if not normalized or _SENSITIVE_RE.search(normalized):
            return []

        items: list[ExtractedMemory] = []
        for pattern, key in _FORGET_PREFERENCE_PATTERNS:
            if pattern.search(normalized):
                items.append(
                    ExtractedMemory(
                        key=key,
                        value="",
                        kind=MEMORY_KIND_PREFERENCE,
                        action=MEMORY_ACTION_DELETE,
                    )
                )

        explicit_forget_match = _EXPLICIT_FORGET_RE.search(normalized)
        if explicit_forget_match:
            memory_text = _clean_explicit_memory(explicit_forget_match.group(1))
            if memory_text:
                items.append(
                    ExtractedMemory(
                        key=None,
                        value=memory_text,
                        memory_text=memory_text,
                        kind=MEMORY_KIND_FACT,
                        action=MEMORY_ACTION_DELETE,
                    )
                )

        if items:
            return _deduplicate(items)

        name_match = _NAME_RE.search(normalized)
        if name_match:
            items.append(_preference("preferred_name", name_match.group(1)))

        # “我是……”是用户对自身身份的直接陈述，需要跨会话保留。这里只接受整句且
        # 身份值不包含空格或标点的短表达，并排除疑问词，避免把“我是想问……”或
        # “我是谁”这类临时话语误存为身份并覆盖用户之前已经确认的信息。
        identity_match = _SELF_IDENTITY_RE.fullmatch(normalized)
        if identity_match and identity_match.group(1) not in _NON_IDENTITY_VALUES:
            items.append(_preference("self_identity", identity_match.group(1)))

        language = _first_match_value(_LANGUAGE_RES, normalized)
        if language:
            items.append(_preference("response_language", language))

        style_match = _STYLE_RE.search(normalized)
        if style_match:
            items.append(_preference("response_style", style_match.group(1)))

        path_match = _PROJECT_PATH_RE.search(normalized)
        if path_match:
            items.append(_preference("default_project_path", path_match.group(1)))

        timezone_match = _TIMEZONE_RE.search(normalized)
        if timezone_match:
            items.append(_preference("timezone", timezone_match.group(1)))

        # 明确的“请记住：……”允许保存无法结构化的稳定事实。若这句话已经被识别为
        # 姓名、语言等结构化偏好，就只保存偏好，避免同一信息重复占用上下文。
        explicit_match = _EXPLICIT_MEMORY_RE.search(normalized)
        if explicit_match and not items:
            memory_text = _clean_explicit_memory(explicit_match.group(3))
            if memory_text:
                items.append(
                    ExtractedMemory(
                        key=None,
                        value=memory_text,
                        memory_text=memory_text,
                        kind=MEMORY_KIND_FACT,
                        expires_at=_build_expiry(explicit_match.group(1), explicit_match.group(2)),
                    )
                )
        return _deduplicate(items)


def persist_extracted_memories(
    text: str,
    user_id: str = DEFAULT_USER_ID,
    store: MySQLMemoryStore | None = None,
) -> list[ExtractedMemory]:
    """提取并落库；结构化偏好使用 upsert，长期事实使用精确去重。"""
    extracted = RuleBasedMemoryExtractor().extract(text)
    if not extracted:
        return []

    memory_store = store or MySQLMemoryStore()
    for item in extracted:
        if item.action == MEMORY_ACTION_DELETE:
            if item.key:
                memory_store.delete_preference(item.key, user_id=user_id)
            elif item.memory_text:
                _delete_matching_memories(memory_store, item.memory_text, user_id)
            continue
        if item.key:
            memory_store.set_preference(item.key, item.value, user_id=user_id)
            for alias_key, alias_value in item.aliases.items():
                memory_store.set_preference(alias_key, alias_value, user_id=user_id)
        if item.memory_text:
            memory_store.add_memory(
                item.memory_text,
                user_id=user_id,
                source="auto",
                expires_at=item.expires_at,
            )
    return extracted


def _preference(key: str, value: str) -> ExtractedMemory:
    return ExtractedMemory(
        key=key,
        value=value.strip(),
        kind=MEMORY_KIND_PREFERENCE,
    )


def _first_match_value(patterns: tuple[re.Pattern[str], ...], text: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
    return None


def _clean_explicit_memory(value: str) -> str:
    cleaned = value.strip(" ，,。；;：:")
    if not cleaned or len(cleaned) > 300 or _SENSITIVE_RE.search(cleaned):
        return ""
    if cleaned in {"这个", "这件事", "这些", "它", "上面的内容"}:
        return ""
    return cleaned


def _build_expiry(amount: str | None, unit: str | None) -> datetime | None:
    if not amount or not unit:
        return None
    value = int(amount)
    if unit == "天":
        delta = timedelta(days=value)
    elif unit == "小时":
        delta = timedelta(hours=value)
    else:
        delta = timedelta(minutes=value)
    return datetime.now(timezone.utc).replace(tzinfo=None) + delta


def _delete_matching_memories(store: MySQLMemoryStore, text: str, user_id: str) -> None:
    for record in store.list_memories(user_id=user_id, limit=200):
        if record.memory_text.strip() == text.strip():
            store.delete_memory(record.id, user_id=user_id)


def _deduplicate(items: list[ExtractedMemory]) -> list[ExtractedMemory]:
    result: list[ExtractedMemory] = []
    seen: set[tuple[str | None, str, str]] = set()
    for item in items:
        identity = (item.key, item.value, item.memory_text)
        if identity not in seen:
            seen.add(identity)
            result.append(item)
    return result
