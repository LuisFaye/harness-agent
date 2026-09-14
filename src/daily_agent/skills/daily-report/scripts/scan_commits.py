#!/usr/bin/env python3
"""
日报生成用的 Git 提交扫描脚本。

脚本会扫描一个或多个 git 仓库在指定日期内的提交，
并输出日报服务可直接消费的结构化数据。
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, date
from pathlib import Path
from typing import Optional


def run_git_command(repo_path: str, args: list[str]) -> tuple[bool, str]:
    """在指定仓库里执行 git 命令，并返回是否成功和标准输出。"""
    try:
        result = subprocess.run(
            ["git", "-C", repo_path] + args,
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.returncode == 0, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except Exception as e:
        return False, str(e)


def is_git_repo(path: str) -> bool:
    """判断路径是不是 git 仓库。"""
    success, _ = run_git_command(path, ["rev-parse", "--git-dir"])
    return success


def get_current_git_user(repo_path: str) -> str:
    """读取当前仓库配置的 git 用户名或邮箱。"""
    success, name = run_git_command(repo_path, ["config", "user.name"])
    if success and name:
        return name
    success, email = run_git_command(repo_path, ["config", "user.email"])
    if success and email:
        return email
    return ""


def get_repo_name(repo_path: str) -> str:
    """优先从 remote 地址推断仓库名，失败时使用目录名。"""
    success, remote = run_git_command(repo_path, ["remote", "get-url", "origin"])
    if success and remote:
        # 从远程仓库地址里提取最后一段作为仓库名。
        name = remote.rstrip("/").split("/")[-1]
        if name.endswith(".git"):
            name = name[:-4]
        return name
    return Path(repo_path).name


def get_current_branch(repo_path: str) -> str:
    """读取当前分支名。"""
    success, branch = run_git_command(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    if success:
        return branch
    return "未知"


def get_commit_branches(repo_path: str, commit_hash: str) -> list[str]:
    """获取包含指定提交的所有分支。"""
    success, output = run_git_command(
        repo_path,
        ["branch", "-a", "--contains", commit_hash, "--format=%(refname:short)"]
    )
    if success and output:
        branches = [b.strip() for b in output.split("\n") if b.strip()]
        # 如果本地分支已经存在，就过滤掉重复的 origin/xxx 远程跟踪分支。
        local_branches = [b for b in branches if not b.startswith("origin/")]
        remote_only = [b for b in branches if b.startswith("origin/") and b.replace("origin/", "") not in local_branches]
        return local_branches + remote_only
    return []


def get_remote_url(repo_path: str) -> str:
    """读取 origin 远程地址。"""
    success, url = run_git_command(repo_path, ["remote", "get-url", "origin"])
    if success:
        return url
    return ""


def get_commits_for_date(
    repo_path: str,
    target_date: date,
    author: Optional[str] = None
) -> list[dict]:
    """获取指定日期内的所有提交。"""
    date_str = target_date.strftime("%Y-%m-%d")

    # 使用不容易出现在提交信息里的分隔符，避免 subject/body 里有普通标点时解析错位。
    # 格式：hash<字段分隔>subject<字段分隔>author<字段分隔>email<字段分隔>date<字段分隔>body<提交分隔>
    commit_sep = "<<<COMMIT_END>>>"
    field_sep = "<<<FIELD_SEP>>>"

    # 拼出包含完整提交正文的 git log 命令。
    log_args = [
        "log",
        f"--since={date_str} 00:00:00",
        f"--until={date_str} 23:59:59",
        f"--format=%H{field_sep}%s{field_sep}%an{field_sep}%ae{field_sep}%ai{field_sep}%b{commit_sep}",
        "--all"
    ]

    if author:
        log_args.append(f"--author={author}")

    success, output = run_git_command(repo_path, log_args)

    if not success or not output:
        return []

    commits = []
    commit_blocks = output.split(commit_sep)

    for block in commit_blocks:
        block = block.strip()
        if not block:
            continue

        parts = block.split(field_sep, 5)
        if len(parts) >= 5:
            commit_hash = parts[0]
            subject = parts[1]
            author_name = parts[2]
            author_email = parts[3]
            commit_date = parts[4]
            body = parts[5].strip() if len(parts) > 5 else ""

            # 同时保留标题和正文，方便后续摘要或追溯。
            full_message = subject
            if body:
                full_message = f"{subject}\n{body}"

            # 读取本次提交的文件和代码行数变更。
            stats = get_commit_stats(repo_path, commit_hash)

            # 读取这个提交出现在哪些分支上。
            branches = get_commit_branches(repo_path, commit_hash)

            commits.append({
                "hash": commit_hash[:8],
                "full_hash": commit_hash,
                "subject": subject,
                "body": body,
                "message": full_message,
                "author": author_name,
                "email": author_email,
                "date": commit_date,
                "branches": branches,
                "stats": stats
            })

    return commits


def get_commit_stats(repo_path: str, commit_hash: str) -> dict:
    """读取单个提交的文件、插入行和删除行统计。"""
    success, output = run_git_command(
        repo_path,
        ["show", "--stat", "--format=", commit_hash]
    )

    stats = {
        "files_changed": 0,
        "insertions": 0,
        "deletions": 0
    }

    if not success or not output:
        return stats

    # git show --stat 的最后一行通常是汇总信息，这里只解析这一行。
    lines = output.strip().split("\n")
    if lines:
        summary_line = lines[-1]
        # 解析类似 "X files changed, Y insertions(+), Z deletions(-)" 的统计文本。
        import re
        files_match = re.search(r"(\d+) files? changed", summary_line)
        ins_match = re.search(r"(\d+) insertions?\(\+\)", summary_line)
        del_match = re.search(r"(\d+) deletions?\(-\)", summary_line)

        if files_match:
            stats["files_changed"] = int(files_match.group(1))
        if ins_match:
            stats["insertions"] = int(ins_match.group(1))
        if del_match:
            stats["deletions"] = int(del_match.group(1))

    return stats


def find_git_repos(base_paths: list[str]) -> list[str]:
    """在给定路径中查找 git 仓库，支持路径本身或下一层子目录。"""
    repos = []

    for base_path in base_paths:
        base_path = os.path.expanduser(base_path)
        if not os.path.exists(base_path):
            continue

        # 先判断传入路径本身是不是仓库。
        if is_git_repo(base_path):
            repos.append(base_path)
            continue

        # 再查找下一层子目录，避免递归过深导致扫描过慢。
        try:
            for item in os.listdir(base_path):
                item_path = os.path.join(base_path, item)
                if os.path.isdir(item_path) and is_git_repo(item_path):
                    repos.append(item_path)
        except PermissionError:
            continue

    return list(set(repos))  # 去重，避免同一仓库被重复扫描。


def get_common_dev_paths() -> list[str]:
    """返回常见开发目录，用于 --scan-home 自动扫描。"""
    home = os.path.expanduser("~")
    common_dirs = [
        "projects",
        "work",
        "dev",
        "code",
        "repos",
        "github",
        "gitlab",
        "src",
        "workspace",
        "IdeaProjects"
    ]
    return [os.path.join(home, d) for d in common_dirs]


def scan_repositories(
    repos: list[str],
    target_date: date,
    author: Optional[str] = None
) -> dict:
    """扫描多个仓库并汇总提交、作者、分支和代码变更统计。"""
    scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    results = {
        "scan_metadata": {
            "scan_time": scan_time,
            "target_date": target_date.strftime("%Y-%m-%d"),
            "author_filter": author,
            "scanned_repos_count": 0,
            "repos_with_commits": 0
        },
        "authors": [],
        "branches": [],
        "repositories": [],
        "total_commits": 0,
        "total_files_changed": 0,
        "total_insertions": 0,
        "total_deletions": 0
    }

    all_authors = set()
    all_branches = set()
    scanned_count = 0

    for repo_path in repos:
        if not is_git_repo(repo_path):
            continue

        scanned_count += 1

        # 未显式传作者时，默认只统计当前仓库配置的 git 用户。
        effective_author = author
        if not effective_author:
            effective_author = get_current_git_user(repo_path)

        commits = get_commits_for_date(repo_path, target_date, effective_author)

        if commits:
            repo_name = get_repo_name(repo_path)
            current_branch = get_current_branch(repo_path)
            remote_url = get_remote_url(repo_path)

            repo_stats = {
                "files_changed": sum(c["stats"]["files_changed"] for c in commits),
                "insertions": sum(c["stats"]["insertions"] for c in commits),
                "deletions": sum(c["stats"]["deletions"] for c in commits)
            }

            # 汇总去重后的作者和分支，方便日报附录追溯。
            repo_authors = set()
            repo_branches = set()
            for c in commits:
                repo_authors.add(f"{c['author']} <{c['email']}>")
                repo_branches.update(c.get("branches", []))

            all_authors.update(repo_authors)
            all_branches.update(repo_branches)

            results["repositories"].append({
                "name": repo_name,
                "path": repo_path,
                "remote_url": remote_url,
                "current_branch": current_branch,
                "authors": list(repo_authors),
                "branches_involved": list(repo_branches),
                "commits": commits,
                "stats": repo_stats
            })

            results["total_commits"] += len(commits)
            results["total_files_changed"] += repo_stats["files_changed"]
            results["total_insertions"] += repo_stats["insertions"]
            results["total_deletions"] += repo_stats["deletions"]

    results["scan_metadata"]["scanned_repos_count"] = scanned_count
    results["scan_metadata"]["repos_with_commits"] = len(results["repositories"])
    results["authors"] = sorted(list(all_authors))
    results["branches"] = sorted(list(all_branches))

    return results


def format_text_output(results: dict) -> str:
    """把扫描结果格式化成适合终端阅读的文本。"""
    lines = []
    meta = results.get("scan_metadata", {})
    target_date = meta.get("target_date", "未知")

    lines.append(f"=== Git Commits for {target_date} ===")
    lines.append("")

    # 扫描元数据。
    lines.append("--- 扫描信息 ---")
    lines.append(f"扫描时间: {meta.get('scan_time', '未知')}")
    lines.append(f"目标日期: {target_date}")
    lines.append(f"扫描仓库数: {meta.get('scanned_repos_count', 0)}")
    lines.append(f"有提交的仓库: {meta.get('repos_with_commits', 0)}")
    if meta.get("author_filter"):
        lines.append(f"作者过滤: {meta['author_filter']}")
    lines.append("")

    if not results["repositories"]:
        lines.append("当天没有找到提交。")
        return "\n".join(lines)

    # 作者汇总。
    if results.get("authors"):
        lines.append("--- 提交作者 ---")
        for author in results["authors"]:
            lines.append(f"  - {author}")
        lines.append("")

    # 分支汇总。
    if results.get("branches"):
        lines.append("--- 涉及分支 ---")
        for branch in results["branches"]:
            lines.append(f"  - {branch}")
        lines.append("")

    # 全局统计。
    lines.append("--- 统计汇总 ---")
    lines.append(f"总提交数: {results['total_commits']} 次提交")
    lines.append(f"仓库数: {len(results['repositories'])}")
    lines.append(f"文件变更: {results['total_files_changed']} 个文件")
    lines.append(f"代码变更: +{results['total_insertions']}/-{results['total_deletions']} lines")
    lines.append("")

    # 按仓库列出提交详情。
    lines.append("--- 提交详情 ---")
    for repo in results["repositories"]:
        lines.append(f"## {repo['name']}")
        lines.append(f"   路径: {repo['path']}")
        if repo.get("remote_url"):
            lines.append(f"   远程: {repo['remote_url']}")
        lines.append(f"   当前分支: {repo.get('current_branch', '未知')}")
        if repo.get("branches_involved"):
            lines.append(f"   涉及分支: {', '.join(repo['branches_involved'])}")
        if repo.get("authors"):
            lines.append(f"   作者: {', '.join(repo['authors'])}")
        lines.append(f"   提交数: {len(repo['commits'])}")
        lines.append("")

        for commit in repo["commits"]:
            branch_info = ""
            if commit.get("branches"):
                branch_info = f" [{', '.join(commit['branches'][:3])}]"
            lines.append(f"   - [{commit['hash']}]{branch_info} {commit.get('subject', commit.get('message', ''))}")
            lines.append(f"     作者: {commit['author']} <{commit['email']}>")
            lines.append(f"     时间: {commit['date']}")
            if commit["stats"]["files_changed"] > 0:
                stats = commit["stats"]
                lines.append(f"     变更: {stats['files_changed']} 个文件, +{stats['insertions']}/-{stats['deletions']}")
            # 如果提交正文不为空，也一起展示出来。
            if commit.get("body"):
                body_lines = commit["body"].split("\n")
                for body_line in body_lines:
                    if body_line.strip():
                        lines.append(f"       {body_line.strip()}")
            lines.append("")

        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="扫描指定日期的 git 提交，用于生成工作日报。"
    )
    parser.add_argument(
        "--repos",
        type=str,
        help="逗号分隔的仓库路径列表。"
    )
    parser.add_argument(
        "--date",
        type=str,
        default=date.today().strftime("%Y-%m-%d"),
        help="扫描日期，格式 YYYY-MM-DD，默认今天。"
    )
    parser.add_argument(
        "--author",
        type=str,
        help="按作者名或邮箱过滤，默认使用当前 git 用户。"
    )
    parser.add_argument(
        "--scan-home",
        action="store_true",
        help="扫描用户目录下的常见开发目录。"
    )
    parser.add_argument(
        "--format",
        choices=["json", "text"],
        default="text",
        help="输出格式，默认 text。"
    )

    args = parser.parse_args()

    # 解析用户传入的日期，格式错误时直接用非 0 状态退出。
    try:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    except ValueError:
        print(f"错误：日期格式不正确：{args.date}。请使用 YYYY-MM-DD。", file=sys.stderr)
        sys.exit(1)

    # 根据命令参数决定要扫描哪些仓库。
    if args.repos:
        repo_paths = [p.strip() for p in args.repos.split(",")]
        repos = find_git_repos(repo_paths)
    elif args.scan_home:
        search_paths = get_common_dev_paths() + [os.getcwd()]
        repos = find_git_repos(search_paths)
    else:
        # 默认扫描当前目录；如果当前目录不是仓库，就看它下一层有没有仓库。
        cwd = os.getcwd()
        if is_git_repo(cwd):
            repos = [cwd]
        else:
            repos = find_git_repos([cwd])

    if not repos:
        print("没有找到 git 仓库。", file=sys.stderr)
        sys.exit(1)

    # 执行扫描。
    results = scan_repositories(repos, target_date, args.author)

    # 按用户要求输出 JSON 或文本。
    if args.format == "json":
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        print(format_text_output(results))


if __name__ == "__main__":
    main()
