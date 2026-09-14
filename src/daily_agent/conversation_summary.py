"""会话历史的规则型压缩（不调用 LLM）。

使用场景
--------
1. MySQLContextBuilder：组装上下文时，对「滑动窗口」之外的旧消息做现场摘要；
2. MySQLMemoryStore.refresh_conversation_summary：每次 ask 成功后把摘要持久化到
   conversation_session_summaries，下次直接读取，避免重复计算。

压缩策略
--------
- 按 role 分成用户/助手两组，各取最近若干条 snippet（去重、截断）；
- 输出 1～2 行中文，例如「前文用户提到：…」「前文助手结论：…」；
- 不做语义理解，因此成本低、可预测，但无法保留复杂推理细节。

与「删消息」的区别：conversation_messages 表仍保留全文，只是给模型看时用摘要代替。
"""

from __future__ import annotations

import re

from daily_agent.memory_support import summarize_text_for_title


def format_conversation_message(item) -> str:
    """把单条 DB 消息格式化为「用户: …」/「助手: …」一行，供最近会话窗口使用。"""
    label = "用户" if item.role == "user" else "助手" if item.role == "assistant" else item.role
    return f"{label}: {compact_text(item.content, 220)}"


def compress_conversation_history(messages) -> list[str]:
    """把一批历史消息压成少量摘要行。

    messages: ConversationMessage 列表，通常已是时间正序、且不含「最近窗口」内的消息。
    返回: 0～2 行字符串列表，由 context 或 memory 用换行拼成 summary_text。
    """
    if not messages:
        return []

    user_snippets: list[str] = []
    assistant_snippets: list[str] = []
    for item in messages:
        snippet = compact_text(item.content, 90)
        if item.role == "user":
            user_snippets.append(snippet)
        elif item.role == "assistant":
            assistant_snippets.append(snippet)

    summary: list[str] = []
    if user_snippets:
        summary.append(f"前文用户提到：{join_tail_unique(user_snippets, 3)}")
    if assistant_snippets:
        summary.append(f"前文助手结论：{join_tail_unique(assistant_snippets, 3)}")
    if not summary:
        summary.append(f"前文共有 {len(messages)} 条消息，已自动压缩。")
    return summary


def compact_text(text: str, max_chars: int) -> str:
    """空白归一化后按字符截断，用于控制单条 snippet 长度。"""
    return " ".join(text.split())[:max_chars]


def join_tail_unique(items: list[str], limit: int) -> str:
    """从列表尾部取最多 limit 条去重 snippet，用分号连接（保持时间顺序）。"""
    kept: list[str] = []
    seen: set[str] = set()
    for item in reversed(items):
        normalized = " ".join(item.split())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        kept.append(normalized)
        if len(kept) >= limit:
            break
    return "；".join(reversed(kept))


# 无独立主题的短跟进语：首个有效主题产生前，遇到这类内容时继续向后查找；
# 一旦命中有效主题，后续普通追问不会改变标题。
_FOLLOW_UP_PHRASES = frozenset(
    {
        "好",
        "好的",
        "你好",
        "您好",
        "可以",
        "嗯",
        "嗯嗯",
        "哦",
        "哦哦",
        "行",
        "行吧",
        "没问题",
        "是的",
        "对",
        "对的",
        "确认",
        "知道了",
        "收到",
        "明白",
        "明白了",
        "继续",
        "继续吧",
        "请继续",
        "好的继续",
        "可以继续",
        "继续执行",
        "重试",
        "重试一下",
        "再来一次",
        "ok",
        "okay",
    }
)

# Markdown 标题 / 列表符号：只去掉行首的标记符本身，保留后面的正文。
_MARKDOWN_LEADING_PATTERN = re.compile(r"^(?:#{1,6}\s+|[-*+]\s+|\d{1,3}[.、)]\s+)")

# 常见对话式前缀：只做有界剥离（最多 3 轮），避免误删正常正文。
_CONVERSATION_PREFIX_PATTERN = re.compile(
    r"^(?:你好[，,、\s]+|您好[，,、\s]+)?"
    r"(?:请\s*帮我|请\s*帮忙|请\s*麻烦你|帮我一下|帮帮忙|麻烦你|请你|请问|帮我|帮忙)?"
)

_TITLE_PUNCTUATION = " \t\n\r，,。.；;：:！!？?、…—"

# 句子结束符：中文句号/感叹号/问号与中英文感叹号、问号直接作为句末；
# 英文句号仅在后面是空白/换行/字符串结尾时视为句末，避免把数字小数点
# 或域名中的点当作句界；标题只保留第一句。
_SENTENCE_END_PATTERN = re.compile(r"[。！？!?]|\.(?=\s|$)")

# 首句本身过短、只有社交意图时，直接显示原文没有区分度。这里仅匹配完整短句，
# 避免把「你好，帮我排查问题」误归类成寒暄；普通任务仍走下面的标题清理逻辑。
_SEMANTIC_TITLE_RULES = (
    (re.compile(r"^(?:你|您)?好(?:呀|啊|哇)?$|^(?:hi|hello|hey)$", re.IGNORECASE), "用户打招呼"),
    (re.compile(r"^(?:谢谢|多谢|感谢)(?:你|您)?(?:了|啦|啊)?$", re.IGNORECASE), "用户表示感谢"),
    (re.compile(r"^(?:再见|拜拜|回头见|bye|goodbye)$", re.IGNORECASE), "用户结束对话"),
    (re.compile(r"^(?:你是谁|你叫什么|介绍一下你自己)$", re.IGNORECASE), "了解助手身份"),
    (re.compile(r"^(?:你能做什么|你会做什么|你有哪些能力|你有什么功能)$", re.IGNORECASE), "了解助手能力"),
)


def _first_sentence(text: str) -> str:
    """提取清洗后文本的第一句话，作为会话标题候选。

    句界规则：
    - 优先按最早出现的中文句号、感叹号、问号或中英文感叹号、问号截取第一句话；
    - 英文句号只在后面是空白/换行/字符串结尾时视为句末，避免数字小数点
      （如 1.2.3）和域名（如 example.com）中的点被误判为句界；
    - 若当前行没有句末符号，则把换行也视作句界，只取第一行；
    - 去掉句末符号本身并归一化空白，避免多行/多句正文拼进标题。
    返回空字符串表示当前文本没有可用句子。
    """
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _SENTENCE_END_PATTERN.search(line)
        if match:
            first_sentence = line[: match.start()].strip()
            if first_sentence:
                return " ".join(first_sentence.split())
        else:
            return " ".join(line.split())
    return ""


def _clean_title_text(text: str) -> str:
    """保守清理消息正文，只保留第一句话，产出适合做标题的单行文本。"""
    lines: list[str] = []
    in_fence = False
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(_MARKDOWN_LEADING_PATTERN.sub("", line))
    text = "\n".join(lines)
    for _ in range(3):
        cleaned = _CONVERSATION_PREFIX_PATTERN.sub("", text, count=1)
        if cleaned == text:
            break
        text = cleaned
    return _first_sentence(text)


def _is_follow_up(text: str) -> bool:
    """判断清理后的消息是否只是无独立主题的短跟进。"""
    normalized = text.strip(_TITLE_PUNCTUATION).lower()
    return normalized in _FOLLOW_UP_PHRASES


def _semantic_title(text: str) -> str:
    """把完整的短社交句归纳为有区分度的会话标题。"""
    normalized = " ".join(str(text or "").split()).strip(_TITLE_PUNCTUATION)
    for pattern, title in _SEMANTIC_TITLE_RULES:
        if pattern.fullmatch(normalized):
            return title
    return ""


def summarize_session_title(user_messages: list[str], fallback: str = "", max_chars: int = 36) -> str:
    """按时间正序从 user 消息中归纳会话侧栏标题。

    user_messages: 该会话的 user 消息正文，按时间正序（最早在前）传入；
    只取第一个有效文本作为标题：跳过空白或纯寒暄/确认词后继续向后查找，
    一旦命中有效主题就立即返回，保证标题由首次有效用户文本决定；
    有效文本会先做 Markdown/代码围栏/对话前缀清理，再只保留第一句话
    （以最早出现的句末符号为界：中文句号、感叹号、问号及中英文感叹号、问号
    直接生效，英文句号仅在后面是空白/换行/结尾时生效，必要时换行也视为句界）；
    fallback: 没有任何有效 user 消息时的兜底文本（例如最后一条消息或 session_id）；
    max_chars: 标题长度上限，默认与 summarize_text_for_title 保持一致。

    返回: 单行短标题；长度截断和空值兜底仍复用 summarize_text_for_title。
    """
    for message in user_messages:
        cleaned = _clean_title_text(message)
        semantic_title = _semantic_title(cleaned)
        if semantic_title:
            return semantic_title
        if not cleaned or _is_follow_up(cleaned):
            continue
        return summarize_text_for_title(cleaned, max_chars=max_chars)
    return summarize_text_for_title(fallback, max_chars=max_chars)
