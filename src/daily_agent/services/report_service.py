"""日报生成服务。

当前实现复用了本机已经存在的 daily-report skill 脚本来扫描 git 提交，
然后在 Python 里做一层轻量汇总，输出适合直接发送或继续加工的日报文本。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from daily_agent.errors import DailyAgentError
from daily_agent.paths import AppPaths
from daily_agent.skill_catalog import SkillCatalog

DEFAULT_SKILL_NAME = "daily-report"


@dataclass(frozen=True)
class DailyReportResult:
    """日报生成结果。

    - report_text: 最终给人看的日报正文
    - trace: 追踪信息，保留扫描原始结果，方便前端展示和排查
    """

    report_text: str
    trace: dict[str, Any]


class DailyReportService:
    """基于本地 daily-report skill 生成工作日报。"""

    def __init__(self, scan_script: Path | None = None, *, paths: AppPaths | None = None) -> None:
        self.paths = paths or AppPaths.discover()
        self.scan_script = scan_script or _resolve_daily_report_scan_script(paths=self.paths)

    def generate(
        self,
        *,
        target_date: date,
        repos: list[str],
        author: str | None = None,
        extra_notes: list[str] | None = None,
    ) -> DailyReportResult:
        """扫描指定日期的提交记录，并生成一段可直接使用的日报文本。"""
        payload = self.scan(target_date=target_date, repos=repos, author=author)
        report_text = self.render_report(payload=payload, extra_notes=extra_notes or [])
        trace = {
            "scan": payload,
            "extra_notes": extra_notes or [],
        }
        return DailyReportResult(report_text=report_text, trace=trace)

    def scan(
        self,
        *,
        target_date: date,
        repos: list[str],
        author: str | None = None,
    ) -> dict[str, Any]:
        """公开扫描入口，供 ask 技能链路先扫再追问附加工作。"""
        if not self.scan_script.exists():
            raise DailyAgentError(f"日报扫描脚本不存在：{self.scan_script}")
        if not repos:
            raise DailyAgentError("生成日报至少需要一个 git 仓库路径。")
        return self._scan_commits(target_date=target_date, repos=repos, author=author)

    def render_report(self, *, payload: dict[str, Any], extra_notes: list[str] | None = None) -> str:
        """根据已有扫描结果渲染最终日报正文。"""
        return self._build_report_text(payload, extra_notes or [])

    def _scan_commits(self, *, target_date: date, repos: list[str], author: str | None = None) -> dict[str, Any]:
        """调用外部扫描脚本，拿到结构化提交数据。

        这里不直接在本仓库重写 git 扫描逻辑，而是复用用户已经在用的 skill 脚本，
        这样日报口径能和原有工作流保持一致，也减少重复维护。
        """
        cmd = [
            "python3",
            str(self.scan_script),
            "--repos",
            ",".join(repos),
            "--date",
            target_date.isoformat(),
            "--format",
            "json",
        ]
        if author:
            cmd.extend(["--author", author])

        try:
            # 使用 subprocess 而不是 import 外部脚本，是为了把依赖边界控制得更清楚：
            # 这个服务只关心“给参数、拿 JSON 输出”，不依赖脚本内部实现细节。
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except Exception as exc:
            raise DailyAgentError(f"执行日报扫描脚本失败：{exc}") from exc

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise DailyAgentError(f"日报扫描失败：{detail or '未知错误'}")

        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise DailyAgentError("日报扫描输出不是合法 JSON。") from exc

    def _build_report_text(self, payload: dict[str, Any], extra_notes: list[str]) -> str:
        """把扫描结果整理成偏口语化的日报正文。

        这里故意采用可预测的规则拼装，而不是再调用一次大模型总结。
        原因很直接：
        1. 日报是高频动作，规则化生成更稳定，也没有额外模型成本；
        2. 前端和定时任务都需要确定性输出，便于排查“为什么今天这样写”；
        3. 真要更像人写的总结，后续可以在这个结果之上再叠加 LLM 美化。
        """
        repositories = payload.get("repositories", [])
        total_commits = int(payload.get("total_commits", 0))

        work_items: list[str] = []
        if total_commits > 0:
            repo_names = [repo.get("name") or Path(repo.get("path", "")).name or "未知仓库" for repo in repositories]
            repo_names = [name for name in repo_names if name]
            repo_part = "、".join(repo_names[:3])
            if len(repo_names) > 3:
                repo_part += "等仓库"
            work_items.append(
                f"梳理并汇总 {payload.get('scan_metadata', {}).get('target_date', '')} 的代码提交，覆盖{repo_part or '相关仓库'}的 {total_commits} 条变更，形成可追溯的日报基础数据"
            )

            commit_subjects = self._collect_subjects(repositories)
            if commit_subjects:
                summary = "；".join(commit_subjects[:2])
                work_items.append(f"围绕核心开发事项推进工作，包括{summary}，沉淀了当日主要研发进展")

        for note in extra_notes[:2]:
            clean = " ".join(note.split()).strip("；。 ")
            if clean:
                work_items.append(f"{clean}，补充了代码提交之外的工作产出")

        if not work_items:
            work_items.append("完成当日工作记录整理，暂未扫描到代码提交，已保留后续补充空间")

        return "今日" + "；".join(work_items[:4]) + "。"

    def _collect_subjects(self, repositories: list[dict[str, Any]]) -> list[str]:
        """收集去重后的提交标题，给日报正文提供几个核心事项。"""
        subjects: list[str] = []
        seen: set[str] = set()
        for repo in repositories:
            for commit in repo.get("commits", []):
                subject = " ".join(str(commit.get("subject", "")).split())
                if not subject or subject in seen:
                    continue
                seen.add(subject)
                subjects.append(subject)
                if len(subjects) >= 4:
                    return subjects
        return subjects


def _resolve_daily_report_scan_script(*, paths: AppPaths | None = None) -> Path:
    """优先使用受管 skill 目录里的脚本，而不是写死外部路径。

    这样不管是“迁移接管”后的 imported skill，还是后续重新导入的 daily-report，
    服务层都能跟着 skill 注册表走，不会再出现页面上的 skill 和运行时脚本不是同一份的问题。
    """
    resolved_paths = paths or AppPaths.discover()
    skill = SkillCatalog(paths=resolved_paths).get_skill(DEFAULT_SKILL_NAME)
    if skill:
        candidate = Path(skill.path) / "scripts" / "scan_commits.py"
        if candidate.exists():
            return candidate
    return resolved_paths.imported_skills_home / DEFAULT_SKILL_NAME / "scripts" / "scan_commits.py"
