# LLM-Graph

算是第一个正经弄的小项目，参考了其他开源agent框架自己做的（AI辅助理解，但是代码逻辑是自己理出来的），只要是为了完成一些简单的工作流。

使用langgraph框架，以及自己电脑上搭建的Qwen3.6-27B，依赖Vllm运行，现在也能够接入Deepseek API运行。

以及能够为自己的日常生活增加一点可以复用的小工具，娱乐小功能。

## 环境安装

项目使用 Python 3.12。首次使用时，需要在项目根目录自行创建虚拟环境并安装依赖。
下面以 Conda 为例：

```powershell
conda create -n LLMv1 python=3.12 -y
conda activate LLMv1
python -m pip install --upgrade pip
python -m pip install -r requirements-LLMv1.txt
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

完成上面的环境安装和模型配置后，激活虚拟环境并启动对话：

```powershell
conda activate LLMv1
python tool_agent_chat.py
```

脚本会为每次新对话生成一个 `thread_id`。默认情况下，对话 checkpoint 保存在
`outputs/checkpoints/tool_agent.sqlite`。之后使用相同的 checkpoint 后端并传入同一个
ID，即可继续原来的对话：

```powershell
python tool_agent_chat.py --thread-id cli-20260817-120000-abcd1234
```

也可以选择模型和 Agent 工作目录：

```powershell
python tool_agent_chat.py `
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
python tool_agent_chat.py --help
```

### Checkpoint 存储后端

项目默认使用 SQLite，不需要额外启动数据库。`.env.example` 中的默认配置为：

```dotenv
LLM_GRAPH_CHECKPOINT_BACKEND=sqlite
LLM_GRAPH_CHECKPOINT_SQLITE_PATH=outputs/checkpoints/tool_agent.sqlite
```

如果需要让 CLI、API 和 GUI 共用 PostgreSQL checkpoint，可以启动 PostgreSQL 后在
个人 `.env` 中配置：

```dotenv
LLM_GRAPH_CHECKPOINT_BACKEND=postgres
LLM_GRAPH_CHECKPOINT_POSTGRES_URL=postgresql://llm_graph:<password>@127.0.0.1:5433/llm_graph?sslmode=disable
```

不要把包含真实密码的 `.env` 提交到仓库。通过 GUI 启动 FastAPI 时，应用会自动执行
LangGraph checkpoint 数据表的初始化和升级。如果只使用命令行，第一次连接该数据库前
可以手动执行一次：

```powershell
python -c "import asyncio; from src.persistence.checkpoints import setup_checkpoint_backend; asyncio.run(setup_checkpoint_backend())"
```

SQLite 和 PostgreSQL 中的旧会话不会自动互相迁移。切换后端后，只有目标后端中已经存在
的 `thread_id` 才能恢复；如需查看原来的 SQLite 会话，应切回 SQLite 或单独执行数据迁移。

## Windows GUI 工作台

项目提供 Electron + React 桌面开发版。它会自动通过 `conda run -n LLMv1`
启动本地 FastAPI，并使用随机端口和临时访问令牌连接后端。

首次启动和后续启动都可以运行：

```powershell
.\start_gui.ps1
```

也可以手动安装和启动前端：

```powershell
npm install --prefix desktop
npm run dev --prefix desktop
```

如果 `conda` 不在 PATH 中，可显式指定 Python：

```powershell
$env:LLM_GRAPH_PYTHON = "E:\Software\Anaconda3\envs\LLMv1\python.exe"
.\start_gui.ps1
```

GUI 会话标题、模型和工作目录等元数据始终保存在 `outputs/gui_state.sqlite`。消息
checkpoint 默认保存在 `outputs/checkpoints/tool_agent.sqlite`；当
`LLM_GRAPH_CHECKPOINT_BACKEND=postgres` 时，消息和 graph state 改为保存在配置的
PostgreSQL 数据库中。模型地址、数据库连接和密钥继续由 `.env` 与
`config/user_config.json` 管理，不会写入浏览器存储。

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
- [x] 增加多轮对话和支持中断以及断点续接功能（支持多轮对话之后它的checkpoints默认能实现了，不过可能跟普遍理解的断点续接不一样）
- [x] 两层上下文机制触发仍超限时，构建三次重试，每次使用更强硬的自动压缩方法
- [x] ~~增加OCR功能~~(用qwen多模态模型就行了)，~~RAG改成用skill读取内容~~。~~然后看看能不能用3.5的小模型正常完成功能~~。
- [ ] ~~支持skill载入~~和网上skill的使用
- [ ] 支持把记忆写成md格式
- [ ] 接入某个奇怪的TTS，实现日常娱乐小工具和任务安排等简单功能
- [ ] 把项目放到docker上（？）
- [ ] 不可能的幻想：想让他能够操作鼠标完成一些操作，比如玩我的世界
