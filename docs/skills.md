# Skill 指南

Skill 为 Agent 提供能力说明、输入契约和可选执行器。导入成功只代表内容已登记；是否能执行，还取决于兼容状态、依赖、启用状态和安全检查。

## Skill 类型

SB Agent 当前识别以下状态：

- `native`：包含 SB Agent 原生 `skill.json`，通过 `python_callable` 执行。
- `compatible`：第三方 `SKILL.md` 可映射到受支持的 HTTP、网页读取、浏览器或受控命令能力。
- `document_only`：可以作为说明提供给模型，但没有可调用执行器。
- `unsupported`：声明了当前不能安全运行的能力或命令。
- `error`：清单、路径或依赖解析失败。

交互模式分为两种：

- `react_tool`：作为工具进入普通 Agent loop，模型可按任务决定何时调用。
- `deterministic`：显式命令或持续会话进入固定 Skill 流程。

## 查看与导入

CLI 可查看已注册 Skill：

~~~bash
sb-agent skills
~~~

所有者也可以在工作台能力中心导入：

- 本机 Skill 目录。
- GitHub 仓库 URL。
- `https://skills.sh/owner/repo/skill` 形式的单个 Skill 地址。

导入后先查看兼容状态、诊断、依赖、风险等级和确认要求，再决定是否启用。`SKILL.md` 中写有 `web_search` 只表示需求被识别，不会自动安装搜索供应商；应通过 MCP 或兼容工具连接实际服务。

## 原生 Skill 结构

最小目录通常包含：

~~~text
my-skill/
├── SKILL.md
├── skill.json
└── scripts/
    └── run.py
~~~

`SKILL.md` 说明何时使用、输入含义、流程和边界；`skill.json` 描述机器可读的执行契约。原生 Python 执行器示例：

~~~json
{
  "id": "my-skill",
  "tool_name": "my_skill",
  "description": "处理一个明确的本地任务",
  "interaction_mode": "react_tool",
  "requires_confirmation": false,
  "input_schema": {
    "type": "object",
    "properties": {
      "task": {"type": "string", "description": "要处理的任务"}
    },
    "required": ["task"]
  },
  "execution": {
    "type": "python_callable",
    "entrypoint": "my_skill.handlers:run"
  }
}
~~~

可直接参考仓库中的 `src/daily_agent/skills/`。执行器应返回可序列化结果，不应依赖全局可写状态；需要文件时使用分配给当前用户的工作区。

## 第三方 Skill 安全

第三方 Skill 与普通依赖相同，必须先审查再启用：

- 固定可信来源和版本，避免运行来源不明或会自动更新的脚本。
- 检查 `SKILL.md`、清单和脚本是否要求读取密钥、敏感目录或上传数据。
- 高风险或外部可执行能力应保留用户确认，不要为了方便关闭。
- 只提供任务所需的环境变量；不要把整个配置文件或宿主机凭据目录挂进容器。
- 导入目录会做路径、符号链接、文件大小和危险内容检查，但这不能替代人工审查。

兼容命令只允许有限前缀，并受超时、确认和敏感路径规则限制。macOS 本地运行还会使用 `sandbox-exec` 做操作系统级约束；Docker/Linux 中因此禁用依赖该机制的受控 Shell/Git 工具。

## MCP 与 Skill 的选择

已有稳定 MCP Server 时优先使用 MCP：它能直接暴露工具、资源和提示词，并把服务端升级与 Agent 核心解耦。适合仓库内确定性逻辑、需要明确输入契约或需要跟随项目一起发布的能力，则使用原生 Skill。

MCP 密钥应来自环境变量或本机权限为 `600` 的配置文件；查询接口不会回显密钥明文。连接成功后仍需同步并启用具体工具，才能进入 Agent 的本轮工具列表。
