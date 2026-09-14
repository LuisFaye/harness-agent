-- MySQL DDL for SB Agent memory.
-- `sb-agent memory init` 会自动创建数据库，然后在该数据库中执行下面的建表语句。

CREATE TABLE IF NOT EXISTS user_preferences (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    user_id VARCHAR(128) NOT NULL COMMENT '用户标识，默认 default',
    pref_key VARCHAR(128) NOT NULL COMMENT '偏好键名',
    pref_value TEXT NOT NULL COMMENT '偏好值',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uniq_user_pref (user_id, pref_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户结构化偏好表（如 home_city、risk_preference）';

CREATE TABLE IF NOT EXISTS skill_preferences (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    user_id VARCHAR(128) NOT NULL COMMENT '用户标识',
    skill_name VARCHAR(128) NOT NULL COMMENT 'Skill 稳定名称',
    pref_key VARCHAR(128) NOT NULL COMMENT 'Skill 自己定义的配置键',
    pref_value TEXT NOT NULL COMMENT '配置值；敏感值仍应只保存环境变量名',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uniq_user_skill_pref (user_id, skill_name, pref_key),
    KEY idx_user_skill (user_id, skill_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='按用户和 Skill 隔离的私有偏好表';

CREATE TABLE IF NOT EXISTS conversation_memories (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    user_id VARCHAR(128) NOT NULL COMMENT '用户标识',
    memory_text TEXT NOT NULL COMMENT '记忆正文',
    source VARCHAR(64) NOT NULL DEFAULT 'manual' COMMENT '来源：manual 手动 / auto 自动提取',
    expires_at DATETIME NULL COMMENT '可选过期时间；为空表示长期有效',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (id),
    KEY idx_user_created (user_id, created_at),
    KEY idx_user_expires (user_id, expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='长期自然语言记忆表（手动 remember 或自动提取）';

ALTER TABLE conversation_memories
    ADD COLUMN expires_at DATETIME NULL COMMENT '可选过期时间；为空表示长期有效' AFTER source;

ALTER TABLE conversation_memories
    ADD INDEX idx_user_expires (user_id, expires_at);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    user_id VARCHAR(128) NOT NULL COMMENT '用户标识',
    session_id VARCHAR(128) NOT NULL COMMENT '会话标识',
    role VARCHAR(16) NOT NULL COMMENT '消息角色（user / assistant / system 等）',
    content TEXT NOT NULL COMMENT '消息正文',
    run_id VARCHAR(128) NULL COMMENT '产生这轮消息的 Agent 运行 ID，用于刷新后查看执行过程',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (id),
    KEY idx_user_session_created (user_id, session_id, created_at),
    KEY idx_user_created (user_id, created_at),
    KEY idx_run_id (run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='短期多轮对话原文表（按 user_id + session_id 存储）';

ALTER TABLE conversation_messages
    ADD COLUMN run_id VARCHAR(128) NULL COMMENT '产生这轮消息的 Agent 运行 ID' AFTER content;

ALTER TABLE conversation_messages
    ADD INDEX idx_run_id (run_id);

ALTER TABLE conversation_messages
    ADD COLUMN attachment_ids JSON NULL COMMENT '用户消息附带的附件 ID 列表' AFTER run_id;

CREATE TABLE IF NOT EXISTS conversation_session_summaries (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    user_id VARCHAR(128) NOT NULL COMMENT '用户标识',
    session_id VARCHAR(128) NOT NULL COMMENT '会话标识',
    summary_text TEXT NOT NULL COMMENT '压缩后的会话摘要正文',
    summarized_message_count INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '已纳入摘要的消息条数',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uniq_user_session (user_id, session_id),
    KEY idx_user_updated (user_id, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='会话历史压缩摘要表（窗口外旧消息的规则摘要）';

CREATE TABLE IF NOT EXISTS conversation_session_titles (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    user_id VARCHAR(128) NOT NULL COMMENT '用户标识',
    session_id VARCHAR(128) NOT NULL COMMENT '会话标识',
    title VARCHAR(64) NOT NULL COMMENT '首次有效问答生成的语义短标题',
    source VARCHAR(32) NOT NULL DEFAULT 'model' COMMENT '标题来源：model 模型生成 / manual 人工设置',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
    PRIMARY KEY (id),
    UNIQUE KEY uniq_user_session (user_id, session_id),
    KEY idx_user_updated (user_id, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='会话语义标题表（首次生成后保持稳定）';

CREATE TABLE IF NOT EXISTS conversation_shares (
    id CHAR(36) NOT NULL COMMENT '分享记录 UUID',
    owner_user_id VARCHAR(128) NOT NULL COMMENT '会话所有者用户 ID',
    recipient_user_id VARCHAR(128) NOT NULL COMMENT '接收分享的用户 ID',
    session_id VARCHAR(128) NOT NULL COMMENT '被分享的会话 ID',
    permission VARCHAR(32) NOT NULL DEFAULT 'read_only' COMMENT '固定为 read_only',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
    revoked_at DATETIME(6) NULL COMMENT '撤销时间',
    PRIMARY KEY (id),
    KEY idx_share_recipient_created (recipient_user_id, created_at),
    KEY idx_share_owner_session (owner_user_id, session_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='指定成员可见的只读会话分享';

CREATE TABLE IF NOT EXISTS conversation_session_copies (
    id CHAR(36) NOT NULL COMMENT '复制记录 UUID',
    source_share_id CHAR(36) NOT NULL COMMENT '来源分享记录 ID',
    source_owner_user_id VARCHAR(128) NOT NULL COMMENT '原会话所有者用户 ID',
    source_session_id VARCHAR(128) NOT NULL COMMENT '原会话 ID',
    target_user_id VARCHAR(128) NOT NULL COMMENT '复制后的会话所有者用户 ID',
    target_session_id VARCHAR(128) NOT NULL COMMENT '复制后的新会话 ID',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '复制时间',
    PRIMARY KEY (id),
    UNIQUE KEY uniq_share_target_copy (source_share_id, target_user_id),
    KEY idx_copy_target_session (target_user_id, target_session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='共享会话复制来源审计';

CREATE TABLE IF NOT EXISTS task_history (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    user_id VARCHAR(128) NOT NULL COMMENT '用户标识',
    task_type VARCHAR(64) NOT NULL COMMENT '任务类型（如 ask、plan）',
    user_input TEXT NOT NULL COMMENT '用户输入原文',
    output_summary TEXT NULL COMMENT '输出摘要',
    status VARCHAR(32) NOT NULL COMMENT '执行状态（如 success、error）',
    error_message TEXT NULL COMMENT '失败时的错误信息',
    metadata_json JSON NULL COMMENT '扩展元数据 JSON',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (id),
    KEY idx_user_created (user_id, created_at),
    KEY idx_user_task (user_id, task_type, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='任务执行历史表（ask / 命令等，供排查与上下文摘要）';

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id VARCHAR(128) NOT NULL COMMENT '一次 Agent 运行的全链路唯一标识',
    user_id VARCHAR(128) NOT NULL COMMENT '运行所属用户标识',
    session_id VARCHAR(128) NOT NULL COMMENT '运行所属会话标识',
    status VARCHAR(32) NOT NULL COMMENT 'queued / running / success / error / cancel_requested / cancelled 等',
    query_text LONGTEXT NOT NULL COMMENT '本次运行收到的用户输入',
    response_text LONGTEXT NULL COMMENT '本次运行最终回答',
    execution_source VARCHAR(64) NULL COMMENT 'agent_loop / skill_command / local 等执行来源',
    model_id VARCHAR(128) NULL COMMENT '本次运行使用的模型 ID',
    error_stage VARCHAR(64) NULL COMMENT '失败阶段',
    error_message TEXT NULL COMMENT '失败原因',
    duration_ms BIGINT UNSIGNED NULL COMMENT '运行耗时毫秒数',
    metadata_json JSON NULL COMMENT '最终结果、计划、Trace 和 Prompt 等扩展元数据',
    started_at DATETIME(6) NOT NULL COMMENT '运行开始时间，按 UTC 写入',
    finished_at DATETIME(6) NULL COMMENT '运行结束时间，按 UTC 写入',
    cancel_requested_at DATETIME(6) NULL COMMENT '收到取消请求的时间',
    cancelled_at DATETIME(6) NULL COMMENT '执行器确认取消的时间',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '记录创建时间',
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '最后更新时间',
    PRIMARY KEY (run_id),
    KEY idx_agent_runs_user_started (user_id, started_at),
    KEY idx_agent_runs_user_status_started (user_id, status, started_at),
    KEY idx_agent_runs_session_started (session_id, started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent 运行生命周期主表';

CREATE TABLE IF NOT EXISTS agent_run_events (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    run_id VARCHAR(128) NOT NULL COMMENT '所属 Agent 运行 ID',
    sequence INT UNSIGNED NOT NULL COMMENT '同一次运行内从 1 开始递增的事件序号',
    event_type VARCHAR(64) NOT NULL COMMENT 'run_started / response_delta / run_completed 等事件类型',
    event_timestamp VARCHAR(40) NOT NULL COMMENT '事件原始 ISO 8601 时间，保留时区信息',
    payload_json JSON NOT NULL COMMENT '事件 payload 对象',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '事件落库时间',
    PRIMARY KEY (id),
    UNIQUE KEY uniq_agent_run_sequence (run_id, sequence),
    KEY idx_agent_run_events_run_id (run_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent 流式运行事件表';

CREATE TABLE IF NOT EXISTS agent_run_evidence (
    evidence_id VARCHAR(64) NOT NULL COMMENT 'Agent 可按需读取的证据唯一标识',
    run_id VARCHAR(128) NOT NULL COMMENT '证据所属运行 ID',
    user_id VARCHAR(128) NOT NULL COMMENT '证据所属用户标识',
    session_id VARCHAR(128) NOT NULL COMMENT '证据所属会话标识',
    tool_name VARCHAR(128) NOT NULL COMMENT '产生该证据的工具名称',
    content_type VARCHAR(64) NOT NULL COMMENT 'text/plain 或 application/json',
    raw_content LONGTEXT NOT NULL COMMENT '完整工具结果',
    character_count INT UNSIGNED NOT NULL COMMENT '原始字符数',
    estimated_tokens INT UNSIGNED NOT NULL COMMENT '原始结果估算 Token 数',
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '证据创建时间',
    PRIMARY KEY (evidence_id),
    KEY idx_agent_run_evidence_run (run_id, created_at),
    KEY idx_agent_run_evidence_session (user_id, session_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent 长工具结果证据表';

CREATE TABLE IF NOT EXISTS tool_call_logs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    user_id VARCHAR(128) NOT NULL COMMENT '用户标识',
    tool_name VARCHAR(128) NOT NULL COMMENT '工具名称',
    input_json JSON NULL COMMENT '调用入参 JSON',
    output_summary TEXT NULL COMMENT '输出摘要',
    status VARCHAR(32) NOT NULL COMMENT '调用状态',
    error_message TEXT NULL COMMENT '失败时的错误信息',
    duration_ms INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '耗时（毫秒）',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (id),
    KEY idx_user_created (user_id, created_at),
    KEY idx_user_tool (user_id, tool_name, created_at),
    KEY idx_status_created (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='工具调用日志表';

CREATE TABLE IF NOT EXISTS react_pause_states (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    user_id VARCHAR(128) NOT NULL COMMENT '用户标识',
    session_id VARCHAR(128) NOT NULL COMMENT '会话标识',
    run_id VARCHAR(128) NOT NULL COMMENT '关联一次 ReAct 暂停执行的运行 ID',
    status VARCHAR(32) NOT NULL COMMENT '状态：needs_confirmation / paused / cancelled / completed',
    payload_json JSON NOT NULL COMMENT '暂停时的运行快照 JSON',
    expires_at DATETIME NULL COMMENT '过期时间，过期后可自动清理',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (id),
    UNIQUE KEY uniq_user_session (user_id, session_id),
    KEY idx_run_status (run_id, status),
    KEY idx_expires_at (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ReAct 暂停态持久化表';

CREATE TABLE IF NOT EXISTS skill_session_states (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    user_id VARCHAR(128) NOT NULL COMMENT '用户标识',
    session_id VARCHAR(128) NOT NULL COMMENT '会话标识',
    skill_name VARCHAR(128) NOT NULL COMMENT '技能名称',
    tool_name VARCHAR(128) NULL COMMENT '工具名称',
    interaction_mode VARCHAR(32) NOT NULL DEFAULT 'deterministic' COMMENT '技能交互模式：deterministic / react_tool',
    run_id VARCHAR(128) NULL COMMENT '关联一次 ask / skill 执行的运行 ID',
    status VARCHAR(32) NOT NULL COMMENT '状态：needs_input / needs_confirmation / paused / completed / cancelled',
    inputs_json JSON NOT NULL COMMENT '当前已收集输入 JSON',
    state_json JSON NOT NULL COMMENT '运行时附加状态 JSON',
    expires_at DATETIME NULL COMMENT '过期时间，过期后可自动清理',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    PRIMARY KEY (id),
    UNIQUE KEY uniq_user_session (user_id, session_id),
    KEY idx_skill_status (skill_name, status),
    KEY idx_run_id (run_id),
    KEY idx_expires_at (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='技能多轮会话状态表';

CREATE TABLE IF NOT EXISTS scheduled_jobs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    user_id VARCHAR(128) NOT NULL COMMENT '用户标识',
    job_type VARCHAR(64) NOT NULL COMMENT '任务类型（目前为 daily_report）',
    title VARCHAR(255) NOT NULL COMMENT '任务标题',
    status VARCHAR(32) NOT NULL DEFAULT 'enabled' COMMENT '任务状态（enabled / disabled 等）',
    schedule_kind VARCHAR(32) NOT NULL DEFAULT 'daily_time' COMMENT '调度方式（daily_time 表示每天固定时刻）',
    schedule_time VARCHAR(8) NOT NULL COMMENT '每天执行时刻，格式 HH:MM',
    timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Shanghai' COMMENT 'schedule_time 所属时区',
    config_json JSON NULL COMMENT '业务配置 JSON（如日报仓库列表 repos）',
    last_run_at DATETIME NULL COMMENT '最近一次开始执行时间',
    next_run_at DATETIME NOT NULL COMMENT '下次应被 worker 扫描执行的时间',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
    PRIMARY KEY (id),
    KEY idx_user_status_next_run (user_id, status, next_run_at),
    KEY idx_status_next_run (status, next_run_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='定时任务定义表';

CREATE TABLE IF NOT EXISTS scheduled_job_runs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    job_id BIGINT UNSIGNED NOT NULL COMMENT '关联 scheduled_jobs.id',
    user_id VARCHAR(128) NOT NULL COMMENT '用户标识',
    status VARCHAR(32) NOT NULL COMMENT '执行状态',
    started_at DATETIME NOT NULL COMMENT '开始时间',
    finished_at DATETIME NULL COMMENT '结束时间',
    output_text LONGTEXT NULL COMMENT '执行输出（如日报正文）',
    error_message TEXT NULL COMMENT '失败时的错误信息',
    metadata_json JSON NULL COMMENT '扩展元数据 JSON',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
    PRIMARY KEY (id),
    KEY idx_job_created (job_id, created_at),
    KEY idx_user_created (user_id, created_at),
    KEY idx_status_created (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='定时任务执行记录表';

CREATE TABLE IF NOT EXISTS auth_users (
    id VARCHAR(36) NOT NULL COMMENT '用户 UUID',
    email VARCHAR(254) NOT NULL COMMENT '规范化后的登录邮箱',
    password_hash VARCHAR(255) NOT NULL COMMENT 'scrypt 密码哈希',
    display_name VARCHAR(80) NOT NULL COMMENT '页面显示名称',
    role VARCHAR(32) NOT NULL DEFAULT 'member' COMMENT 'owner / member',
    status VARCHAR(32) NOT NULL DEFAULT 'active' COMMENT 'active / disabled',
    failed_login_count INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '连续登录失败次数',
    locked_until DATETIME(6) NULL COMMENT '登录锁定截止时间（UTC）',
    last_login_at DATETIME(6) NULL COMMENT '最近登录时间（UTC）',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '更新时间（UTC）',
    PRIMARY KEY (id),
    UNIQUE KEY uniq_auth_users_email (email),
    KEY idx_auth_users_status_created (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='登录用户表';

CREATE TABLE IF NOT EXISTS auth_sessions (
    id VARCHAR(36) NOT NULL COMMENT '会话 UUID',
    user_id VARCHAR(36) NOT NULL COMMENT '登录用户 ID',
    token_hash CHAR(64) NOT NULL COMMENT '会话令牌 SHA-256，不保存明文',
    expires_at DATETIME(6) NOT NULL COMMENT '会话过期时间（UTC）',
    last_seen_at DATETIME(6) NOT NULL COMMENT '最近访问时间（UTC）',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间（UTC）',
    revoked_at DATETIME(6) NULL COMMENT '撤销时间（UTC）',
    PRIMARY KEY (id),
    UNIQUE KEY uniq_auth_sessions_token_hash (token_hash),
    KEY idx_auth_sessions_user_expires (user_id, expires_at),
    KEY idx_auth_sessions_expires (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='服务端登录会话表';

CREATE TABLE IF NOT EXISTS auth_invite_codes (
    id VARCHAR(36) NOT NULL COMMENT '邀请码 UUID',
    code VARCHAR(64) NOT NULL COMMENT '可由所有者重新查看的明文邀请码',
    label VARCHAR(80) NOT NULL DEFAULT '' COMMENT '管理员用途备注',
    use_count INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '已成功注册人数',
    max_uses INT UNSIGNED NULL COMMENT '最多注册人数；NULL 表示不限制',
    expires_at DATETIME(6) NULL COMMENT '过期时间（UTC）；NULL 表示永久有效',
    created_by VARCHAR(36) NOT NULL COMMENT '创建邀请码的所有者用户 ID',
    revoked_at DATETIME(6) NULL COMMENT '停用时间（UTC）',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间（UTC）',
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '更新时间（UTC）',
    PRIMARY KEY (id),
    UNIQUE KEY uniq_auth_invite_codes_code (code),
    KEY idx_auth_invite_codes_status (expires_at, revoked_at, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='可配置人数和期限的批量注册邀请码';

CREATE TABLE IF NOT EXISTS auth_invite_code_uses (
    id VARCHAR(36) NOT NULL COMMENT '使用记录 UUID',
    invite_code_id VARCHAR(36) NOT NULL COMMENT '邀请码 ID',
    user_id VARCHAR(36) NOT NULL COMMENT '通过邀请码注册的用户 ID',
    used_at DATETIME(6) NOT NULL COMMENT '成功注册时间（UTC）',
    PRIMARY KEY (id),
    UNIQUE KEY uniq_auth_invite_code_user (invite_code_id, user_id),
    KEY idx_auth_invite_code_uses_time (invite_code_id, used_at),
    KEY idx_auth_invite_code_uses_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='邀请码成功注册使用记录';

CREATE TABLE IF NOT EXISTS auth_password_resets (
    id VARCHAR(36) NOT NULL COMMENT '重置请求 UUID',
    user_id VARCHAR(36) NOT NULL COMMENT '要重置密码的用户 ID',
    token_hash CHAR(64) NOT NULL COMMENT '重置令牌 SHA-256，不保存明文',
    created_by VARCHAR(36) NOT NULL COMMENT '创建链接的所有者用户 ID',
    expires_at DATETIME(6) NOT NULL COMMENT '重置链接过期时间（UTC）',
    used_at DATETIME(6) NULL COMMENT '使用时间（UTC）',
    revoked_at DATETIME(6) NULL COMMENT '撤销时间（UTC）',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间（UTC）',
    PRIMARY KEY (id),
    UNIQUE KEY uniq_auth_password_resets_token_hash (token_hash),
    KEY idx_auth_password_resets_user_created (user_id, created_at),
    KEY idx_auth_password_resets_status (expires_at, used_at, revoked_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='管理员签发的一次性密码重置链接';

CREATE TABLE IF NOT EXISTS auth_audit_logs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    actor_user_id VARCHAR(36) NULL COMMENT '操作人用户 ID，匿名操作为空',
    action VARCHAR(64) NOT NULL COMMENT '安全事件类型',
    target_user_id VARCHAR(36) NULL COMMENT '被操作用户 ID',
    detail_json JSON NULL COMMENT '不含密钥和正文的补充信息',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间（UTC）',
    PRIMARY KEY (id),
    KEY idx_auth_audit_actor_created (actor_user_id, created_at),
    KEY idx_auth_audit_action_created (action, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='认证与账号管理审计日志';
