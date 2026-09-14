from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from daily_agent.capability_models import McpServerWriteRequest
from daily_agent.database_models import (
    DatabaseConnectionInteraction,
    DatabaseConnectionResponse,
    DatabaseConnectionSaveResponse,
    DatabaseConnectionWriteRequest,
)
from daily_agent.errors import DailyAgentError
from daily_agent.mcp_catalog import McpCatalog, McpServerRecord
from daily_agent.mcp_runtime import McpRuntimeManager, get_mcp_runtime
from daily_agent.paths import AppPaths

MYSQL_COMMAND = "mysql_mcp_server_pro"
MANAGED_BY = "database_connection"
_PASSWORD_ASSIGNMENT = re.compile(
    r"(?i)(?P<prefix>\b(?:mysql_)?password\s*[:=]\s*)(?P<quote>['\"]?)(?P<value>[^\s,;'\"&]+)(?P=quote)"
)
_MYSQL_URI = re.compile(r"(?i)(?:jdbc:)?mysql://[^\s]+")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_$.-]{1,128}$")


@dataclass
class DatabaseConnectionRecord:
    connection_id: str
    name: str
    host: str
    port: int
    database: str
    username: str
    mcp_server_id: str
    credential_source: str
    envfile_path: str
    status: str = "disconnected"
    last_connected_at: str | None = None
    last_error: str | None = None

    @property
    def imported(self) -> bool:
        return self.credential_source == "external_envfile"


class DatabaseConnectionCatalog:
    """保存无密钥连接元数据，并把密码限制在权限为 0600 的独立 env 文件中。"""

    def __init__(
        self,
        *,
        paths: AppPaths | None = None,
        path: Path | None = None,
        connection_home: Path | None = None,
    ) -> None:
        self.paths = paths or AppPaths.discover()
        self.path = (path or self.paths.database_connection_registry_path).resolve()
        self.connection_home = (connection_home or self.paths.database_connection_home).resolve()

    def list_connections(self) -> list[DatabaseConnectionRecord]:
        payload = self._load()
        items = [
            DatabaseConnectionRecord(**item)
            for item in payload.get("connections", [])
            if isinstance(item, dict)
        ]
        return sorted(items, key=lambda item: item.name.lower())

    def get(self, connection_id: str) -> DatabaseConnectionRecord | None:
        return next((item for item in self.list_connections() if item.connection_id == connection_id), None)

    def get_by_mcp_server(self, server_id: str) -> DatabaseConnectionRecord | None:
        return next((item for item in self.list_connections() if item.mcp_server_id == server_id), None)

    def create_managed(
        self,
        request: DatabaseConnectionWriteRequest,
        *,
        mcp_server_id: str,
    ) -> DatabaseConnectionRecord:
        items = self.list_connections()
        self._validate_unique_name(request.name, items)
        connection_id = f"db-{_slug(request.name)}-{uuid.uuid4().hex[:8]}"
        envfile = self.connection_home / f"{connection_id}.env"
        self._write_envfile(envfile, request)
        record = DatabaseConnectionRecord(
            connection_id=connection_id,
            name=request.name.strip(),
            host=request.host.strip(),
            port=request.port,
            database=request.database.strip(),
            username=request.username.strip(),
            mcp_server_id=mcp_server_id,
            credential_source="managed_envfile",
            envfile_path=str(envfile),
        )
        self._save([*items, record])
        return record

    def adopt_external(
        self,
        *,
        server: McpServerRecord,
        envfile: Path,
    ) -> DatabaseConnectionRecord:
        existing = self.get_by_mcp_server(server.server_id)
        if existing is not None:
            return existing
        values = _read_envfile(envfile)
        connection_id = f"db-{_slug(server.name)}-{uuid.uuid4().hex[:8]}"
        record = DatabaseConnectionRecord(
            connection_id=connection_id,
            name=server.name,
            host=values.get("MYSQL_HOST", ""),
            port=_safe_port(values.get("MYSQL_PORT")),
            database=values.get("MYSQL_DATABASE", ""),
            username=values.get("MYSQL_USER", ""),
            mcp_server_id=server.server_id,
            credential_source="external_envfile",
            envfile_path=str(envfile.resolve()),
            status=server.status,
            last_connected_at=server.last_connected_at,
            last_error=server.last_error,
        )
        self._save([*self.list_connections(), record])
        return record

    def update(self, connection_id: str, request: DatabaseConnectionWriteRequest) -> DatabaseConnectionRecord | None:
        items = self.list_connections()
        current = next((item for item in items if item.connection_id == connection_id), None)
        if current is None:
            return None
        self._validate_unique_name(request.name, items, exclude_id=connection_id)
        if current.imported:
            raise DailyAgentError("外部 envfile 导入的连接不能在这里修改，请编辑原 envfile 后重新测试。")
        if request.password:
            password = request.password
        else:
            password = _read_envfile(Path(current.envfile_path)).get("MYSQL_PASSWORD", "")
        effective = request.model_copy(update={"password": password})
        self._write_envfile(Path(current.envfile_path), effective)
        current.name = request.name.strip()
        current.host = request.host.strip()
        current.port = request.port
        current.database = request.database.strip()
        current.username = request.username.strip()
        current.status = "disconnected"
        current.last_error = None
        self._save([current if item.connection_id == connection_id else item for item in items])
        return current

    def update_runtime(self, connection_id: str, *, status: str, error: str | None = None) -> None:
        items = self.list_connections()
        current = next((item for item in items if item.connection_id == connection_id), None)
        if current is None:
            return
        current.status = status
        current.last_error = error
        if status == "connected":
            current.last_connected_at = datetime.now(timezone.utc).isoformat()
        self._save(items)

    def delete(self, connection_id: str) -> DatabaseConnectionRecord | None:
        items = self.list_connections()
        current = next((item for item in items if item.connection_id == connection_id), None)
        if current is None:
            return None
        self._save([item for item in items if item.connection_id != connection_id])
        if not current.imported:
            try:
                Path(current.envfile_path).unlink(missing_ok=True)
            except OSError as exc:
                raise DailyAgentError(f"连接已删除，但私密文件清理失败：{exc}") from exc
        return current

    def _write_envfile(self, path: Path, request: DatabaseConnectionWriteRequest) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "\n".join(
            [
                f"MYSQL_HOST={_env_value(request.host)}",
                f"MYSQL_PORT={request.port}",
                f"MYSQL_USER={_env_value(request.username)}",
                f"MYSQL_PASSWORD={_env_value(request.password)}",
                f"MYSQL_DATABASE={_env_value(request.database)}",
                "MYSQL_ROLE=writer",
                "",
            ]
        )
        _atomic_private_write(path, content)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "connections": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DailyAgentError(f"数据库连接注册表损坏：{exc}") from exc
        return payload if isinstance(payload, dict) else {"version": 1, "connections": []}

    def _save(self, items: list[DatabaseConnectionRecord]) -> None:
        payload = {"version": 1, "connections": [asdict(item) for item in items]}
        _atomic_private_write(self.path, json.dumps(payload, ensure_ascii=False, indent=2))

    @staticmethod
    def _validate_unique_name(
        name: str,
        items: list[DatabaseConnectionRecord],
        *,
        exclude_id: str | None = None,
    ) -> None:
        normalized = name.strip().lower()
        if any(item.connection_id != exclude_id and item.name.lower() == normalized for item in items):
            raise DailyAgentError("数据库连接名称已存在。")


class DatabaseInteractionStore:
    """保存待补充的非敏感连接字段和原任务，任何密码都不得进入这里。"""

    def __init__(self, *, paths: AppPaths | None = None) -> None:
        self.path = (paths or AppPaths.discover()).database_interaction_registry_path.resolve()

    def create(
        self,
        *,
        user_id: str,
        session_id: str,
        query: str,
        fields: dict[str, Any],
    ) -> DatabaseConnectionInteraction:
        request_id = f"dbi-{uuid.uuid4().hex}"
        interaction = DatabaseConnectionInteraction(
            request_id=request_id,
            name=str(fields.get("name") or ""),
            host=str(fields.get("host") or ""),
            port=_safe_port(fields.get("port")),
            database=str(fields.get("database") or ""),
            username=str(fields.get("username") or ""),
        )
        items = self._load()
        items.append(
            {
                "request_id": request_id,
                "user_id": user_id,
                "session_id": session_id,
                "query": query,
                "interaction": interaction.model_dump(),
            }
        )
        _atomic_private_write(self.path, json.dumps({"version": 1, "items": items[-100:]}, ensure_ascii=False, indent=2))
        return interaction

    def consume(self, request_id: str) -> str | None:
        items = self._load()
        current = next((item for item in items if item.get("request_id") == request_id), None)
        if current is None:
            return None
        remaining = [item for item in items if item.get("request_id") != request_id]
        _atomic_private_write(self.path, json.dumps({"version": 1, "items": remaining}, ensure_ascii=False, indent=2))
        return str(current.get("query") or "") or None

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [item for item in payload.get("items", []) if isinstance(item, dict)]


class DatabaseConnectionService:
    def __init__(
        self,
        *,
        catalog: DatabaseConnectionCatalog | None = None,
        mcp_catalog: McpCatalog | None = None,
        runtime: McpRuntimeManager | None = None,
        interaction_store: DatabaseInteractionStore | None = None,
    ) -> None:
        self.catalog = catalog or DatabaseConnectionCatalog()
        self.mcp_catalog = mcp_catalog or McpCatalog()
        self.runtime = runtime or get_mcp_runtime()
        self.interaction_store = interaction_store or DatabaseInteractionStore()

    def adopt_legacy_mysql_servers(self) -> list[DatabaseConnectionRecord]:
        adopted = []
        for server in self.mcp_catalog.list_servers():
            if server.managed_by == MANAGED_BY or Path(str(server.command or "")).name != MYSQL_COMMAND:
                continue
            envfile = _arg_value(server.args, "--envfile")
            if not envfile or not Path(envfile).expanduser().is_file():
                continue
            record = self.catalog.adopt_external(server=server, envfile=Path(envfile).expanduser())
            self.mcp_catalog.mark_database_managed(server.server_id, record.connection_id)
            adopted.append(record)
        return adopted

    def list_connections(self) -> list[DatabaseConnectionResponse]:
        self.adopt_legacy_mysql_servers()
        return [self._response(item) for item in self.catalog.list_connections()]

    def create(self, request: DatabaseConnectionWriteRequest) -> DatabaseConnectionSaveResponse:
        command = shutil.which(MYSQL_COMMAND)
        if not command:
            raise DailyAgentError("本机没有找到 mysql_mcp_server_pro，请先安装后再连接 MySQL。")
        placeholder_id = f"pending-{uuid.uuid4().hex[:8]}"
        envfile = self.catalog.connection_home / f"{placeholder_id}.env"
        mcp = self.mcp_catalog.create(
            McpServerWriteRequest(
                name=request.name.strip(),
                transport_type="stdio",
                command=command,
                args=["--mode", "stdio", "--envfile", str(envfile), "--oauth", "false"],
                enabled=True,
            )
        )
        try:
            record = self.catalog.create_managed(request, mcp_server_id=mcp.server_id)
            actual_envfile = Path(record.envfile_path)
            mcp.args = ["--mode", "stdio", "--envfile", str(actual_envfile), "--oauth", "false"]
            self.mcp_catalog.update(
                mcp.server_id,
                McpServerWriteRequest(
                    name=request.name.strip(),
                    transport_type="stdio",
                    command=command,
                    args=mcp.args,
                    enabled=True,
                ),
            )
            self.mcp_catalog.mark_database_managed(mcp.server_id, record.connection_id)
        except Exception:
            self.mcp_catalog.delete(mcp.server_id)
            raise
        try:
            self._test_record(record)
        except Exception:
            # 新连接只有通过真实只读健康检查才算保存成功。失败时完整回滚，
            # 用户修正表单后可以继续使用同一名称重试，不会遗留不可编辑的半成品。
            self.runtime.disconnect(record.mcp_server_id)
            self.mcp_catalog.delete(record.mcp_server_id)
            self.catalog.delete(record.connection_id)
            raise
        continuation = (
            self.interaction_store.consume(request.interaction_request_id)
            if request.interaction_request_id
            else None
        )
        return DatabaseConnectionSaveResponse(
            connection=self._response(self.catalog.get(record.connection_id) or record),
            continuation_query=continuation,
        )

    def update(self, connection_id: str, request: DatabaseConnectionWriteRequest) -> DatabaseConnectionSaveResponse | None:
        record = self.catalog.update(connection_id, request)
        if record is None:
            return None
        mcp = self.mcp_catalog.get(record.mcp_server_id)
        if mcp is not None:
            self.runtime.disconnect(mcp.server_id)
            self.mcp_catalog.update(
                mcp.server_id,
                McpServerWriteRequest(
                    name=request.name.strip(),
                    transport_type="stdio",
                    command=mcp.command,
                    args=["--mode", "stdio", "--envfile", record.envfile_path, "--oauth", "false"],
                    enabled=True,
                ),
            )
            self.mcp_catalog.mark_database_managed(mcp.server_id, connection_id)
        self._test_record(record)
        return DatabaseConnectionSaveResponse(connection=self._response(self.catalog.get(connection_id) or record))

    def test(self, connection_id: str) -> DatabaseConnectionResponse | None:
        record = self.catalog.get(connection_id)
        if record is None:
            return None
        self._test_record(record)
        return self._response(self.catalog.get(connection_id) or record)

    def delete(self, connection_id: str) -> bool:
        record = self.catalog.get(connection_id)
        if record is None:
            return False
        self.runtime.disconnect(record.mcp_server_id)
        self.mcp_catalog.delete(record.mcp_server_id)
        return self.catalog.delete(connection_id) is not None

    def _test_record(self, record: DatabaseConnectionRecord) -> None:
        mcp = self.mcp_catalog.get(record.mcp_server_id)
        if mcp is None:
            raise DailyAgentError("数据库连接对应的内部适配器不存在。")
        self.catalog.update_runtime(record.connection_id, status="connecting")
        try:
            capabilities = self.runtime.connect(mcp, force=True)
            tool_name = _find_execute_sql_tool(capabilities.get("tools") or [])
            if not tool_name:
                raise DailyAgentError("MySQL 适配器没有提供 execute_sql Tool。")
            health_result = self.runtime.invoke(
                record.mcp_server_id,
                tool_name,
                {
                    "query": (
                        "SELECT DATABASE() AS current_database, "
                        "CURRENT_USER() AS authenticated_user, 1 AS connection_ok"
                    )
                },
            )
            if "connection_ok" not in str(health_result):
                raise DailyAgentError("MySQL 健康检查没有返回预期结果。")
        except Exception as exc:
            message = _safe_connection_error(exc)
            self.catalog.update_runtime(record.connection_id, status="error", error=message)
            self.mcp_catalog.update_runtime(record.mcp_server_id, status="error", last_error=message)
            raise
        self.mcp_catalog.update_runtime(
            record.mcp_server_id,
            status="connected",
            last_error=None,
            tools=capabilities.get("tools") or [],
            resources=capabilities.get("resources") or [],
            prompts=capabilities.get("prompts") or [],
        )
        self.catalog.update_runtime(record.connection_id, status="connected", error=None)

    def _response(self, record: DatabaseConnectionRecord) -> DatabaseConnectionResponse:
        return DatabaseConnectionResponse(
            connection_id=record.connection_id,
            name=record.name,
            host=record.host,
            port=record.port,
            database=record.database,
            username=record.username,
            status=record.status,
            last_connected_at=record.last_connected_at,
            last_error=record.last_error,
            imported=record.imported,
            password_configured=bool(_read_envfile(Path(record.envfile_path)).get("MYSQL_PASSWORD")),
            tool_names=database_tool_names(record),
        )


def sanitize_database_credentials(query: str) -> tuple[str, dict[str, Any] | None]:
    """在请求进入运行历史、记忆提取和模型之前移除用户误粘贴的数据库密码。"""
    fields: dict[str, Any] = {}
    detected = False
    sanitized = query
    uri_match = _MYSQL_URI.search(query)
    if uri_match:
        raw_uri = uri_match.group(0)
        split_value = raw_uri[5:] if raw_uri.lower().startswith("jdbc:") else raw_uri
        parsed = urlsplit(split_value)
        if parsed.password is not None:
            detected = True
            fields.update(
                host=parsed.hostname or "",
                port=parsed.port or 3306,
                username=unquote(parsed.username or ""),
                database=parsed.path.lstrip("/"),
            )
            safe_uri = f"mysql://{fields['username']}@{fields['host']}:{fields['port']}/{fields['database']}"
            sanitized = sanitized.replace(raw_uri, safe_uri)
    if _PASSWORD_ASSIGNMENT.search(sanitized):
        detected = True
        sanitized = _PASSWORD_ASSIGNMENT.sub(lambda match: f"{match.group('prefix')}[已移除，请使用安全表单]", sanitized)
    return sanitized, fields if detected else None


def classify_mysql_sql(sql: str) -> str:
    """使用 sqlglot AST 严格区分只读和写操作，无法确认安全时一律归为写操作。"""
    from sqlglot import exp, parse
    from sqlglot.errors import ParseError

    text = str(sql or "").strip()
    if not text:
        raise DailyAgentError("SQL 不能为空。")
    try:
        statements = [item for item in parse(text, read="mysql") if item is not None]
    except ParseError as exc:
        raise DailyAgentError(f"SQL 语法无法解析：{exc}") from exc
    if len(statements) != 1:
        raise DailyAgentError("每次只能执行一条 SQL，禁止多语句调用。")
    statement = statements[0]
    forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter, exp.Command)
    if any(statement.find(kind) is not None for kind in forbidden):
        return "write"
    normalized = text.upper()
    if "INTO OUTFILE" in normalized or "INTO DUMPFILE" in normalized or "FOR UPDATE" in normalized:
        return "write"
    if isinstance(statement, (exp.Select, exp.Show, exp.Describe)):
        return "read"
    return "write"


def database_tool_names(record: DatabaseConnectionRecord) -> list[str]:
    prefix = f"db_{_tool_slug(record.name)}"
    return [f"{prefix}_query", f"{prefix}_list_tables", f"{prefix}_describe_table", f"{prefix}_execute"]


def find_interaction_in_observations(observations: list[Any]) -> dict[str, Any] | None:
    for observation in observations:
        if str(getattr(observation, "tool", "")) != "database_connect":
            continue
        try:
            payload = json.loads(str(getattr(observation, "output", "") or ""))
        except ValueError:
            continue
        if isinstance(payload, dict) and payload.get("type") == "database_connection":
            return payload
    return None


def apply_database_interaction_result(result):
    """把 database_connect 的 Tool 结果转换成统一 secure_input 终态。"""
    from dataclasses import replace

    interaction = find_interaction_in_observations(result.observations)
    if interaction is None:
        return result
    return replace(
        result,
        status="needs_input",
        response_text="请在下方安全表单中填写数据库连接信息。密码不会发送给模型。",
        stop_reason="等待用户安全填写数据库连接",
        missing_info=[],
        error_stage="secure_input",
        interaction=interaction,
    )


def _atomic_private_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _read_envfile(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    values = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        raw_value = value.strip()
        if raw_value.startswith('"') and raw_value.endswith('"'):
            try:
                values[key.strip()] = str(json.loads(raw_value))
                continue
            except ValueError:
                pass
        values[key.strip()] = raw_value.strip("'\"")
    return values


def _env_value(value: str) -> str:
    return json.dumps(str(value).replace("\n", "").replace("\r", ""), ensure_ascii=False)


def _arg_value(args: list[str], key: str) -> str | None:
    try:
        return args[args.index(key) + 1]
    except (ValueError, IndexError):
        return None


def _find_execute_sql_tool(tools: list[dict[str, Any]]) -> str | None:
    return next((str(item.get("name")) for item in tools if str(item.get("name") or "").endswith("_execute_sql")), None)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "mysql"


def _tool_slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "mysql"
    return normalized[:32]


def _safe_port(value: Any) -> int:
    try:
        port = int(value or 3306)
    except (TypeError, ValueError):
        return 3306
    return port if 1 <= port <= 65535 else 3306


def _safe_connection_error(exc: Exception) -> str:
    """第三方连接错误可能拼接连接串，返回或持久化前必须再次移除凭据。"""
    text = " ".join(str(exc).split())
    text = _PASSWORD_ASSIGNMENT.sub(lambda match: f"{match.group('prefix')}******", text)
    text = re.sub(
        r"(?i)((?:jdbc:)?mysql://[^:\s/@]+:)[^@\s]+(@)",
        r"\1******\2",
        text,
    )
    return text[:500] or exc.__class__.__name__
