# SB Agent

SB Agent 是一个基于 Agno 的自托管任务 Agent。它提供浏览器工作台、CLI、HTTP API、定时任务、持久化会话，以及可扩展的 Tool / Skill / MCP 能力。

普通自然语言请求由模型在已启用工具中自主执行；显式 `/command` 和未完成的确定性 Skill 会话走固定流程。会话、运行、事件、记忆和用户数据保存在 MySQL 中。

## 主要能力

- React 浏览器工作台与 NDJSON 流式响应
- 多用户登录、邀请码、会话隔离与只读分享
- 对话历史、摘要、长期记忆、运行事件和证据持久化
- 本地 Tool、原生/兼容 Skill、MCP stdio 与 Streamable HTTP
- 文件、产物、网页读取、项目检查、日报与定时任务
- 工具确认、敏感路径拦截、运行取消和执行预算

## Docker 快速开始

需要 Docker 与 Docker Compose。项目会同时启动 MySQL 8.4 和 SB Agent，并在首次启动时自动初始化数据库。

~~~bash
cp .env.example .env
# 将 .env 中的 SB_AGENT_MYSQL_PASSWORD 改为随机强密码
docker compose up --build -d
docker compose ps
~~~

打开 <http://127.0.0.1:16666/>，创建第一个所有者账号，然后在“模型设置”中填写兼容 OpenAI API 的地址、模型名和 API Key。

查看日志或停止服务：

~~~bash
docker compose logs -f app
docker compose down
~~~

`docker compose down` 会保留数据库和应用数据。不要随意添加 `-v`，它会删除 Compose 管理的数据卷。

镜像运行在 Linux 中。当前受控 Shell/Git 工具依赖 macOS `sandbox-exec`，因此在 Docker 中不可用；工作台、API、模型、记忆、文件、Skill 和 MCP 不受此限制。

完整部署、备份和公网接入说明见 [部署指南](docs/deployment.md)。

## 本地开发

需要 Python 3.11+、MySQL 8、Node.js 22 和 pnpm。

~~~bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
sb-agent config init
~~~

编辑 `~/.sb-agent/config.toml`，配置模型和 MySQL。该文件可能包含密钥，权限会设为 `600`，并且不应提交到 Git。

初始化数据库并构建前端：

~~~bash
sb-agent memory init
cd frontend
pnpm install --frozen-lockfile
pnpm run build
cd ..
~~~

启动 API：

~~~bash
sb-agent-api
# 等价于：uvicorn daily_agent.api:app --host 127.0.0.1 --port 16666
~~~

内部 Python 包名仍为 `daily_agent`，用于兼容现有导入路径；旧的 `daily-agent*` 命令也暂时保留为别名。

## 常用命令

~~~bash
sb-agent doctor
sb-agent ask "梳理当前项目结构"
sb-agent ask --session-id work "继续分析主要调用流程"
sb-agent history --limit 20
sb-agent skills
sb-agent-worker
~~~

`sb-agent-worker` 每次只扫描一轮到期任务；生产环境可由 cron 或 systemd timer 周期调用。

## 配置

配置文件默认位于 `~/.sb-agent/config.toml`，也可以通过 `SB_AGENT_CONFIG` 指向其他位置。常用环境变量如下：

| 环境变量 | 用途 | 默认值 |
| --- | --- | --- |
| `SB_AGENT_MODEL_API_KEY` | 模型 API Key | 空 |
| `SB_AGENT_MODEL_BASE_URL` | OpenAI 兼容接口地址 | `https://api.deepseek.com` |
| `SB_AGENT_MODEL` | 模型 ID | `deepseek-v4-flash` |
| `SB_AGENT_MYSQL_HOST` | MySQL 地址 | `127.0.0.1` |
| `SB_AGENT_MYSQL_PORT` | MySQL 端口 | `3306` |
| `SB_AGENT_MYSQL_USER` | MySQL 用户 | `root` |
| `SB_AGENT_MYSQL_PASSWORD` | MySQL 密码 | 空 |
| `SB_AGENT_MYSQL_DATABASE` | 数据库名 | `sb_agent` |
| `SB_AGENT_API_PORT` | API 监听端口 | `16666` |
| `SB_AGENT_DATA_DIR` | 配置、日志和导入 Skill 的数据目录 | `~/.sb-agent` |

完整默认配置由 `sb-agent config init` 生成。执行预算默认最多调用 16 次工具、总时长不限制；单工具超时、重复调用保护和取消机制仍然生效。

## 安全边界

- Compose 默认只将端口绑定到 `127.0.0.1`。公网访问必须放在 HTTPS 反向代理或安全隧道之后。
- 首次打开会初始化所有者；之后业务接口使用服务端登录会话，并按用户隔离数据。
- 第三方 Skill 和 MCP 可能执行命令或访问外部服务，启用前应检查来源、权限与密钥范围。
- 不要将 `.env`、`~/.sb-agent/config.toml`、数据库备份或真实用户数据提交到仓库。
- 不建议在本项目中保存银行卡、证件、交易所密钥等高敏感信息。

## 文档

- [架构说明](docs/architecture.md)：核心组件、执行链路、状态与数据边界。
- [部署指南](docs/deployment.md)：Compose、本地部署、HTTPS、备份与升级。
- [Skill 指南](docs/skills.md)：Skill 类型、导入、清单与安全模型。
