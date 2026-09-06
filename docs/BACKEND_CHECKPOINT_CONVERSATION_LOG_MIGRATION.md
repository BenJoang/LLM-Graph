# SQL 对话事实源双后端实现

## 最终架构

普通后端、CLI、HTTP API、GUI、Tool Agent 和 TTS 统一使用 SQL 对话运行协调器。
LangGraph 以 `checkpointer=None` 编译，运行时不再写 checkpoint，也不设置
`durability`。旧 checkpoint 仅作为迁移来源和回滚数据保留。

四张业务表的职责如下：

- `conversation_sessions`：长期会话与下一个轮次编号。
- `conversation_runs`：一次用户请求的运行状态。终态为 `completed`、
  `cancelled`、`failed` 或 `interrupted`。
- `conversation_events`：唯一永久对话事实源，按 Run 幂等追加完整消息。
- `conversation_context`：模型读取的最新投影，包含 `through_event_id`、版本、
  `projected_messages` 和 `compression_session`。

`conversation_migrations` 是迁移账本，不属于运行时对话模型。

## 运行流程

```text
创建 Run
  -> 读取 conversation_context（不存在时从 completed events 重建）
  -> 追加用户消息
  -> 无 checkpointer 执行受管 Graph
  -> 每个节点的完整消息按 message_id 幂等写入 events
  -> 写入成功后通知 GUI SSE
  -> 原子提交 completed Run 与新 context 投影
```

只有 `completed` Run 会进入下一轮上下文。取消、失败和中断的事件仍保留用于审计，
但不会被模型或 GUI 的正式历史读取。

本项目按本地单实例运行：启动时把遗留 `running` Run 直接改为
`interrupted`，不做 heartbeat、中途恢复或工具重放。用户停止写为 `cancelled`，
超时和普通异常写为 `failed`。

## 数据库配置

统一使用 `LLM_GRAPH_DATABASE_URL`：

```dotenv
# SQLite
LLM_GRAPH_DATABASE_URL=sqlite:///outputs/conversations/conversation.sqlite3

# PostgreSQL
LLM_GRAPH_DATABASE_URL=postgresql://user:password@127.0.0.1:5433/database
```

未配置时，为兼容旧安装，会跟随旧 checkpoint 后端选择数据库；SQLite 使用独立的
`outputs/conversations/conversation.sqlite3`，不会与 checkpoint 表混放。

PostgreSQL 通过行锁串行化同一会话的 Run；SQLite 使用 `BEGIN IMMEDIATE`。
两者运行相同的 Store 契约测试。

## Graph 注册与兼容

当前受管 Graph：

- `src.graphs.tool_agent_graph:astream_tool_agent`
- `src.graphs.tts_graph:astream_tool_agent`

它们都通过各自的 `build_graph(checkpointer=None)` 执行。旧 `thread_id` 参数继续作为
`session_id` 的兼容别名。未注册或源码缺失的 Graph 不能创建新会话或恢复运行。

GUI 的标题、模型、工作目录等仍保存在 `outputs/gui_state.sqlite`；消息、Run 状态和
上下文保存在业务 Store。删除顺序为业务会话后 GUI 元数据，重复删除保持成功。

## 旧数据迁移

迁移不会随应用启动自动执行：

```powershell
# 只读分析
.\.venv\Scripts\python.exe -m scripts.migrate_checkpoint_conversations --dry-run

# 写入 LLM_GRAPH_DATABASE_URL 指向的业务库
.\.venv\Scripts\python.exe -m scripts.migrate_checkpoint_conversations --apply
```

脚本使用稳定 Run ID `migration:{session_id}:{turn_no}` 和消息原 ID；每个 GUI 会话写入
迁移账本与 checksum。重复执行只校验或补齐，不会重复插入。

正式迁移结果：

- 29 sessions，其中 6 个空会话；
- 2634 events、164 runs；
- 155 completed、9 interrupted；
- 缺失 `computer_use_graph` 的会话迁移后已归档；
- GUI 元数据库迁移前备份在 `outputs`；
- 旧 2450 行 checkpoint 未删除。

## 验证

```powershell
# SQLite + 无模型协调器/GUI 测试
.\.venv\Scripts\python.exe -m pytest -q `
  tests/test_conversation_store.py `
  tests/test_tool_agent_runner.py `
  tests/test_gui_backend.py

# 同时执行 PostgreSQL Store 契约
$env:LLM_GRAPH_TEST_POSTGRES='1'
.\.venv\Scripts\python.exe -m pytest -q tests/test_conversation_store.py
```

消息编解码覆盖 Human、AI、Tool、System、多模态、tool calls、usage 和 artifact。
包含 NUL 的历史工具输出会使用可逆文本编码写入 PostgreSQL JSONB，读取后恢复原文。
