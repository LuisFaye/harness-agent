"""第三方 Skill 与 MCP 内容的静态安全审查。

这层审查只处理高置信度的投毒信号，不把它当作完整恶意代码分析器：
- Skill 在导入前和每次装载时都扫描，防止导入后目录被替换；
- MCP 在同步能力元数据时逐项标记，命中项不会注册给模型；
- MCP Tool、Resource、Prompt 的动态返回在进入模型上下文前再次检查。

审查失败采用默认拒绝。原因会使用稳定、可展示的中文文本返回，避免把可疑原文
再次拼进提示词或日志。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from daily_agent.errors import DailyAgentError

_MAX_SKILL_FILE_BYTES = 512 * 1024
_MAX_SKILL_TOTAL_BYTES = 2 * 1024 * 1024
_SCANNED_ROOT_FILES = {"SKILL.md", "skill.json", "manifest.json", ".skill.json"}
_SCANNED_DIRECTORIES = {"scripts", "references"}
_SCANNED_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml", ".py", ".js", ".mjs", ".ts", ".sh"}


@dataclass(frozen=True)
class SecurityFinding:
    code: str
    message: str
    location: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "location": self.location}


@dataclass(frozen=True)
class SecurityReview:
    status: str
    findings: tuple[SecurityFinding, ...] = ()

    @property
    def blocked(self) -> bool:
        return self.status == "blocked"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "findings": [item.to_dict() for item in self.findings],
        }


_CONTENT_RULES: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "instruction_override",
        "包含要求忽略或覆盖上级指令的内容。",
        re.compile(
            r"(?:ignore|disregard|override|forget)\s+(?:all\s+)?"
            r"(?:previous|prior)(?:\s+(?:system|developer))?\s+"
            r"(?:instructions?|prompts?|rules?)|"
            r"(?:ignore|disregard|override|forget)\s+(?:the\s+)?(?:system|developer)\s+"
            r"(?:instructions?|prompts?|rules?)|"
            r"(?:忽略|无视|覆盖|绕过).{0,24}(?:系统|开发者|先前|上级).{0,12}(?:指令|提示词|规则)",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "prompt_exposure",
        "包含索取或泄露系统提示词、内部指令的内容。",
        re.compile(
            r"(?:reveal|show|print|return|expose|leak).{0,30}(?:system|developer).{0,12}(?:prompt|instructions?)|"
            r"(?:显示|输出|返回|泄露|窃取).{0,24}(?:系统|开发者|内部).{0,12}(?:提示词|指令)",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "hidden_action",
        "包含要求向用户隐瞒行为或绕过确认的内容。",
        re.compile(
            r"(?:do\s+not|don't|never)\s+(?:tell|inform|notify)\s+(?:the\s+)?user|"
            r"(?:without|skip|bypass).{0,20}(?:consent|confirmation|approval)|"
            r"(?:不要|禁止).{0,12}(?:告诉|通知|告知)用户|(?:绕过|跳过).{0,12}(?:确认|授权|审批)",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "destructive_execution",
        "包含高危破坏或下载后直接执行的命令。",
        re.compile(
            r"rm\s+-rf\s+(?:/|~|\$HOME)(?:\s|$)|"
            r"(?:curl|wget)\b[^\n|]{0,300}\|\s*(?:ba)?sh\b|"
            r"(?:nc|netcat)\b[^\n]{0,100}\s+-e\s+",
            re.IGNORECASE,
        ),
    ),
)

_SECRET_TARGET = re.compile(
    r"(?:api[_ -]?key|access[_ -]?token|password|cookie|\.env|\.ssh|id_rsa|system prompt|"
    r"密钥|令牌|密码|凭据|系统提示词)",
    re.IGNORECASE,
)
_EXFILTRATION_ACTION = re.compile(
    r"(?:exfiltrat|upload|send|post|webhook|curl|wget|requests?\.(?:post|put)|"
    r"上传|外传|发送到|回传)",
    re.IGNORECASE,
)
# 网页搜索结果天然会混有密钥/发送等技术词，组合规则假阳性极高，整类能力不做安全审查。
_WEB_SEARCH_MARKERS = ("web_search", "search_web", "mcp_web_search")


def is_web_search_capability(name: str) -> bool:
    """判断名称是否属于网页搜索能力（工具名、审查 source、服务端标记均可）。"""
    text = str(name or "").lower()
    return any(marker in text for marker in _WEB_SEARCH_MARKERS)


def review_skill_directory(folder: Path) -> SecurityReview:
    """扫描会进入提示词或执行链的 Skill 文本文件，命中高危项时整体隔离。"""
    root = folder.expanduser().resolve()
    findings: list[SecurityFinding] = []
    total_bytes = 0
    if not root.is_dir():
        return SecurityReview(
            status="blocked",
            findings=(SecurityFinding("invalid_directory", "Skill 目录不存在或不是目录。", str(root)),),
        )

    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            findings.append(SecurityFinding("symlink", "Skill 包含符号链接，无法确认真实读取边界。", relative))
            continue
        if not path.is_file() or not _should_scan_skill_file(path, root):
            continue
        size = path.stat().st_size
        total_bytes += size
        if size > _MAX_SKILL_FILE_BYTES:
            findings.append(SecurityFinding("oversized_file", "Skill 单个文本文件超过安全审查上限。", relative))
            continue
        if total_bytes > _MAX_SKILL_TOTAL_BYTES:
            findings.append(SecurityFinding("oversized_package", "Skill 待审查文本总量超过安全上限。", relative))
            break
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            findings.append(SecurityFinding("unreadable_text", "Skill 文本文件无法按 UTF-8 安全读取。", relative))
            continue
        findings.extend(_review_text(text, location=relative))

    return SecurityReview(status="blocked" if findings else "approved", findings=tuple(_deduplicate(findings)))


def review_mcp_capabilities(
    capabilities: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, list[dict[str, Any]]], SecurityReview]:
    """审查 MCP 声明的 Tool、Resource、Prompt 元数据，并标记不可注册项。"""
    reviewed: dict[str, list[dict[str, Any]]] = {"tools": [], "resources": [], "prompts": []}
    all_findings: list[SecurityFinding] = []
    for kind in reviewed:
        for index, raw in enumerate(capabilities.get(kind) or []):
            item = dict(raw) if isinstance(raw, dict) else {"value": raw}
            name = str(item.get("name") or item.get("uri") or index)
            location = f"mcp.{kind}.{name}"
            # web_search 元数据与返回摘要都不做隔离，避免搜索结果被误拦。
            if is_web_search_capability(name) or is_web_search_capability(location):
                item["security_status"] = "approved"
                item["security_findings"] = []
                reviewed[kind].append(item)
                continue
            text = json.dumps(item, ensure_ascii=False, default=str)
            findings = _review_text(text, location=location)
            item["security_status"] = "blocked" if findings else "approved"
            item["security_findings"] = [finding.message for finding in findings]
            reviewed[kind].append(item)
            all_findings.extend(findings)
    review = SecurityReview(
        status="blocked" if all_findings else "approved",
        findings=tuple(_deduplicate(all_findings)),
    )
    return reviewed, review


def guard_mcp_payload(payload: Any, *, source: str) -> Any:
    """阻止 MCP 动态返回中的高置信度投毒文本进入模型上下文。"""
    # 网页搜索正文不进安全审查：结果页常同时出现 token/send 等词，会稳定误报。
    if is_web_search_capability(source):
        return payload
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(payload)
    findings = _review_text(text, location=source)
    if findings:
        codes = "、".join(sorted({item.code for item in findings}))
        raise DailyAgentError(f"MCP 返回内容未通过安全审查，已阻止进入模型上下文（{codes}）。")
    return payload


def security_block_message(review: SecurityReview, *, subject: str) -> str:
    """生成不回显可疑原文的稳定隔离原因。"""
    reasons = "；".join(item.message for item in review.findings[:3])
    return f"{subject}安全审查未通过，已隔离。{reasons}"


def _review_text(text: str, *, location: str) -> list[SecurityFinding]:
    findings: list[SecurityFinding] = []
    normalized = str(text or "")[:1_000_000]
    for code, message, pattern in _CONTENT_RULES:
        if pattern.search(normalized):
            findings.append(SecurityFinding(code, message, location))
    if _SECRET_TARGET.search(normalized) and _EXFILTRATION_ACTION.search(normalized):
        findings.append(SecurityFinding("secret_exfiltration", "包含收集并外传密钥或凭据的组合行为。", location))
    return findings


def _should_scan_skill_file(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if len(relative.parts) == 1:
        return path.name in _SCANNED_ROOT_FILES
    return relative.parts[0] in _SCANNED_DIRECTORIES and path.suffix.lower() in _SCANNED_SUFFIXES


def _deduplicate(findings: list[SecurityFinding]) -> list[SecurityFinding]:
    result: list[SecurityFinding] = []
    seen: set[tuple[str, str]] = set()
    for item in findings:
        key = (item.code, item.location)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
