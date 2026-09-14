"""调用本地真实 API 执行 Agent 基线评测，并生成机器结果与人类可读报告。"""

from __future__ import annotations

import argparse
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main() -> int:
    parser = argparse.ArgumentParser(description="运行真实 Agent 基线评测")
    parser.add_argument("--base-url", default="http://127.0.0.1:16666")
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("live_agent_cases.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/sb-agent-eval"))
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=150)
    args = parser.parse_args()

    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    run_stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    user_id = f"eval-baseline-{run_stamp}"
    started_at = time.time()
    results: list[dict[str, Any]] = []

    # 每条用例使用独立 session，避免普通用例之间互相污染；只有显式配置 setup_queries
    # 的上下文用例会在同一个 session 内先执行铺垫消息，再执行真正被评分的问题。
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {
            pool.submit(run_case, args.base_url, user_id, case, args.timeout): case
            for case in cases
        }
        completed = 0
        for future in as_completed(futures):
            case = futures[future]
            completed += 1
            try:
                result = future.result()
            except Exception as exc:
                result = failure_result(case, f"评测执行器异常：{exc}")
            results.append(result)
            marker = "PASS" if result["passed"] else "FAIL"
            print(f"[{completed:02d}/{len(cases):02d}] {marker} {case['id']} {result['latency_ms']}ms", flush=True)

    results.sort(key=lambda item: next(index for index, case in enumerate(cases) if case["id"] == item["id"]))
    payload = {
        "run_stamp": run_stamp,
        "user_id": user_id,
        "base_url": args.base_url,
        "duration_ms": int((time.time() - started_at) * 1000),
        "summary": summarize(results),
        "results": results,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / f"baseline-{run_stamp}.json"
    report_path = args.output_dir / f"baseline-{run_stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(build_report(payload), encoding="utf-8")
    print(f"RESULT_JSON={json_path}")
    print(f"REPORT_MD={report_path}")
    return 0


def run_case(base_url: str, user_id: str, case: dict[str, Any], timeout: float) -> dict[str, Any]:
    session_id = f"eval-{case['id']}"
    setup_results = []
    for setup_query in case.get("setup_queries", []):
        setup_results.append(call_ask(base_url, setup_query, user_id, session_id, timeout))

    started_at = time.perf_counter()
    try:
        response = call_ask(base_url, case["query"], user_id, session_id, timeout)
        latency_ms = int((time.perf_counter() - started_at) * 1000)
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        return failure_result(case, f"HTTP 调用失败：{exc}", int((time.perf_counter() - started_at) * 1000))

    checks = evaluate(case, response, latency_ms)
    observations = response.get("observations") or []
    return {
        "id": case["id"],
        "category": case["category"],
        "query": case["query"],
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
        "latency_ms": latency_ms,
        "status": response.get("status"),
        "run_id": response.get("run_id"),
        "response_text": response.get("response_text") or "",
        "tools": [item.get("tool") for item in observations if item.get("tool")],
        "observations": observations,
        "loop_events": response.get("loop_events") or [],
        "trace": response.get("trace") or [],
        "setup_run_ids": [item.get("run_id") for item in setup_results],
    }


def call_ask(base_url: str, query: str, user_id: str, session_id: str, timeout: float) -> dict[str, Any]:
    body = json.dumps({"query": query, "user_id": user_id, "session_id": session_id}, ensure_ascii=False).encode()
    request = Request(
        f"{base_url.rstrip('/')}/ask",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def evaluate(case: dict[str, Any], response: dict[str, Any], latency_ms: int) -> list[dict[str, Any]]:
    text = str(response.get("response_text") or "")
    observations = response.get("observations") or []
    tools = [str(item.get("tool")) for item in observations if item.get("tool")]
    expected_status = case.get("expected_status", "success")
    checks = [check("终态符合预期", response.get("status") == expected_status, str(response.get("status")))]
    checks.append(check("回答非空", len(text.strip()) >= int(case.get("min_response_chars", 1)), f"{len(text.strip())} 字"))
    failed_tools = [str(item.get("tool")) for item in observations if item.get("status") != "success"]
    tool_failure_allowed = bool(case.get("allow_tool_failure"))
    checks.append(check("工具执行成功", not failed_tools or tool_failure_allowed, f"失败工具={failed_tools}"))

    if "max_response_chars" in case:
        checks.append(check("回答长度上限", len(text.strip()) <= case["max_response_chars"], f"{len(text.strip())}/{case['max_response_chars']} 字"))
    if "max_latency_ms" in case:
        checks.append(check("响应耗时", latency_ms <= case["max_latency_ms"], f"{latency_ms}/{case['max_latency_ms']}ms"))
    if "min_tools" in case:
        checks.append(check("最少工具次数", len(tools) >= case["min_tools"], f"{len(tools)}/{case['min_tools']}"))
    if "max_tools" in case:
        checks.append(check("最多工具次数", len(tools) <= case["max_tools"], f"{len(tools)}/{case['max_tools']}"))
    if case.get("required_tools"):
        missing = [name for name in case["required_tools"] if name not in tools]
        checks.append(check("必需工具", not missing, f"实际={tools} 缺少={missing}"))
    if case.get("expected_tools_any"):
        matched = any(name in tools for name in case["expected_tools_any"])
        checks.append(check("预期工具之一", matched, f"实际={tools}"))
    if case.get("response_contains_any"):
        lowered = text.lower()
        matched = any(str(word).lower() in lowered for word in case["response_contains_any"])
        checks.append(check("回答关键内容", matched, f"期望任一={case['response_contains_any']}"))
    if "min_url_count" in case:
        urls = set(re.findall(r"https?://[^\s\)\]>]+", text))
        checks.append(check("链接数量", len(urls) >= int(case["min_url_count"]), f"{len(urls)}/{case['min_url_count']}，实际={sorted(urls)}"))

    # 相同工具和完全相同参数出现两次，说明循环保护没有发挥作用。这里直接比较结构化参数，
    # 不依赖回答文案，因此能够稳定抓到真实重复调用。
    signatures = []
    for item in observations:
        signature = (item.get("tool"), json.dumps(item.get("inputs") or {}, ensure_ascii=False, sort_keys=True))
        signatures.append(signature)
    checks.append(check("无重复工具参数", len(signatures) == len(set(signatures)), str(signatures)))
    return checks


def check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def failure_result(case: dict[str, Any], message: str, latency_ms: int = 0) -> dict[str, Any]:
    return {
        "id": case["id"], "category": case["category"], "query": case["query"],
        "passed": False, "checks": [check("请求完成", False, message)], "latency_ms": latency_ms,
        "status": "error", "run_id": None, "response_text": "", "tools": [], "observations": [],
        "loop_events": [], "trace": [], "setup_run_ids": [],
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    categories: dict[str, dict[str, int]] = {}
    for item in results:
        current = categories.setdefault(item["category"], {"total": 0, "passed": 0})
        current["total"] += 1
        current["passed"] += int(item["passed"])
    passed = sum(int(item["passed"]) for item in results)
    latencies = sorted(item["latency_ms"] for item in results)
    return {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "pass_rate": round(passed / len(results) * 100, 1) if results else 0,
        "average_latency_ms": round(sum(latencies) / len(latencies)) if latencies else 0,
        "p95_latency_ms": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else 0,
        "categories": categories,
    }


def build_report(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# SB Agent 真实任务基线评测", "",
        f"- 执行批次：{payload['run_stamp']}",
        f"- 总用例：{summary['total']}",
        f"- 自动通过：{summary['passed']}",
        f"- 自动失败：{summary['failed']}",
        f"- 自动通过率：{summary['pass_rate']}%",
        f"- 平均耗时：{summary['average_latency_ms']}ms",
        f"- P95 耗时：{summary['p95_latency_ms']}ms", "",
        "## 分类结果", "", "| 分类 | 通过 | 总数 |", "|---|---:|---:|",
    ]
    for category, item in summary["categories"].items():
        lines.append(f"| {category} | {item['passed']} | {item['total']} |")
    lines.extend(["", "## 用例明细", "", "| 结果 | 用例 | 分类 | 耗时 | 工具 | 失败检查 |", "|---|---|---|---:|---|---|"])
    for item in payload["results"]:
        failed = "；".join(check_item["name"] for check_item in item["checks"] if not check_item["passed"]) or "-"
        marker = "PASS" if item["passed"] else "FAIL"
        lines.append(f"| {marker} | {item['id']} | {item['category']} | {item['latency_ms']}ms | {', '.join(item['tools']) or '-'} | {failed} |")
    lines.extend(["", "## 自动失败详情", ""])
    for item in payload["results"]:
        if item["passed"]:
            continue
        lines.append(f"### {item['id']}")
        lines.append("")
        lines.append(f"- 问题：{item['query']}")
        lines.append(f"- 回答：{item['response_text'][:600]}")
        for check_item in item["checks"]:
            if not check_item["passed"]:
                lines.append(f"- 失败项：{check_item['name']}；{check_item['detail']}")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
