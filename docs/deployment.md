# 部署指南

推荐使用仓库内的 Docker Compose。默认配置仅绑定本机回环地址，适合本机使用，也便于在前面增加 HTTPS 反向代理。

## Compose 部署

准备配置并启动：

~~~bash
cp .env.example .env
# 编辑 .env，设置随机且唯一的 SB_AGENT_MYSQL_PASSWORD
docker compose up --build -d
docker compose ps
curl --fail http://127.0.0.1:16666/health
~~~

首次启动流程如下：

1. MySQL 8.4 完成健康检查。
2. 应用执行可重复的 `sb-agent memory init`。
3. FastAPI 在容器内监听 `16666`。
4. 打开 <http://127.0.0.1:16666/> 初始化所有者并配置模型。

Compose 使用三个命名卷：MySQL 数据、SB Agent 数据和用户工作区。普通停止不会删除它们：

~~~bash
docker compose stop
docker compose start
docker compose down
~~~

`docker compose down -v` 会删除数据库、账号、会话、配置和工作区数据，只能在确认不需要恢复时执行。

## 更新版本

更新源码后重新构建并启动：

~~~bash
docker compose build --pull app
docker compose up -d
docker compose ps
curl --fail http://127.0.0.1:16666/health
~~~

入口脚本会再次执行数据库初始化；SQL 使用幂等结构，正常更新不会主动删除历史数据。正式更新前仍应备份数据库和应用数据卷。

## 公网访问

不要把 `16666` 直接暴露到公网，也不要把 Compose 端口改为 `0.0.0.0` 后依赖应用自身承担 TLS。推荐保留：

~~~yaml
ports:
  - "127.0.0.1:16666:16666"
~~~

然后使用 Cloudflare Tunnel、Caddy、Nginx 或其他受控入口提供 HTTPS，并为应用容器设置：

~~~yaml
environment:
  SB_AGENT_PUBLIC_ORIGIN: https://agent.example.com
  SB_AGENT_COOKIE_SECURE: "true"
~~~

反向代理需要支持长连接和流式传输，不应缓冲 `application/x-ndjson` 响应。公网启用前至少确认：

- 证书有效并强制 HTTPS。
- 所有者已初始化，邀请链接使用正确公网域名。
- Cookie 带 Secure 属性，代理没有改写错误的 Host 或协议。
- MySQL 没有发布宿主机端口。
- `.env`、配置文件和备份不在 Web 可访问目录。

## 外部 MySQL 或单独构建镜像

只构建应用镜像：

~~~bash
docker build -t sb-agent:local .
~~~

运行时至少需要传入 `SB_AGENT_MYSQL_HOST`、`SB_AGENT_MYSQL_USER`、`SB_AGENT_MYSQL_PASSWORD` 和 `SB_AGENT_MYSQL_DATABASE`，并持久化 `/data` 与 `/workspace`。数据库账号需要对目标库拥有建表、变更结构和读写权限。

模型配置可在所有者工作台保存，也可以使用 `SB_AGENT_MODEL_API_KEY`、`SB_AGENT_MODEL_BASE_URL` 和 `SB_AGENT_MODEL` 注入。不要把真实密钥写入 Dockerfile、Compose 文件或 Git 跟踪的配置。

## 备份与恢复

需要同时备份两部分：

- MySQL：用户、会话、运行、事件、记忆、能力配置和元数据。
- `/data`、`/workspace`：配置、导入的 Skill、附件、产物和用户工作区。

数据库可使用常规 `mysqldump` 生成逻辑备份。文件卷可在停止应用写入后，由运维环境按命名卷执行归档。备份文件包含账号、密钥或用户内容时，应加密保存并限制读取权限。

恢复顺序建议为：先恢复 MySQL，再恢复相同时间点的两个文件卷，最后启动应用并检查 `/health`、登录、历史会话和文件下载。不要只恢复数据库后假定附件和产物也完整。

## 运行检查

~~~bash
docker compose ps
docker compose logs --tail=200 app
docker compose logs --tail=200 mysql
curl --fail http://127.0.0.1:16666/health
~~~

常见问题：

- 应用反复重启：先检查 MySQL 健康状态、密码是否一致和数据库初始化日志。
- 页面不是最新版本：确认镜像重新构建，并查看 `/health` 返回的前端构建信息。
- 登录后反复退出：检查公网地址、HTTPS 和 Secure Cookie 配置。
- 流式回答一次性出现：关闭反向代理缓冲并延长读取超时。
- Docker 中看不到 Shell/Git：这是当前 Linux 镜像的已知安全限制，不是安装失败。
