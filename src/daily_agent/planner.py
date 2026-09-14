"""基于规则的通用任务规划（第一版，不调用 LLM）。

职责
----
在用户提问后、执行工具前，生成 TaskPlan：
- 识别通用意图
- 给出不绑定领域工具的兜底步骤
- 保留 missing_info / risk_level 字段，兼容 API、CLI 和历史记录结构

输出形式
--------
- to_instructions() -> 追加到 Agno Agent 的 instructions，引导模型理解任务结构
- to_metadata() / to_markdown() -> CLI plan 命令、任务历史 metadata

局限
----
这是没有命中可执行 skill 时的兜底计划，不负责选择具体领域工具。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlanStep:
    """计划中的单步：人类可读名称 + 可选工具名 + 原因说明。"""

    name: str
    tool: str | None
    reason: str


@dataclass(frozen=True)
class TaskPlan:
    """一次用户请求的结构化计划。"""

    intent: str
    steps: list[PlanStep]
    missing_info: list[str]
    risk_level: str  # low | medium | high

    def to_metadata(self) -> dict:
        """序列化进 task_history.metadata，便于事后排查。"""
        return {
            "intent": self.intent,
            "steps": [step.__dict__ for step in self.steps],
            "missing_info": self.missing_info,
            "risk_level": self.risk_level,
        }

    def to_instructions(self) -> str:
        """转成中文说明块，由 build_agent 追加到模型 instructions。"""
        lines = [
            "任务计划：",
            f"- 识别意图：{self.intent}",
            f"- 风险等级：{self.risk_level}",
        ]
        if self.missing_info:
            lines.append("- 缺少信息：" + "、".join(self.missing_info))
            lines.append("- 如果缺少信息会影响工具调用，先向用户确认，不要猜。")
        if self.steps:
            lines.append("建议步骤：")
            for index, step in enumerate(self.steps, 1):
                tool = step.tool or "无需工具"
                lines.append(f"{index}. {step.name}，工具：{tool}，原因：{step.reason}")
        return "\n".join(lines)

    def to_markdown(self, request: str | None = None, title: str = "任务计划") -> str:
        """把计划渲染成更适合保存和阅读的 Markdown 文档。"""
        lines = [
            f"# {title}",
            "",
            f"**意图：** {self.intent}",
            f"**风险等级：** {self.risk_level}",
        ]
        if request:
            lines.extend(
                [
                    "",
                    "## 原始需求",
                    request,
                ]
            )
        if self.missing_info:
            lines.append(f"**缺少信息：** {'、'.join(self.missing_info)}")
        else:
            lines.append("**缺少信息：** 无")
        lines.extend(["", "## 建议步骤"])
        for index, step in enumerate(self.steps, 1):
            tool = step.tool or "无需工具"
            lines.extend(
                [
                    f"{index}. {step.name}",
                    f"   - 工具：{tool}",
                    f"   - 原因：{step.reason}",
                ]
            )
        return "\n".join(lines)


class RuleBasedPlanner:
    """生成不绑定领域工具的通用兜底计划。"""

    def plan(self, query: str) -> TaskPlan:
        stripped = query.strip()
        steps = [
            PlanStep(
                name="理解用户目标",
                tool=None,
                reason="先确认用户真正要完成什么，以及是否需要外部工具或补充信息。",
            ),
            PlanStep(
                name="选择可用能力并执行",
                tool=None,
                reason="如果已启用技能或工具能完成任务，交给主 Agent 自主调用；否则直接说明限制。",
            ),
            PlanStep(
                name="总结结果和下一步",
                tool=None,
                reason="把已知信息整理成用户能直接使用的中文回复。",
            )
        ]

        return TaskPlan(
            intent="general" if stripped else "empty",
            steps=steps,
            missing_info=[],
            risk_level="low",
        )
