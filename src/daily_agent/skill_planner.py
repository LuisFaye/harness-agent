from __future__ import annotations

from daily_agent.planner import PlanStep, TaskPlan
from daily_agent.skill_catalog import SkillCatalog, SkillRecord
from daily_agent.skill_router import SkillRouter
from daily_agent.skill_slot_filling import SkillSlotFiller


class SkillPlanner:
    """基于已启用 skill 生成结构化计划。"""

    def __init__(
        self,
        catalog: SkillCatalog | None = None,
        router: SkillRouter | None = None,
        slot_filler: SkillSlotFiller | None = None,
        working_directory: str | None = None,
    ) -> None:
        self.catalog = catalog or SkillCatalog()
        self.router = router or SkillRouter()
        self.slot_filler = slot_filler or SkillSlotFiller()
        self.working_directory = working_directory or "."

    def plan(self, query: str) -> TaskPlan | None:
        candidates = [item for item in self.catalog.enabled_skill_records() if item.execution]
        if not candidates:
            return None

        command_match = self._match_command(query, candidates)
        if command_match:
            return self._build_skill_plan(command_match, query)

        decision = self.router.route(query=query, candidates=candidates)
        if not decision:
            return None
        skill = self.catalog.get_skill(decision.skill_name)
        if not skill or skill.status != "enabled" or skill.error_message or not skill.execution:
            return None
        return self._build_skill_plan(skill, query)

    def _build_skill_plan(self, skill: SkillRecord, query: str) -> TaskPlan:
        slot_result = self.slot_filler.fill(
            skill=skill,
            query=query,
            existing_inputs={},
            routed_inputs={},
            working_directory=self.working_directory,
        )
        return TaskPlan(
            intent=f"skill:{skill.name}",
            steps=[
                PlanStep(
                    name=f"执行技能 {skill.name}",
                    tool=skill.tool_name or skill.name,
                    reason=skill.description or "命中技能路由后，交给对应 handler 处理。",
                )
            ],
            missing_info=[_field_label(skill, item) for item in slot_result.missing_info],
            risk_level=_infer_skill_risk_level(skill, query),
        )

    def _match_command(self, query: str, candidates: list[SkillRecord]) -> SkillRecord | None:
        stripped = query.strip()
        if not stripped.startswith("/"):
            return None
        command = stripped.split()[0].lower()
        for skill in candidates:
            for trigger in skill.triggers:
                if trigger.startswith("/") and trigger.lower() == command:
                    return skill
        return None


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


def _infer_skill_risk_level(skill: SkillRecord, query: str) -> str:
    if skill.requires_confirmation:
        return "medium"
    return "low"
