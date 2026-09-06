# LLM-Graph

算是第一个正经弄的小项目，参考了其他开源agent框架自己做的（AI辅助理解，但是代码逻辑是自己理出来的），只要是为了完成一些简单的工作流。

使用langgraph框架，以及自己电脑上搭建的Qwen3.6-27B，依赖Vllm运行，现在也能够接入Deepseek API运行。

以及能够为自己的日常生活增加一点可以复用的小工具，娱乐小功能。

## 环境安装

项目使用 Python 3.12，并把 Python 虚拟环境固定在项目根目录的 `.venv` 中，不需要
安装 Conda。首次使用前请先安装：

- Python 3.12，建议从 python.org 安装并启用 Windows Python Launcher（`py.exe`）
- Node.js LTS（包含 npm）

然后在项目根目录运行一键安装脚本：

```powershell
.\setup.ps1
```

也可以双击 `setup.bat`。安装脚本会创建 `.venv`、安装
`requirements-LLMv1.txt` 中的 Python 依赖，并安装 `desktop` 的 npm 依赖。
`.venv` 是本机生成目录，不应提交到 Git；删除后重新运行安装脚本即可恢复。

如需手动安装，等价命令为：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-LLMv1.txt
npm install --prefix desktop
```

复制环境变量示例文件：

```powershell
Copy-Item .env.example .env
```

然后根据 `config/user_config.json` 中所选 profile 的 `base_url_env` 和
`api_key_env`，在 `.env` 中填写对应的模型服务地址和 API Key。本项目是模型客户端，
本地 Qwen 模型需要另行启动兼容 OpenAI API 的推理服务；也可以直接配置 DeepSeek API。

## 幽幽子对话机器人

目前QQ群聊里有一个小幽幽子机器人（不是那个大的正版幽幽子bot）
它的实现依靠qq_main_graph
但是小幽幽子机器人的Nonebot框架不在这里


## 自己调试，学习代码用

一般的功能都用tool_agent_graph，他有子agent拉起功能，能够完成一些比较一般的任务。

### 怎么使用tool_agent_graph

`tool_agent_graph` 是带工具调用能力的 agent graph。项目根目录提供了
`tool_agent_chat.py`，可以直接在终端中进行多轮对话，不再需要临时创建 test 脚本。

完成上面的环境安装和模型配置后，使用项目虚拟环境启动对话：

```powershell
.\.venv\Scripts\python.exe tool_agent_chat.py
```

脚本会为每次新对话生成一个 `thread_id`（兼容字段，实际作为 `session_id`）。对话消息
永久追加到 SQL 业务表。之后连接同一业务库并传入同一个 ID，即可继续原来的对话：

```powershell
.\.venv\Scripts\python.exe tool_agent_chat.py --thread-id cli-20260817-120000-abcd1234
```

也可以选择模型和 Agent 工作目录：

```powershell
.\.venv\Scripts\python.exe tool_agent_chat.py `
  --profile deepseekv4-flash `
  --vision-profile qwen3-vl `
  --working-dir "E:\Code Program\LLM-Graph"
```

对话中支持以下命令：

- `/help`：查看帮助
- `/thread`：显示当前会话 ID
- `/new`：创建一个空白会话
- `/save`：将当前会话导出到 `outputs/cli_session_snapshot.json`
- `/exit` 或 `/quit`：退出

查看所有启动参数：

```powershell
.\.venv\Scripts\python.exe tool_agent_chat.py --help
```

### 对话存储后端

普通 CLI、API 和 GUI 运行不再使用 LangGraph 持久 checkpointer。通过
`LLM_GRAPH_DATABASE_URL` 选择 SQLite 或 PostgreSQL：

```dotenv
# 本地 SQLite
LLM_GRAPH_DATABASE_URL=sqlite:///outputs/conversations/conversation.sqlite3

# 或 PostgreSQL
LLM_GRAPH_DATABASE_URL=postgresql://llm_graph:<password>@127.0.0.1:5433/llm_graph?sslmode=disable
```

不配置时会兼容旧环境选择数据库，但 SQLite 始终使用独立的 conversation 文件。
`conversation_events` 是唯一永久对话事实；失败、取消和中断的消息保留用于审计，
但不会进入后续模型上下文。旧 checkpoint 仅供迁移和回滚，迁移命令见
[`docs/BACKEND_CHECKPOINT_CONVERSATION_LOG_MIGRATION.md`](docs/BACKEND_CHECKPOINT_CONVERSATION_LOG_MIGRATION.md)。

不要把包含真实密码的 `.env` 提交到仓库。

## Windows GUI 工作台

项目提供 Electron + React 桌面开发版。它会自动使用项目内
`.venv\Scripts\python.exe` 启动本地 FastAPI，并使用随机端口和临时访问令牌连接后端。

首次使用先执行一次安装，后续直接启动：

```powershell
.\setup.ps1
.\start_gui.ps1
```

也可以分别双击 `setup.bat` 和 `start_gui.bat`。

完成安装后，也可以直接启动前端开发服务器；Electron 会自动寻找项目内 `.venv`：

```powershell
npm run dev --prefix desktop
```

如需临时使用另一个 Python 3.12 虚拟环境，可以显式覆盖解释器；普通使用不需要设置：

```powershell
$env:LLM_GRAPH_PYTHON = "E:\other-project\.venv\Scripts\python.exe"
.\start_gui.ps1
```

GUI 会话标题、模型和工作目录等元数据始终保存在 `outputs/gui_state.sqlite`。消息、
Run 状态和模型上下文投影保存在 `LLM_GRAPH_DATABASE_URL` 指向的业务库。模型地址、
数据库连接和密钥继续由 `.env` 与
`config/user_config.json` 管理，不会写入浏览器存储。

每个空会话可以选择受管 Graph。目前注册了 Tool Agent 和 TTS，两者都通过统一 SQL
协调器以 `checkpointer=None` 执行。新增 Graph 时需要先加入受管注册表，不能直接填写
任意导入路径绕过 SQL。已有消息的会话会锁定 Graph。

不同 GUI 会话可以同时运行，但同一个会话一次只允许一个任务。关闭 GUI 会取消仍在
运行的任务。永久删除会先清除业务会话及事件，再删除 GUI 元数据，重复调用保持幂等。

## 开发计划

- [x] 子Agent编写和正常拉起
- [x] 路径查询以及文件查询，正常文本文件和docx文件的读取
- [x] 两层上下文压缩机制，实现tool返回结果的压缩以及LLM总结摘要
- [x] 能够调用模型的图像理解功能，做成了Imageread
- [x] QQ用的graph能够自动搜索历史记录并按意愿调用图像理解功能
- [x] 支持deepseek版本的接入，QQ用的版本也支持了
- [x] tool_agent_graph需要可以指定路径，然后能够自动拼接某.md文件到系统提示词中
- [x] 用subprocess写python脚本执行功能
- [x] 使用飞书机器人对接现在使用的平台，完成相关任务（目前使用3.6 27B完成功能）
- [x] 使用 SQL 对话事实源支持多轮对话；本地单实例启动时将遗留运行标记为 interrupted
- [x] 两层上下文机制触发仍超限时，构建三次重试，每次使用更强硬的自动压缩方法
- [x] ~~增加OCR功能~~(用qwen多模态模型就行了)，~~RAG改成用skill读取内容~~。~~然后看看能不能用3.5的小模型正常完成功能~~。
- [ ] ~~支持skill载入~~和网上skill的使用
- [x] ~~支持把记忆写成md格式~~（还是用数据库为基础的RAG吧）
- [x] 接入某个奇怪的TTS，实现日常娱乐小工具和任务安排等简单功能(全部都能通过skill实现)
- [x] 把项目放到docker上（项目通过docker启动多个服务）
- [·] 不可能的幻想：想让他能够操作鼠标完成一些操作(已经能够玩魔塔了)，比如玩我的世界
