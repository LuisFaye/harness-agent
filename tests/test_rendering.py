from daily_agent.services.rendering import redact_config, sanitize_response_text


def test_sanitize_response_text_strips_ansi_and_box_drawing():
    raw = "\x1b[1m┏━ Message ━━━━━━━━━━━━━━━━━━━━━━━━━\x1b[0m\n你好！有什么可以帮你的？"
    assert sanitize_response_text(raw) == "你好！有什么可以帮你的？"


def test_sanitize_response_text_removes_panel_headers():
    raw = "Message\nResponse\n\n这是最终回答。"
    assert sanitize_response_text(raw) == "这是最终回答。"


def test_sanitize_response_text_extracts_legacy_print_response_payload():
    raw = "\n".join(
        [
            "┏━ Message ━━━━━━━━━━━━━━━━━━━━━━━━━",
            "请根据下面已经完成的执行结果，给用户一个简洁、可执行的中文回答。",
            "用户原始问题：你好",
            "任务计划：",
            "- 识别意图：general",
            "- 风险等级：low",
            "Response",
            "你好！我是 SB Agent，有什么可以帮你的？",
        ]
    )
    assert sanitize_response_text(raw) == "你好！我是 SB Agent，有什么可以帮你的？"


def test_sanitize_response_text_handles_inline_panel_dump_with_visible_ansi_tokens():
    raw = (
        "│ │ 请根据下面已经完成的执行结果，给用户一个简洁、可执行的中文回答。 │ │ "
        "不要声称自己又查询了新的实时数据，只能使用观察结果里的内容。 │ │ "
        "用户原始问题：你好 │ │ 任务计划： │ │ 1. │ │ - 识别意图：general │ │ - 风险等级：low │ │ "
        "建议步骤： │ │ 1. 直接回答用户问题，工具：无需工具，原因：没有识别到需要实时工具的明确任务。 │ │ "
        "没有调用实时工具，请直接回答用户问题。 │ │ Response (4.2s) │ │ 你好！我是你的中文日常助手。 │ │ "
        "[1m• [0m梳理项目结构：说明入口文件和主流程。 │ │ [1m• [0m生成工作日报：扫描提交并整理产出。 │ │ "
        "[1m• [0m处理日常任务：将复杂需求拆解为可执行步骤。 │ │ 有什么具体需要帮忙的吗？ │ │"
    )
    assert sanitize_response_text(raw) == "\n".join(
        [
            "你好！我是你的中文日常助手。",
            "• 梳理项目结构：说明入口文件和主流程。",
            "• 生成工作日报：扫描提交并整理产出。",
            "• 处理日常任务：将复杂需求拆解为可执行步骤。",
            "有什么具体需要帮忙的吗？",
        ]
    )


def test_sanitize_response_text_preserves_markdown_lines_and_code_indentation():
    raw = """### 标题

- 列表

```python
if ready:
    run()
```
"""

    assert sanitize_response_text(raw) == raw.strip()


def test_sanitize_response_text_preserves_box_drawing_in_fenced_code():
    raw = """## 整体架构蓝图

```
┌─────────────────────────┐
│       AI Agent          │
├────────────┬────────────┤
│ filesystem │ database   │
└────────────┴────────────┘
```
"""

    assert sanitize_response_text(raw) == raw.strip()


def test_redact_config_keeps_token_budget_but_hides_secrets():
    payload = redact_config(
        {
            "model": {"api_key": "sk-secret"},
            "context": {"token_budget": 4000},
            "mysql": {"password": "password"},
        }
    )

    assert payload["model"]["api_key"] == "***"
    assert payload["mysql"]["password"] == "***"
    assert payload["context"]["token_budget"] == 4000
