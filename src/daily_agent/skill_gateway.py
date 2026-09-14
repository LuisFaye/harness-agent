"""技能入口分流层（确定性快捷路径）。

自从主链路改成 Agent 工具调用循环后，「自然语言意图 -> 选哪个 skill」这件事
交给模型在工具循环里自主判断。这里只保留两类确定性的快捷入口：
1. 如果当前会话里有未完成的多轮 skill，就继续该 skill；
2. 命令式触发，如 `/daily-report`，给用户一个明确、可预期的入口。

其余自然语言请求一律返回 None，交回 AskService 走 Agent loop 主链路。
真正的参数解析、缺参追问、确认、执行，都交给各自 skill handler。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from daily_agent.skill_catalog import SkillCatalog, SkillRecord
from daily_agent.skill_session import SkillSessionState, SkillSessionStore

# 文档型 skill 注入上下文时的正文上限，避免把超长 SKILL.md 整篇塞进 prompt。
_DOCUMENT_SKILL_CONTEXT_CHARS = 8000


@dataclass(frozen=True)
class SkillRouteMatch:
    skill: SkillRecord
    session: SkillSessionState | None = None
    trigger: str | None = None
    is_continuation: bool = False
    route_reason: str | None = None
    route_type: str | None = None
    routed_inputs: dict | None = None


class SkillGateway:
    """只挑出确定性命中的 skill：未完成会话续接，或命令式触发。"""

    def __init__(
        self,
        catalog: SkillCatalog | None = None,
        session_store: SkillSessionStore | None = None,
    ) -> None:
        self.catalog = catalog or SkillCatalog()
        self.session_store = session_store or SkillSessionStore()

    def resolve(self, *, query: str, user_id: str, session_id: str) -> SkillRouteMatch | None:
        pending = self.session_store.get(user_id=user_id, session_id=session_id)
        if pending:
            skill = self.catalog.get_skill(pending.skill_name)
            if skill and skill.status == "enabled" and not skill.error_message and skill.execution:
                return SkillRouteMatch(skill=skill, session=pending, is_continuation=True)
            self.session_store.clear(user_id=user_id, session_id=session_id)

        candidates = [item for item in self.catalog.enabled_skill_records() if item.execution]
        if not candidates:
            return None

        return self._match_command(query, candidates)

    def document_skill_context(self, query: str) -> str | None:
        """若用户用 /技能名 点名文档型 skill，把 SKILL.md 指引注入 Agent 上下文。

        文档型 skill 没有可执行入口，不会走命令式 SkillRuntime；
        但仍应在用户显式选择后被识别并加载说明，而不是当成普通闲聊文本。
        """
        command = _leading_slash_command(query)
        if not command:
            return None
        for skill in self.catalog.enabled_skill_records():
            if skill.execution:
                continue
            if command not in _command_aliases(skill):
                continue
            body = _read_skill_markdown(skill.path)
            if not body:
                return (
                    f"用户通过命令 {command} 指定了技能「{skill.name}」。"
                    f"请严格按该技能的设计意图处理后续请求：{skill.description or skill.name}"
                )
            return (
                f"用户通过命令 {command} 指定了技能「{skill.name}」。"
                "请严格遵循下方技能说明完成请求，不要忽略这些约束：\n\n"
                f"{body}"
            )
        return None

    def _match_command(self, query: str, candidates: list[SkillRecord]) -> SkillRouteMatch | None:
        command = _leading_slash_command(query)
        if not command:
            return None
        for skill in candidates:
            aliases = _command_aliases(skill)
            if command in aliases:
                trigger = next((item for item in skill.triggers if item.lower() == command), command)
                return SkillRouteMatch(
                    skill=skill,
                    trigger=trigger,
                    route_reason="命令触发",
                    route_type="command",
                )
        return None


def _leading_slash_command(query: str) -> str | None:
    """取出首词斜杠命令，例如 `/frontend-design 做个首页` -> `/frontend-design`。"""
    stripped = query.strip()
    if not stripped.startswith("/"):
        return None
    return stripped.split()[0].lower()


def _command_aliases(skill: SkillRecord) -> set[str]:
    """汇总可命中该技能的斜杠命令别名。

    除了 skill.json 里显式声明的 /trigger，还支持用 /技能名 点名，
    这样导入的第三方技能（往往只有 name、没有斜杠触发词）也能被 / 菜单插入后识别。
    """
    aliases = {f"/{skill.name.strip().lower()}"}
    for trigger in skill.triggers:
        text = str(trigger or "").strip()
        if text.startswith("/"):
            aliases.add(text.lower())
    tool_name = str(skill.tool_name or "").strip()
    if tool_name:
        normalized = tool_name.lower().replace("_", "-")
        aliases.add(f"/{normalized}")
        aliases.add(f"/{tool_name.lower()}")
    return {item for item in aliases if item and item != "/"}


def _read_skill_markdown(skill_path: str) -> str:
    """读取技能目录下的 SKILL.md，超长时截断，避免撑爆上下文。"""
    path = Path(skill_path).expanduser() / "SKILL.md"
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if len(text) <= _DOCUMENT_SKILL_CONTEXT_CHARS:
        return text
    return text[:_DOCUMENT_SKILL_CONTEXT_CHARS].rstrip() + "\n\n…（技能说明已截断）"
