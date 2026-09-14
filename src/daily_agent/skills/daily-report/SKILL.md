---
name: daily-report
description: "根据 git 提交记录生成工作日报。"
---

# daily-report

根据 git 提交记录生成工作日报。

## 使用场景

- 用户说“生成日报”“今天做了什么”“工作总结”
- 用户使用 `/daily-report` 或 `/daily_report`
- 用户希望扫描一个或多个 git 仓库并整理当天工作

## 输入

- `repos`：要扫描的 git 仓库路径列表；不传时运行时会尝试使用当前项目。
- `target_date`：日报日期，格式 `YYYY-MM-DD`；不传时默认今天。
- `author`：可选，只统计该作者的提交。
- `extra_notes`：代码提交之外的补充工作，例如会议、沟通、排查、文档。

## 输出

输出一段简洁中文日报正文，并在执行结果里保留扫描到的提交元数据，便于追溯。
