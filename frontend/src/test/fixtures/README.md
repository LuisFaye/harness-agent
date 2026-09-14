# NDJSON v2 契约夹具

这些夹具来自当前后端公开契约，不包含真实用户输入、密钥或 Tool 原始结果。

夹具依据：

- `src/daily_agent/stream_protocol.py` 的事件类型、版本和 sequence 规则。
- `src/daily_agent/api.py` 的 `/ask/stream` 发包顺序与 payload。
- `src/daily_agent/working_state.py` 的公开 WorkingState。
- `tests/test_api.py` 已验证的成功、失败和取消事件序列。
- `src/daily_agent/run_models.py` 的事件回放响应。

Python 契约测试会校验所有 NDJSON 文件与后端协议一致；TypeScript 测试会校验解析、分块、状态投影、重复事件和断流恢复。

| 文件 | 场景 |
|---|---|
| `stream-success.ndjson` | 状态、Tool Trace、流式回答和成功终态 |
| `stream-failed.ndjson` | 执行中失败 |
| `stream-cancelled.ndjson` | 取消请求和取消完成 |
| `stream-waiting-confirmation.ndjson` | 等待高风险操作确认 |
| `stream-recovery-initial.ndjson` | 断流前已收到的事件 |
| `stream-recovery-page.json` | 从事件接口补拉的后续事件 |
