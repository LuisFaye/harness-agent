import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP


# 这个进程只用于测试 SB Agent 的 stdio MCP 连接。
# FastMCP 会把协议内容写到标准输入输出，所以这里不能额外 print，
# 否则普通日志会混进 JSON-RPC 数据，客户端会误判 MCP Server 损坏。
http_mode = len(sys.argv) >= 3 and sys.argv[1] == "--http"
crash_marker = None
if "--crash-once" in sys.argv:
    marker_index = sys.argv.index("--crash-once") + 1
    crash_marker = Path(sys.argv[marker_index])
server = FastMCP(
    "sb-agent-test-echo",
    host="127.0.0.1",
    port=int(sys.argv[2]) if http_mode else 8000,
    stateless_http=http_mode,
)


@server.tool(description="把收到的文字原样返回，用于验证 Agent 真的调用了 MCP Tool。")
def echo_text(text: str) -> str:
    return f"MCP-ECHO:{text}"


@server.tool(description="第一次调用主动断开，重启后返回结果，用于验证客户端自动恢复。")
def unstable_echo(text: str) -> str:
    if crash_marker is not None and not crash_marker.exists():
        crash_marker.write_text("crashed", encoding="utf-8")
        # 必须直接结束子进程，才能模拟真实 MCP Server 崩溃，而不是普通工具业务异常。
        os._exit(7)
    return f"MCP-RECOVERED:{text}"


@server.resource("echo://status", description="测试 MCP Server 的固定状态资源。")
def status_resource() -> str:
    return "ready"


@server.prompt(description="生成一条用于测试的简短问候提示词。")
def greeting_prompt(name: str = "测试用户") -> str:
    return f"请向{name}问好。"


if __name__ == "__main__":
    # HTTP 测试由父进程传入临时端口，避免写死端口后与本机其他服务冲突。
    # 没有 `--http` 参数时继续使用 stdio，覆盖实际桌面端最常见的本地 MCP 用法。
    server.run(transport="streamable-http" if http_mode else "stdio")
