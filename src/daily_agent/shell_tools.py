"""原生受控 Shell 与进程 Tool：在工作区内执行命令、增量查看输出、按归属停止进程。

本模块提供三个原生 Tool 定义（shell_execute / shell_status / shell_cancel）以及
线程安全的进程管理器。第三方兼容 Skill 的受限 Shell 执行器（compatible_skill_runtime）
不参与本模块，二者契约独立。

安全边界
--------
- 所有命令一律用 ``shlex.split`` 解析成参数数组后 ``Popen(argv, shell=False)`` 直接执行，
  绝不经过 shell 解释器，因此天然禁止管道、重定向、命令替换、控制运算符和多命令拼接。
- 命令/路径/环境校验与文件系统隔离逻辑在 ``shell_security`` 模块：解释器内联代码选项
  检查参数序列中 "--" 之前的全部选项；相对参数（含裸 config.toml、credentials 等）做
  敏感路径检查；macOS 用 sandbox-exec 真实阻止工作区脚本读取/写入 Workspace Root 与
  Shell 私有 data 目录之外的用户/临时文件，不静默降级为未隔离执行。
- 工作目录只允许 Workspace Root 内的相对/绝对路径，阻止 ``..``、符号链接逃逸和敏感目录。
- 子进程使用独立受控环境：默认只继承 PATH、locale、时区、证书路径等安全键，并设置位于
  SB Agent data 目录内的私有 HOME/TMPDIR/XDG 目录，不继承模型、数据库、MCP 等主进程密钥。
- shell_execute 一律需要现有人工确认（同一运行确认后不重复确认）；shell_status /
  shell_cancel 不需要确认，但只能查看或停止同一 user/session/run 归属的进程。
- 命令或参数出现敏感字样（password/token/secret/authorization/api_key 等）直接拒绝，
  返回给模型的 command 字段统一脱敏，绝不把原始敏感命令写入公开结果。
- 永久拒绝 sudo/su/doas、rm/rmdir、kill/pkill/killall、dd/mkfs/fdisk/shutdown/reboot/
  diskutil 等系统破坏命令，以及 sh/bash/zsh/fish/PowerShell/cmd 等 shell 解释器和
  python -c / node -e 等内联任意代码。
"""

from __future__ import annotations

import codecs
import json
import logging
import os
import signal
import subprocess
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any

from daily_agent.errors import DailyAgentError
from daily_agent.paths import AppPaths
from daily_agent.user_workspace import user_workspace_root
from daily_agent.shell_security import (
    MAX_COMMAND_CHARS,
    SAFE_INHERITED_ENV_KEYS,
    redact_command,
    resolve_cwd,
    shell_runtime_status,
    validate_arg_paths,
    validate_command,
    validate_environment,
    wrap_argv_with_isolation,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 契约常量

SHELL_SOURCE_ID = "workspace-shell"

# timeout_seconds 默认 60，钳制到 1–300。
DEFAULT_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 300

# wait_seconds 默认 5，钳制到 0–10；等待时间内完成返回终态，否则返回 running + process_id。
DEFAULT_WAIT_SECONDS = 5
MAX_WAIT_SECONDS = 10

# 有界环形输出缓冲：最多保留 12 万字符，超出的早期内容会被丢弃并标记 truncated。
MAX_OUTPUT_CHARS = 120_000

# 后台读取线程每次从管道读取的最大字节数；read1 只取当前可用数据，保证增量可见。
READ_CHUNK_BYTES = 65536

# 并发上限：全局 20、单 run 4；达到上限明确拒绝，不淘汰仍在运行的进程。
GLOBAL_MAX_RUNNING_PROCESSES = 20
MAX_RUNNING_PROCESSES_PER_RUN = 4

# 已完成记录保留 15 分钟，后续操作时惰性清理，避免永久积累。
COMPLETED_RETENTION_SECONDS = 15 * 60

# 终止进程组时的 TERM 宽限期（秒），宽限结束后仍存活则 KILL。
TERM_GRACE_SECONDS = 3

# 写入终态前等待 reader 排空管道 EOF 的有界时间（秒），保证尾部输出尽量完整。
READER_DRAIN_WAIT_SECONDS = 2.0


# ---------------------------------------------------------------- Tool 定义

# 三个原生 Shell Tool 的稳定定义，ToolRegistry 与 Agent 构建共用。
SHELL_TOOL_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "name": "shell_execute",
        "description": (
            "在工作区根目录内执行一条普通命令并返回输出。命令必须是没有管道、重定向、"
            "通配符展开、控制运算符和多命令拼接的单个可执行程序；会自动解析成参数数组"
            "后直接执行，不经 shell 解释器。支持工作区相对 cwd、超时、等待时长和少量"
            "非敏感环境变量；执行前需要用户确认。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": MAX_COMMAND_CHARS,
                    "description": "要执行的普通命令字符串（1 到 4000 字符），不允许包含管道、重定向、控制运算符或换行",
                },
                "cwd": {
                    "type": "string",
                    "description": "命令工作目录：工作区相对路径或解析后仍位于工作区内的绝对目录，默认 .",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "超时秒数，1 到 300，默认 60；超时自动终止整个进程组",
                },
                "wait_seconds": {
                    "type": "integer",
                    "description": "等待秒数，0 到 10，默认 5；等待内完成返回终态，未完成返回 running 和 process_id",
                },
                "environment": {
                    "type": "object",
                    "description": "可选的非敏感环境变量，只允许 CI/NO_COLOR/FORCE_COLOR/PYTHONUNBUFFERED/NODE_ENV/TZ/LANG/LC_ALL",
                    "additionalProperties": False,
                    "propertyNames": {
                        "enum": ["CI", "NO_COLOR", "FORCE_COLOR", "PYTHONUNBUFFERED", "NODE_ENV", "TZ", "LANG", "LC_ALL"]
                    },
                },
            },
            "required": ["command"],
            "additionalProperties": False,
        },
        "requires_confirmation": True,
    },
    {
        "name": "shell_status",
        "description": (
            "查询同一个 user/session/run 下由 shell_execute 启动的进程的最新状态，"
            "并从指定输出偏移量增量返回新内容、next_offset 以及是否因环形缓冲丢失早期内容。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "process_id": {
                    "type": "string",
                    "description": "shell_execute 返回的 process_id",
                },
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "输出偏移量，从该绝对字符位置开始返回新内容，默认 0",
                },
            },
            "required": ["process_id"],
            "additionalProperties": False,
        },
        "requires_confirmation": False,
    },
    {
        "name": "shell_cancel",
        "description": (
            "停止同一个 user/session/run 下仍在运行的 Shell 进程：先 TERM 整个进程组，"
            "短暂宽限后仍存活则 KILL。已经结束的进程返回当前终态，不会伪报新的取消。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "process_id": {
                    "type": "string",
                    "description": "shell_execute 返回的 process_id",
                },
            },
            "required": ["process_id"],
            "additionalProperties": False,
        },
        "requires_confirmation": False,
    },
)


# ---------------------------------------------------------------- 通用工具函数


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    """把用户传参钳制到安全范围；非法值回落默认，避免模型乱传导致越界。"""
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def _iso_timestamp() -> str:
    """生成 UTC ISO 时间字符串，用于 started_at / finished_at。"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _stable_owner_run_id(run_id: str | None) -> str:
    """run_id 为空时生成仅供该 Agent 实例使用的稳定 owner id，避免所有默认运行共享归属。"""
    if run_id:
        return str(run_id)
    return f"instance-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------- 单个进程记录


class _ShellProcess:
    """单个受控 Shell 子进程的全部状态：输出环形缓冲、归属、终态与并发标记。

    输出缓冲使用绝对偏移：任何读取方只需记住 next_offset，就能拿到后续全部内容；
    缓冲超限时丢弃最早内容并把 _dropped 置为 True，让调用方知道早期输出已丢失。
    状态、returncode、finished_at、terminate_started 与首次终态写入统一由 cond 保护，
    保证取消/超时/自然退出竞争时只有一条路径取得终止所有权，终态不会被覆盖。
    """

    def __init__(
        self,
        *,
        process_id: str,
        proc: subprocess.Popen,
        argv: list[str],
        command: str,
        cwd_relative: str,
        user_id: str,
        session_id: str,
        run_id: str,
        isolation: str,
        started_at: str,
        started_at_ts: float,
    ) -> None:
        self.process_id = process_id
        self.proc = proc
        self.argv = tuple(argv)
        self.command = command
        self.cwd_relative = cwd_relative
        self.user_id = user_id
        self.session_id = session_id
        self.run_id = run_id
        self.isolation = isolation
        self.started_at = started_at
        self.started_at_ts = started_at_ts
        self.status = "running"
        self.returncode: int | None = None
        self.finished_at: str | None = None
        self.finished_at_ts: float | None = None
        # 终止所有权标记：只有成功把它从 False 置为 True 的路径负责写终态。
        self.terminate_started = False
        # reader 完成事件：自然退出/取消/超时写终态前会先等待它排空 EOF。
        self.reader_done_event = threading.Event()
        self.cond = threading.Condition()
        self._io_lock = threading.Lock()
        self._chunks: deque[str] = deque()
        self._total_chars = 0
        self._buffer_start = 0
        self._dropped = False

    @property
    def ring_dropped(self) -> bool:
        """是否因为环形缓冲丢弃过早期输出。"""
        return self._dropped

    def append_output(self, text: str) -> None:
        """追加解码后的输出；超过上限时精确丢弃最早内容并标记 truncated。

        优先整块丢弃最早 chunk；最后一块只截掉超出的前缀，保证保留内容恰好为
        MAX_OUTPUT_CHARS，且 buffer_start 与 next_offset 始终是连续绝对偏移。
        """
        with self._io_lock:
            if not text:
                return
            self._chunks.append(text)
            self._total_chars += len(text)
            while self._total_chars > MAX_OUTPUT_CHARS and self._chunks:
                first = self._chunks[0]
                if self._total_chars - len(first) >= MAX_OUTPUT_CHARS:
                    self._chunks.popleft()
                    self._total_chars -= len(first)
                    self._buffer_start += len(first)
                    self._dropped = True
                    continue
                excess = self._total_chars - MAX_OUTPUT_CHARS
                self._chunks[0] = first[excess:]
                self._buffer_start += excess
                self._total_chars = MAX_OUTPUT_CHARS
                self._dropped = True
                break

    def read_output(self, offset: int) -> tuple[str, int, bool]:
        """从绝对偏移量读取输出，返回 (新内容, 新的 next_offset, 是否丢失请求区间)。

        chunk 的局部偏移基于 _buffer_start 计算，不能把截断后的局部起点当作绝对 0：
        - offset 早于 buffer_start：返回当前全部保留内容，next_offset=buffer_end，
          lost=True（本次请求区间已因环形截断丢失）；
        - buffer_start <= offset < buffer_end：精确返回该绝对 offset 之后的内容；
        - offset >= buffer_end：返回空，next_offset 回到当前 buffer_end，
          避免调用方保留超大 offset 永久错过后续输出。
        """
        with self._io_lock:
            buffer_end = self._buffer_start + self._total_chars
            if offset < self._buffer_start:
                return "".join(self._chunks), buffer_end, True
            if offset >= buffer_end:
                return "", buffer_end, False
            local = offset - self._buffer_start
            chars_before = 0
            started = False
            parts: list[str] = []
            for chunk in self._chunks:
                if not started:
                    if local >= chars_before + len(chunk):
                        chars_before += len(chunk)
                        continue
                    parts.append(chunk[local - chars_before :])
                    started = True
                else:
                    parts.append(chunk)
            return "".join(parts), buffer_end, False


# ---------------------------------------------------------------- 进程管理器


class ShellProcessManager:
    """线程安全的 Shell 进程注册表：启动、增量状态、取消、超时回收与归属校验。"""

    def __init__(self, *, paths: AppPaths | None = None) -> None:
        resolved = paths or AppPaths.discover()
        self.workspace_root = resolved.workspace_root
        self.data_dir = resolved.data_dir
        self._shell_data_dir = resolved.data_dir / "shell"
        self._lock = threading.RLock()
        self._processes: dict[str, _ShellProcess] = {}

    # ---------------- 对外操作 ----------------

    def start(
        self,
        *,
        command: str,
        cwd: str = ".",
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        wait_seconds: int = DEFAULT_WAIT_SECONDS,
        environment: dict[str, str] | None = None,
        user_id: str,
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        """校验并启动一个受控子进程，返回契约快照（含 process_id、初始 output 与 isolation）。"""
        argv = validate_command(command)
        validate_arg_paths(argv, self.workspace_root)
        working_dir, cwd_relative = resolve_cwd(self.workspace_root, cwd)
        env_overrides = validate_environment(environment)
        timeout = _clamp_int(timeout_seconds, DEFAULT_TIMEOUT_SECONDS, 1, MAX_TIMEOUT_SECONDS)
        wait = _clamp_int(wait_seconds, DEFAULT_WAIT_SECONDS, 0, MAX_WAIT_SECONDS)
        private_env = self._build_private_environment(env_overrides)
        launch_argv, isolation = wrap_argv_with_isolation(argv, self.workspace_root, self._shell_data_dir)

        with self._lock:
            self._purge_completed()
            running = [item for item in self._processes.values() if item.status == "running"]
            if len(running) >= GLOBAL_MAX_RUNNING_PROCESSES:
                raise DailyAgentError(
                    f"Shell 全局并发进程数已达上限（{GLOBAL_MAX_RUNNING_PROCESSES}），请等待已有进程结束。"
                )
            per_run = sum(1 for item in running if item.run_id == run_id)
            if per_run >= MAX_RUNNING_PROCESSES_PER_RUN:
                raise DailyAgentError(
                    f"单个运行内 Shell 并发进程数已达上限（{MAX_RUNNING_PROCESSES_PER_RUN}），"
                    "请先结束或等待已有进程。"
                )
            try:
                proc = subprocess.Popen(
                    launch_argv,
                    cwd=str(working_dir),
                    env=private_env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    shell=False,
                    # POSIX 下创建独立 session/进程组，保证取消与超时能终止整个进程组。
                    start_new_session=os.name == "posix",
                )
            except OSError as exc:
                raise DailyAgentError(f"无法启动命令：{exc}") from exc
            process_id = uuid.uuid4().hex
            record = _ShellProcess(
                process_id=process_id,
                proc=proc,
                argv=argv,
                command=command,
                cwd_relative=cwd_relative,
                user_id=str(user_id),
                session_id=str(session_id),
                run_id=str(run_id),
                isolation=isolation,
                started_at=_iso_timestamp(),
                started_at_ts=time.time(),
            )
            self._processes[process_id] = record

        # 在注册表锁外启动后台线程：读取线程持续排空管道，监控线程负责超时终止。
        threading.Thread(
            target=self._reader_loop,
            args=(record,),
            daemon=True,
            name=f"shell-reader-{process_id[:8]}",
        ).start()
        threading.Thread(
            target=self._monitor_loop,
            args=(record, timeout),
            daemon=True,
            name=f"shell-monitor-{process_id[:8]}",
        ).start()

        if wait > 0:
            self._wait_for_start(record, wait)
        return self._snapshot(record, offset=0)

    def status(
        self,
        *,
        process_id: str,
        offset: int = 0,
        user_id: str,
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        """查询同归属进程的最新状态；offset 为绝对输出偏移量，可增量轮询。"""
        with self._lock:
            self._purge_completed()
            record = self._get_owned(process_id, user_id, session_id, run_id)
        safe_offset = max(0, _clamp_int(offset, 0, 0, 10**12))
        return self._snapshot(record, offset=safe_offset)

    def cancel(
        self,
        *,
        process_id: str,
        user_id: str,
        session_id: str,
        run_id: str,
    ) -> dict[str, Any]:
        """幂等停止同归属进程；已结束的进程返回当前终态，不伪报新的取消。"""
        with self._lock:
            self._purge_completed()
            record = self._get_owned(process_id, user_id, session_id, run_id)
        self._terminate(record, mark_status="cancelled")
        return self._snapshot(record, offset=0)

    def cancel_run(self, *, run_id: str, user_id: str | None = None) -> bool:
        """停止指定 run（可附带 user 过滤）仍在运行的 Shell 子进程，返回是否有进程被停止。

        供运行生命周期（RunLifecycleService.request_cancel）调用：整次 Agent 取消时，
        快速受理并交由后台线程并发终止各进程，不在管理器锁内串行等待最多 4×3 秒；
        后台线程会一直等到所有匹配进程都写入终态，保证最终停止且不留下孤儿。
        """
        with self._lock:
            matches = [
                item
                for item in self._processes.values()
                if item.status == "running"
                and item.run_id == str(run_id)
                and (user_id is None or item.user_id == str(user_id))
            ]
        if not matches:
            return False

        def _cancel_in_background() -> None:
            workers = []
            for item in matches:
                worker = threading.Thread(
                    target=self._terminate,
                    args=(item,),
                    kwargs={"mark_status": "cancelled"},
                    daemon=True,
                    name=f"shell-cancel-{item.process_id[:8]}",
                )
                worker.start()
                workers.append(worker)
            for worker in workers:
                worker.join()

        threading.Thread(
            target=_cancel_in_background,
            daemon=True,
            name="shell-cancel-run",
        ).start()
        return True

    # ---------------- 内部实现 ----------------

    def _get_owned(self, process_id: str, user_id: str, session_id: str, run_id: str) -> _ShellProcess:
        """按归属取进程：不存在或归属不匹配都返回明确中文错误。"""
        record = self._processes.get(str(process_id))
        if record is None:
            raise DailyAgentError("Shell 进程不存在或已完成清理。")
        if (
            record.user_id != str(user_id)
            or record.session_id != str(session_id)
            or record.run_id != str(run_id)
        ):
            raise DailyAgentError("无权访问该 Shell 进程：进程归属与本轮 user/session/run 不匹配。")
        return record

    def _snapshot(self, record: _ShellProcess, *, offset: int) -> dict[str, Any]:
        """生成 Tool 契约快照：状态字段 + 从 offset 开始的新输出 + 下一偏移量 + 隔离标识。

        状态字段在同一把 cond 锁下读取，避免 status/returncode/finished_at 互相不一致。
        """
        with record.cond:
            status = record.status
            returncode = record.returncode
            finished_at = record.finished_at
        output, next_offset, dropped = record.read_output(offset)
        return {
            "process_id": record.process_id,
            "status": status,
            "cwd": record.cwd_relative,
            "command": redact_command(record.command),
            "returncode": returncode,
            "output": output,
            "next_offset": next_offset,
            "truncated": dropped or record.ring_dropped,
            "started_at": record.started_at,
            "finished_at": finished_at,
            "isolation": record.isolation,
        }

    def _build_private_environment(self, overrides: dict[str, str]) -> dict[str, str]:
        """构造受控子进程环境：只继承安全键，HOME/TMPDIR/XDG 全部指到 data 目录内。"""
        env = {key: os.environ[key] for key in SAFE_INHERITED_ENV_KEYS if key in os.environ}
        home = self._shell_data_dir / "home"
        tmp = self._shell_data_dir / "tmp"
        xdg_config = self._shell_data_dir / "xdg" / "config"
        xdg_cache = self._shell_data_dir / "xdg" / "cache"
        xdg_data = self._shell_data_dir / "xdg" / "data"
        try:
            home.mkdir(parents=True, exist_ok=True)
            tmp.mkdir(parents=True, exist_ok=True)
            xdg_config.mkdir(parents=True, exist_ok=True)
            xdg_cache.mkdir(parents=True, exist_ok=True)
            xdg_data.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise DailyAgentError(f"无法创建 Shell 私有运行目录：{exc}") from exc
        env["HOME"] = str(home)
        env["TMPDIR"] = str(tmp)
        env["XDG_CONFIG_HOME"] = str(xdg_config)
        env["XDG_CACHE_HOME"] = str(xdg_cache)
        env["XDG_DATA_HOME"] = str(xdg_data)
        env.update(overrides)
        return env

    def _wait_for_start(self, record: _ShellProcess, wait_seconds: int) -> None:
        """等待最多 wait_seconds；条件变量让出 CPU，避免忙轮询。"""
        deadline = time.monotonic() + wait_seconds
        with record.cond:
            while record.status == "running" and time.monotonic() < deadline:
                record.cond.wait(timeout=max(0.0, min(0.2, deadline - time.monotonic())))

    def _reader_loop(self, record: _ShellProcess) -> None:
        """后台读取线程：增量 UTF-8 解码输出并写入环形缓冲，直到管道 EOF。

        使用 read1 只取当前已到达管道的字节，避免 BufferedReader 等满缓冲区才返回
        导致长进程输出在运行期间不可见；增量解码器正确处理跨块的多字节 UTF-8 字符。
        无论正常 EOF 还是异常，finally 都会设置完成事件并唤醒等待方，不得阻塞回收。
        """
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while True:
                chunk = record.proc.stdout.read1(READ_CHUNK_BYTES)
                if not chunk:
                    break
                record.append_output(decoder.decode(chunk))
            tail = decoder.decode(b"", final=True)
            if tail:
                record.append_output(tail)
        except Exception:
            # 读取线程异常不允许影响进程回收；进程终态由监控线程负责。
            logger.exception("Shell 输出读取线程异常退出：process_id=%s", record.process_id)
        finally:
            try:
                record.proc.stdout.close()
            except Exception:
                pass
            record.reader_done_event.set()
            with record.cond:
                record.cond.notify_all()

    def _monitor_loop(self, record: _ShellProcess, timeout_seconds: int) -> None:
        """后台监控线程：超时则终止整个进程组并标记 timed_out；自然退出则标记成功/失败。"""
        deadline = time.monotonic() + timeout_seconds
        while True:
            with record.cond:
                if record.terminate_started or record.status != "running":
                    # 已被取消/整次取消路径接管，终态由终止路径负责。
                    return
                remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._terminate(record, mark_status="timed_out")
                return
            try:
                returncode = record.proc.wait(timeout=min(remaining, 1.0))
            except subprocess.TimeoutExpired:
                continue
            if self._claim_terminal(record):
                # 自然退出：先等待 reader 排空 EOF 再写终态，保证尾部输出完整。
                self._wait_reader_drained(record)
                self._mark_terminal(
                    record,
                    "success" if returncode == 0 else "failed",
                    returncode,
                )
            return

    def _claim_terminal(self, record: _ShellProcess) -> bool:
        """在进程级 Condition 下原子取得终止所有权；只有成功者负责写终态。"""
        with record.cond:
            if record.status != "running" or record.terminate_started:
                return False
            record.terminate_started = True
            return True

    def _wait_reader_drained(self, record: _ShellProcess) -> None:
        """有界等待 reader 排空管道 EOF，保证终态快照包含尽量完整的尾部输出。"""
        record.reader_done_event.wait(timeout=READER_DRAIN_WAIT_SECONDS)

    def _wait_terminal(self, record: _ShellProcess) -> None:
        """等待其它路径写入终态（有界），避免取消/超时竞争时读到中间状态。"""
        deadline = time.monotonic() + TERM_GRACE_SECONDS + READER_DRAIN_WAIT_SECONDS + 1.0
        with record.cond:
            while record.status == "running" and time.monotonic() < deadline:
                record.cond.wait(timeout=max(0.0, min(0.2, deadline - time.monotonic())))

    def _terminate(
        self,
        record: _ShellProcess,
        *,
        mark_status: str,
        grace: float = TERM_GRACE_SECONDS,
    ) -> None:
        """终止进程组：先取得终止所有权，POSIX 先 TERM 整个进程组，宽限后仍存活则 KILL。

        终止所有权、状态读写和首次终态写入都在同一把 cond 锁下原子完成，保证并发
        取消/超时/自然退出只有一条路径能写终态；写终态前先等待 reader 排空 EOF。
        """
        if not self._claim_terminal(record):
            # 已终止或已被其它路径接管：等待接管路径写入终态后返回，不重复操作。
            self._wait_terminal(record)
            return
        pid = record.proc.pid
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            else:
                record.proc.terminate()
        except (ProcessLookupError, OSError):
            pass
        deadline = time.monotonic() + grace
        while True:
            try:
                returncode = record.proc.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                if time.monotonic() >= deadline:
                    break
                continue
            self._wait_reader_drained(record)
            self._mark_terminal(record, mark_status, returncode)
            return
        # 宽限结束仍存活：强制 KILL 整个进程组。
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            else:
                record.proc.kill()
        except (ProcessLookupError, OSError):
            pass
        try:
            returncode = record.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            returncode = record.proc.poll()
        self._wait_reader_drained(record)
        self._mark_terminal(record, mark_status, returncode if returncode is not None else -9)

    def _mark_terminal(self, record: _ShellProcess, status: str, returncode: int | None) -> None:
        """写入终态并唤醒等待方；同一进程只允许写入一次终态（cond 原子保护）。"""
        with record.cond:
            if record.status != "running":
                return
            record.status = status
            record.returncode = int(returncode) if returncode is not None else None
            record.finished_at = _iso_timestamp()
            record.finished_at_ts = time.time()
            record.cond.notify_all()

    def _purge_completed(self) -> None:
        """清理超过保留期的已完成记录；只在后续操作时惰性执行。"""
        cutoff = time.time() - COMPLETED_RETENTION_SECONDS
        stale = [
            process_id
            for process_id, item in self._processes.items()
            if item.status != "running"
            and item.reader_done_event.is_set()
            and item.finished_at_ts is not None
            and item.finished_at_ts < cutoff
        ]
        for process_id in stale:
            self._processes.pop(process_id, None)


# ---------------------------------------------------------------- 模块级单例与生命周期挂钩

_shell_managers: dict[str, ShellProcessManager] = {}
_shell_manager_lock = threading.Lock()


def get_shell_manager(user_id: str = "default") -> ShellProcessManager:
    """按用户返回进程管理器，工作区和 Shell 私有目录都互相隔离。"""
    owner = str(user_id or "default")
    with _shell_manager_lock:
        manager = _shell_managers.get(owner)
        if manager is None:
            base = AppPaths.discover()
            workspace_root = user_workspace_root(owner, paths=base)
            data_dir = base.data_dir if owner == "default" else base.data_dir / "users" / owner / "runtime"
            paths = AppPaths.discover(
                app_home=base.app_home,
                workspace_root=workspace_root,
                data_dir=data_dir,
                config_path=base.config_path,
                log_dir=base.log_dir,
            )
            manager = ShellProcessManager(paths=paths)
            _shell_managers[owner] = manager
        return manager


def cancel_shell_processes_for_run(run_id: str, user_id: str | None = None) -> bool:
    """停止指定 run 的 Shell 子进程；供 RunLifecycleService.request_cancel 调用。

    任何异常都降级为 False（不阻断主流程的 Agno 取消），并记录日志。
    """
    try:
        if user_id is not None:
            managers = [get_shell_manager(user_id)]
        else:
            with _shell_manager_lock:
                managers = list(_shell_managers.values())
    except Exception:
        logger.exception("无法获取 Shell 进程管理器，跳过 Shell 进程取消：run_id=%s", run_id)
        return False
    try:
        return any(manager.cancel_run(run_id=run_id, user_id=user_id) for manager in managers)
    except Exception:
        logger.exception("停止 run 的 Shell 子进程失败：run_id=%s", run_id)
        return False


# ---------------------------------------------------------------- Agent Tool 构建


def build_shell_tools(
    *,
    user_id: str,
    session_id: str,
    run_id: str | None,
    confirmed_tool_names: set[str] | None = None,
) -> list[Any]:
    """构建 Agno 原生 Shell Tool 列表。

    每个 entrypoint 闭包都绑定当前 user_id / session_id / run_id，确保 status/cancel
    只能访问同归属进程；run_id 为空时使用仅供本次 Agent 实例使用的稳定 owner id。
    shell_execute 复用现有确认机制：首次构建 requires_confirmation=True，用户确认后
    react_runner 会把已确认工具名传回来，同名 Tool 不再重复确认。
    文件系统隔离后端不可用时直接返回空列表，Agent 不挂载不可用 Tool，Prompt Snapshot
    也不宣称可用（ToolRegistry 的 available 与 shell_runtime_status 保持一致）。
    ToolRegistry 在这里延迟导入，避免与 tool_registry 模块形成循环依赖。
    """
    runtime = shell_runtime_status()
    if not runtime["available"]:
        logger.warning("Shell 工具不可用，不挂载：%s", runtime.get("unavailable_reason"))
        return []
    from agno.tools.function import Function
    from daily_agent.tool_registry import ToolRegistry

    manager = get_shell_manager(user_id)
    registry = ToolRegistry()
    owner_run_id = _stable_owner_run_id(run_id)
    confirmed = {str(item).replace("-", "_") for item in (confirmed_tool_names or set())}
    entrypoints = {
        "shell_execute": _make_execute_entrypoint(manager, user_id, session_id, owner_run_id),
        "shell_status": _make_status_entrypoint(manager, user_id, session_id, owner_run_id),
        "shell_cancel": _make_cancel_entrypoint(manager, user_id, session_id, owner_run_id),
    }
    tools: list[Any] = []
    for spec in SHELL_TOOL_DEFINITIONS:
        name = str(spec["name"])
        tool_id = f"shell:{SHELL_SOURCE_ID}:{name}"
        if not registry.is_tool_enabled(tool_id):
            continue
        needs_confirmation = bool(spec["requires_confirmation"]) and name not in confirmed
        tools.append(
            Function(
                name=name,
                description=str(spec["description"]),
                parameters=spec["parameters"],
                entrypoint=entrypoints[name],
                skip_entrypoint_processing=True,
                requires_confirmation=needs_confirmation,
            )
        )
    return tools


def _make_execute_entrypoint(
    manager: ShellProcessManager,
    user_id: str,
    session_id: str,
    run_id: str,
):
    """生成 shell_execute 的 entrypoint：默认值必须与 Tool JSON Schema 一致。"""

    def execute(
        command: str = "",
        cwd: str = ".",
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        wait_seconds: int = DEFAULT_WAIT_SECONDS,
        environment: dict[str, str] | None = None,
    ) -> str:
        snapshot = manager.start(
            command=command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            wait_seconds=wait_seconds,
            environment=environment,
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
        )
        return json.dumps(snapshot, ensure_ascii=False)

    return execute


def _make_status_entrypoint(
    manager: ShellProcessManager,
    user_id: str,
    session_id: str,
    run_id: str,
):
    """生成 shell_status 的 entrypoint。"""

    def status(process_id: str = "", offset: int = 0) -> str:
        snapshot = manager.status(
            process_id=process_id,
            offset=offset,
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
        )
        return json.dumps(snapshot, ensure_ascii=False)

    return status


def _make_cancel_entrypoint(
    manager: ShellProcessManager,
    user_id: str,
    session_id: str,
    run_id: str,
):
    """生成 shell_cancel 的 entrypoint。"""

    def cancel(process_id: str = "") -> str:
        snapshot = manager.cancel(
            process_id=process_id,
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
        )
        return json.dumps(snapshot, ensure_ascii=False)

    return cancel
