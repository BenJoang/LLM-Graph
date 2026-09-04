# LLM-Graph 本地 PostgreSQL、pgvector、Embedding 与 Phoenix 部署记录

这份文档记录当前机器上 `E:\Code Program\llm-graph-postgres` 的部署思路，目标是即使原目录或 Docker 容器丢失，也能根据本文重新搭建一套与当前 LLM-Graph 项目兼容的本地服务。

本文只保存结构和操作方法，不保存真实密码或 API Key。示例中的密码必须替换。

## 1. 当前架构

当前基础设施由一个独立的 Docker Compose 项目管理，Compose 项目名为 `llm-graph-managed`。

```text
Windows / LLM-Graph
├─ PostgreSQL: 127.0.0.1:5434
│  ├─ PostgreSQL 16
│  ├─ pgvector
│  ├─ LangGraph checkpoint 表（启用 PostgreSQL checkpoint 时）
│  └─ rag.document_chunks（RAG 文档块和 1024 维向量）
├─ Qwen3 Embedding: 127.0.0.1:8001
│  └─ OpenAI-compatible /v1/embeddings
├─ Phoenix: 127.0.0.1:6006
│  ├─ Agent、LangGraph、LLM 和工具调用 trace
│  ├─ Dataset、Experiment 与 Evaluation UI
│  └─ OTLP HTTP 6006 / OTLP gRPC 4317
├─ Degoog: 127.0.0.1:4444
│  └─ 本地 Web 搜索服务，可选
└─ Valkey
   └─ 只在 Compose 内部供 Degoog 使用，不暴露宿主机端口
```

对 RAG 来说，必需服务只有 `postgres` 和 `embedding`。Phoenix 是独立的可观测性与评估服务；Degoog、Valkey 是同一套 Compose 中的其他本地能力，不参与向量入库和混合检索。

当前实测环境快照：

| 项目 | 当前值 |
|---|---|
| Docker Desktop | 29.7.2，Linux containers，WSL2 backend |
| Docker Compose | v5.4.0 |
| PostgreSQL | 16.15 |
| pgvector | 0.8.6 |
| Embedding 模型 | `Qwen/Qwen3-Embedding-4B` |
| 模型宿主机目录 | WSL `~/projects/qwen3-embedding-4b` |
| 模型目录大小 | 约 7.6 GB |
| 对外模型名 | `qwen3-embedding-4b` |
| 输出向量维度 | 1024 |
| PostgreSQL 数据卷 | `llm_graph_managed_postgres_data` |
| Phoenix 数据卷 | `llm_graph_managed_phoenix_data` |
| 当前 Phoenix 版本 | `20.7.0` |
| Phoenix UI / OTLP HTTP | `http://127.0.0.1:6006` |
| Phoenix OTLP gRPC | `127.0.0.1:4317` |
| Compose 网络 | `llm-graph-managed_default` |

`latest` 和 `pg16` 标签可能随时间变化，因此上表描述的是当前曾验证成功的状态，不代表未来重新拉取相同标签时一定得到完全相同的镜像内容。

当前镜像摘要留档：

```text
pgvector/pgvector@sha256:ccc6e83d6e35e931dc7c5def2022729d5a6c370318d099181995567ff1fb4d6b
vllm/vllm-openai@sha256:61fc8a896b0a4fbbbdc063bc4b0dbc25ce98e02b5050c24aeb7830ac02039b14
ghcr.io/degoog-org/degoog@sha256:a90a6e66765b7c05ec25c7bccaef55da3fc4e52ac0108abaebef81a3f6c1b4ba
valkey/valkey@sha256:a174b894902bd3367e330d47cc2054367dc4917701776aaf336f41d83b65ec7a
arizephoenix/phoenix@sha256:9be0666f810ed44cffd1ea9c5ff76477d1b1916c24c4c4a9f84e530f23f634c8
```

日常可以继续使用标签；如果以后要求字节级可复现，可将 Compose 中的镜像标签替换为以上 digest。

## 2. 前置条件

重新部署前确认：

1. Windows 已安装 Docker Desktop，并使用 WSL2 Linux container backend。
2. 已安装并启用一个 WSL2 Linux 发行版；当前机器使用 `Ubuntu-22.04`。
3. NVIDIA 驱动、Docker Desktop GPU 支持可用，因为 embedding 容器使用 `gpus: all`。
4. GPU 支持 `bfloat16`，并有足够显存运行 Qwen3-Embedding-4B。当前机器是 RTX 5080 16 GB。
5. WSL 文件系统中已经放好完整的 Qwen3-Embedding-4B 模型文件。

基础检查：

```powershell
docker version
docker compose version
wsl --list --verbose
nvidia-smi
```

模型目录至少应包含 `config.json`、tokenizer 文件、safetensors 权重和权重索引。当前目录可这样检查：

```powershell
wsl -d Ubuntu-22.04 -- bash -lc `
  "ls -lh /home/benjiang/projects/qwen3-embedding-4b && du -sh /home/benjiang/projects/qwen3-embedding-4b"
```

Compose 不会自动下载模型。重新部署到其他电脑时，可以自行从模型发布方下载 `Qwen/Qwen3-Embedding-4B`，但最终必须把 Compose 的宿主机挂载路径改成那台电脑上的真实模型目录。

## 3. 基础设施目录结构

在 LLM-Graph 项目旁边建立独立目录：

```text
E:\Code Program\
├─ LLM-Graph\
└─ llm-graph-postgres\
   ├─ .env                 # 真实服务密码，不提交
   ├─ .env.example         # 可提交的配置模板
   ├─ .gitignore
   ├─ docker-compose.yaml
   ├─ init\
   │  ├─ 001-init.sql
   │  └─ 002-rag-document-chunks.sql
   ├─ backups\             # 手动备份输出目录
   └─ degoog\data\         # Degoog 持久化配置
```

创建目录：

```powershell
$infraRoot = "E:\Code Program\llm-graph-postgres"
New-Item -ItemType Directory -Force `
  $infraRoot, `
  "$infraRoot\init", `
  "$infraRoot\backups", `
  "$infraRoot\degoog\data"
```

数据库文件不要直接绑定到 Windows 普通目录。当前方案使用 Docker named volume，避免 PostgreSQL 在跨文件系统 bind mount 上遇到权限或性能问题。

## 4. 环境变量文件

创建 `E:\Code Program\llm-graph-postgres\.env.example`：

```dotenv
POSTGRES_USER=llm_graph
POSTGRES_PASSWORD=replace_with_a_strong_password
POSTGRES_DB=llm_graph
POSTGRES_PORT=5434

PHOENIX_HOST=127.0.0.1
PHOENIX_HTTP_PORT=6006
PHOENIX_GRPC_PORT=4317

RAG_EMBEDDING_API_KEY=replace_with_a_private_embedding_key

DEGOOG_VERSION=latest
DEGOOG_HOST=127.0.0.1
DEGOOG_PORT=4444
DEGOOG_SETTINGS_PASSWORDS=replace_with_a_strong_settings_password
DEGOOG_PUBLIC_INSTANCE=false
DEGOOG_DEFAULT_SEARCH_LANGUAGE=zh-CN
DEGOOG_PUID=1000
DEGOOG_PGID=1000
TZ=Asia/Shanghai
```

复制为真实配置并替换三个密码：

```powershell
Set-Location "E:\Code Program\llm-graph-postgres"
Copy-Item .env.example .env
notepad .env
```

`.gitignore` 内容：

```gitignore
.env
backups/*.dump
backups/*.sql
backups/*.backup
degoog/data/
```

不要把 `.env`、数据库备份或 Degoog 运行时数据提交到 Git。

## 5. Docker Compose 文件

创建 `E:\Code Program\llm-graph-postgres\docker-compose.yaml`：

```yaml
name: llm-graph-managed

services:
  postgres:
    image: pgvector/pgvector:pg16
    container_name: llm-graph-postgres-managed
    restart: unless-stopped

    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}

    ports:
      - "127.0.0.1:${POSTGRES_PORT:-5434}:5432"

    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./init:/docker-entrypoint-initdb.d:ro

    healthcheck:
      test:
        - CMD-SHELL
        - pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}
      interval: 5s
      timeout: 5s
      retries: 10
      start_period: 10s

  phoenix:
    image: arizephoenix/phoenix:latest
    container_name: llm-graph-phoenix-managed
    restart: unless-stopped

    ports:
      - "${PHOENIX_HOST:-127.0.0.1}:${PHOENIX_HTTP_PORT:-6006}:6006"
      - "${PHOENIX_HOST:-127.0.0.1}:${PHOENIX_GRPC_PORT:-4317}:4317"

    environment:
      PHOENIX_WORKING_DIR: /mnt/data

    volumes:
      - phoenix_data:/mnt/data

    healthcheck:
      test:
        - CMD
        - python3
        - -c
        - "import urllib.request; urllib.request.urlopen('http://127.0.0.1:6006', timeout=5)"
      interval: 10s
      timeout: 5s
      retries: 10
      start_period: 20s

    # 个人开发负载的保护性上限，不是 Phoenix 官方最低要求。
    mem_limit: 2g

  embedding:
    image: vllm/vllm-openai:latest
    restart: unless-stopped
    ipc: host
    gpus: all

    ports:
      - "127.0.0.1:8001:8000"

    volumes:
      - /home/benjiang/projects/qwen3-embedding-4b:/model:ro

    command:
      - --model
      - /model
      - --served-model-name
      - qwen3-embedding-4b
      - --runner
      - pooling
      - --dtype
      - bfloat16
      - --max-model-len
      - "8192"
      - --max-num-seqs
      - "4"
      - --gpu-memory-utilization
      - "0.65"
      - --hf-overrides
      - '{"is_matryoshka":true}'
      - --pooler-config
      - '{"dimensions":1024}'
      - --api-key
      - ${RAG_EMBEDDING_API_KEY:?RAG_EMBEDDING_API_KEY is required}

  degoog:
    image: ghcr.io/degoog-org/degoog:${DEGOOG_VERSION:-latest}
    container_name: llm-graph-degoog-managed
    restart: unless-stopped

    depends_on:
      valkey:
        condition: service_healthy

    ports:
      - "${DEGOOG_HOST:-127.0.0.1}:${DEGOOG_PORT:-4444}:4444"

    environment:
      TZ: ${TZ:-Asia/Shanghai}
      PUID: ${DEGOOG_PUID:-1000}
      PGID: ${DEGOOG_PGID:-1000}
      DEGOOG_SETTINGS_PASSWORDS: ${DEGOOG_SETTINGS_PASSWORDS:?DEGOOG_SETTINGS_PASSWORDS is required}
      DEGOOG_PUBLIC_INSTANCE: ${DEGOOG_PUBLIC_INSTANCE:-false}
      DEGOOG_DEFAULT_SEARCH_LANGUAGE: ${DEGOOG_DEFAULT_SEARCH_LANGUAGE:-zh-CN}
      DEGOOG_VALKEY_URL: redis://valkey:6379

    volumes:
      - ./degoog/data:/app/data

  valkey:
    image: docker.io/valkey/valkey:9-alpine
    restart: unless-stopped

    command:
      - valkey-server
      - --save
      - "30"
      - "1"
      - --loglevel
      - warning

    volumes:
      - valkey_data:/data

    healthcheck:
      test:
        - CMD
        - valkey-cli
        - ping
      interval: 5s
      timeout: 3s
      retries: 10
      start_period: 5s

volumes:
  postgres_data:
    name: llm_graph_managed_postgres_data
  phoenix_data:
    name: llm_graph_managed_phoenix_data
  valkey_data:
```

模型挂载路径是当前机器的 WSL 路径。换机器时必须修改：

```yaml
volumes:
  - /home/<WSL用户名>/projects/qwen3-embedding-4b:/model:ro
```

不要把聊天模型挂到这里。该容器必须使用真正的 Embedding 模型，并通过 pooling runner 提供 `/v1/embeddings`。

## 6. PostgreSQL 首次初始化 SQL

### 6.1 启用 pgvector 并建立 schema

创建 `init\001-init.sql`：

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS rag;
```

### 6.2 建立当前 RAG 表

当前部署最初只把扩展和 schema 放进初始化目录，`rag.document_chunks` 是后续实验时创建的。为了让新部署一次启动后就达到相同结构，建议补充 `init\002-rag-document-chunks.sql`：

```sql
CREATE TABLE IF NOT EXISTS rag.document_chunks (
    id bigserial PRIMARY KEY,
    tenant_id text NOT NULL,
    document_id text NOT NULL,
    chunk_id text NOT NULL,
    source text NOT NULL,
    section text,
    content text NOT NULL,
    content_hash text NOT NULL,
    embedding_model text NOT NULL,
    embedding vector(1024) NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, chunk_id)
);

CREATE INDEX IF NOT EXISTS document_chunks_tenant_document_idx
ON rag.document_chunks (tenant_id, document_id);
```

这里没有建立 HNSW/IVFFlat 索引。当前知识库很小，先使用精确向量扫描；数据量和延迟确实需要时再增加向量索引。

当前 BM25 在 Python 中使用 `jieba + rank-bm25` 临时构建，不依赖 PostgreSQL 全文索引，所以数据库中也没有 BM25 索引。

### 初始化脚本的重要限制

`/docker-entrypoint-initdb.d` 中的 SQL 只会在 PostgreSQL 数据卷第一次创建、数据库目录为空时执行。

如果 `llm_graph_managed_postgres_data` 已经存在，修改 `init` 目录不会自动更新数据库。已有数据库需要手动执行新增 SQL，例如：

```powershell
docker compose exec -T postgres `
  psql -U llm_graph -d llm_graph `
  -f /docker-entrypoint-initdb.d/002-rag-document-chunks.sql
```

## 7. 第一次启动

进入基础设施目录：

```powershell
Set-Location "E:\Code Program\llm-graph-postgres"
```

先检查 Compose 能否解析：

```powershell
docker compose config --services
```

注意：完整的 `docker compose config` 会展开 `.env`，输出中可能含有密码，不要把结果直接复制到公开日志。

拉取并启动：

```powershell
docker compose pull
docker compose up -d
docker compose ps
```

只启动 RAG 核心服务也可以：

```powershell
docker compose up -d postgres embedding
```

正常状态应该看到：

```text
postgres     healthy   127.0.0.1:5434->5432
phoenix      healthy   127.0.0.1:6006->6006、127.0.0.1:4317->4317
embedding    running   127.0.0.1:8001->8000
degoog       healthy   127.0.0.1:4444->4444
valkey       healthy   仅 Compose 内部端口
```

Embedding 首次启动通常比 PostgreSQL 慢，因为需要加载约 7.6 GB 模型到 GPU。查看日志：

```powershell
docker compose logs -f embedding
```

看到服务监听 8000 且模型加载完成后，按 `Ctrl+C` 退出日志跟随；不会停止容器。

## 8. 服务验证

### 8.1 PostgreSQL 和 pgvector

```powershell
docker compose exec postgres `
  pg_isready -U llm_graph -d llm_graph
```

```powershell
docker compose exec postgres `
  psql -U llm_graph -d llm_graph `
  -c "SELECT version();"
```

```powershell
docker compose exec postgres `
  psql -U llm_graph -d llm_graph `
  -c "SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';"
```

确认 RAG 表：

```powershell
docker compose exec postgres `
  psql -U llm_graph -d llm_graph `
  -c "\d rag.document_chunks"
```

### 8.2 Embedding API

PowerShell 不会自动把 Compose `.env` 导入进当前进程。验证时手动输入 embedding key，避免把它写进命令历史：

```powershell
$embeddingKey = Read-Host "Embedding API Key"
$headers = @{ Authorization = "Bearer $embeddingKey" }

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8001/v1/models" `
  -Headers $headers
```

验证实际向量维度：

```powershell
$body = @{
    model = "qwen3-embedding-4b"
    input = @("这是一次 embedding 测试")
} | ConvertTo-Json

$response = Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8001/v1/embeddings" `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body

$response.data[0].embedding.Count
```

预期输出：

```text
1024
```

如果不是 1024，不能直接写入当前的 `vector(1024)` 列。需要先让模型服务维度和数据库表结构一致。

### 8.3 Phoenix

打开 Phoenix：

```text
http://127.0.0.1:6006
```

验证 HTTP 服务：

```powershell
$response = Invoke-WebRequest `
  -UseBasicParsing `
  -Uri "http://127.0.0.1:6006"

$response.StatusCode
```

预期状态码是 `200`。查看容器和持久卷：

```powershell
docker compose ps phoenix
docker compose logs --tail 100 phoenix
docker volume inspect llm_graph_managed_phoenix_data
```

当前 Phoenix 仅绑定 `127.0.0.1`，默认认证关闭但局域网无法直接访问。不要为了远程访问简单改成 `6006:6006`；需要跨机器访问时，应先按照 Phoenix 官方文档启用认证、设置强 `PHOENIX_SECRET`、修改默认管理员密码并创建系统 API Key。

Phoenix 不需要 GPU。个人开发先使用独立 SQLite volume，避免可观测性数据与业务 PostgreSQL 互相影响。数据量或并发明显增长后，可以建立独立的 Phoenix 数据库和用户，再通过 `PHOENIX_SQL_DATABASE_URL` 接入 PostgreSQL 14 以上版本。

2026-09-04 在当前机器上的验收结果：Phoenix `20.7.0` 容器为 `healthy`，UI 返回 HTTP `200`，空载内存约 `468 MiB`；`phoenix-install-check` 与批处理版 `phoenix-batch-install-check` 两条测试 span 均可从 `llm-graph` project 的 REST API 查询到，且容器重建后旧 span 仍存在。

### 8.4 Degoog（可选）

打开：

```text
http://127.0.0.1:4444
```

首次启动后使用 `.env` 中的设置密码进入管理页面，在 Store 中至少安装并启用一个 Web Engine。若客户端需要兼容 SearXNG 返回结构，在 Settings -> Server 中启用 `Serve the SearXNG API shape`。

健康检查：

```powershell
Invoke-RestMethod "http://127.0.0.1:4444/health"
```

## 9. LLM-Graph 项目配置

基础设施 `.env` 和 LLM-Graph 根目录 `.env` 是两份不同文件：

- 基础设施 `.env` 用于创建容器和数据库账号。
- 项目 `.env` 用于让 Python 应用连接这些服务。

在 `E:\Code Program\LLM-Graph\.env` 中配置：

```dotenv
RAG_POSTGRES_URL=postgresql://llm_graph:<与基础设施POSTGRES_PASSWORD相同的密码>@127.0.0.1:5434/llm_graph?sslmode=disable

RAG_EMBEDDING_BASE_URL=http://127.0.0.1:8001/v1
RAG_EMBEDDING_API_KEY=<与基础设施RAG_EMBEDDING_API_KEY相同的值>
RAG_EMBEDDING_MODEL=qwen3-embedding-4b
RAG_EMBEDDING_DIMENSIONS=1024

# Phoenix 默认关闭；确认本地 Phoenix 正常后再开启
PHOENIX_TRACING_ENABLED=true
PHOENIX_COLLECTOR_ENDPOINT=http://127.0.0.1:6006
PHOENIX_PROJECT_NAME=llm-graph
```

最重要的对应关系：

| 基础设施配置 | 项目配置 |
|---|---|
| `POSTGRES_USER` | PostgreSQL URL 用户名 |
| `POSTGRES_PASSWORD` | PostgreSQL URL 密码 |
| `POSTGRES_DB` | PostgreSQL URL 数据库名 |
| `POSTGRES_PORT` | PostgreSQL URL 端口 |
| `RAG_EMBEDDING_API_KEY` | `RAG_EMBEDDING_API_KEY` |
| `--served-model-name` | `RAG_EMBEDDING_MODEL` |
| `pooler-config dimensions=1024` | `RAG_EMBEDDING_DIMENSIONS=1024` |

Phoenix tracing 需要项目 Python 环境安装：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-LLMv1.txt
```

项目依赖中固定了：

```text
arize-phoenix-otel==0.17.1
openinference-instrumentation-langchain==0.1.73
```

`tool_agent_chat.py` 和 FastAPI/GUI 入口会调用 `src.observability.setup_phoenix_tracing()`。只有 `PHOENIX_TRACING_ENABLED=true` 时才注册 OpenInference；关闭或删除该变量不会上传 trace，也不影响 Agent 正常运行。

项目在 Windows 主机运行时使用 `http://127.0.0.1:6006`。如果以后把 Python 后端也放入这份 Compose，则容器内不能使用 `127.0.0.1`，应改成 `http://phoenix:6006`。

第一次验证 tracing：

```powershell
Set-Location "E:\Code Program\LLM-Graph"
.\.venv\Scripts\python.exe tool_agent_chat.py
```

完成一次普通对话后，在 `http://127.0.0.1:6006` 的 Traces 页面选择 `llm-graph` project，应当能看到 LangGraph、ChatOpenAI 和工具调用 spans。Phoenix 只负责记录和评估，不代理模型请求；本地 Qwen 和 DeepSeek 的原有连接方式保持不变。

项目的 LangGraph checkpoint 和 RAG 可以使用同一个 PostgreSQL 实例，也可以分开。当前默认 checkpoint 仍是 SQLite：

```dotenv
LLM_GRAPH_CHECKPOINT_BACKEND=sqlite
LLM_GRAPH_CHECKPOINT_SQLITE_PATH=outputs/checkpoints/tool_agent.sqlite
```

如果要让 checkpoint 也进入当前 PostgreSQL：

```dotenv
LLM_GRAPH_CHECKPOINT_BACKEND=postgres
LLM_GRAPH_CHECKPOINT_POSTGRES_URL=postgresql://llm_graph:<密码>@127.0.0.1:5434/llm_graph?sslmode=disable
```

首次切换后需要执行项目提供的 checkpoint 初始化流程。

## 10. 项目端到端验证

回到项目目录：

```powershell
Set-Location "E:\Code Program\LLM-Graph"
```

入库 README：

```powershell
.\.venv\Scripts\python.exe -m src.rag.ingest_cli README.md `
  --tenant-id study `
  --document-id project-readme `
  --batch-size 8 `
  --max-concurrency 3
```

混合检索：

```powershell
.\.venv\Scripts\python.exe -m src.rag.hybrid_search_cli `
  "Windows GUI 怎么启动" `
  --tenant-id study `
  --top-k 3
```

运行检索评估：

```powershell
.\.venv\Scripts\python.exe -m src.rag.evaluate_cli
```

当前验证过的基线是 6 个问题中，纯向量和混合检索的 Hit@1、Hit@3、MRR 均为满分。这个结果只说明最小链路工作正常；语料扩大后需要继续扩充评估集。

## 11. 日常启动、停止与日志

启动全部服务：

```powershell
Set-Location "E:\Code Program\llm-graph-postgres"
docker compose up -d
```

查看状态：

```powershell
docker compose ps
```

查看单个服务日志：

```powershell
docker compose logs --tail 100 postgres
docker compose logs --tail 100 embedding
docker compose logs --tail 100 phoenix
docker compose logs --tail 100 degoog
```

持续跟随日志：

```powershell
docker compose logs -f embedding
```

停止但保留容器：

```powershell
docker compose stop
```

停止并删除容器、保留 named volumes：

```powershell
docker compose down
```

重新创建容器不会删除数据库数据：

```powershell
docker compose up -d --force-recreate
```

不要随意运行：

```powershell
docker compose down -v
```

`-v` 会删除 Compose 管理的数据卷，其中包括 PostgreSQL 数据库。只有确认已有可恢复备份、并明确想重建空数据库时才能使用。

## 12. PostgreSQL 备份与恢复

`backups` 目录只是预留位置，不会自动备份。下面的方法让 `pg_dump` 在 Linux 容器内生成二进制 custom-format 文件，再复制到 Windows，避免旧版 Windows PowerShell 重定向二进制输出时损坏文件。

### 12.1 创建备份

```powershell
Set-Location "E:\Code Program\llm-graph-postgres"

$backupName = "llm_graph_$(Get-Date -Format 'yyyyMMdd_HHmmss').backup"

docker compose exec -T postgres `
  pg_dump `
  -U llm_graph `
  -d llm_graph `
  -Fc `
  -f "/tmp/$backupName"

docker compose cp `
  "postgres:/tmp/$backupName" `
  ".\backups\$backupName"

docker compose exec -T postgres rm "/tmp/$backupName"
```

确认备份文件存在且大小不是 0：

```powershell
Get-Item ".\backups\$backupName"
```

### 12.2 恢复备份

恢复会覆盖或冲突已有对象。优先在新建的空数据库或专门的恢复环境中演练，不要直接对唯一的生产数据执行。

把备份复制进容器：

```powershell
$backupName = "要恢复的文件名.backup"

docker compose cp `
  ".\backups\$backupName" `
  "postgres:/tmp/$backupName"
```

恢复到已经准备好的空数据库：

```powershell
docker compose exec -T postgres `
  pg_restore `
  -U llm_graph `
  -d llm_graph `
  --no-owner `
  "/tmp/$backupName"
```

如果数据库不是空的，先制定清理或新建数据库方案；不要为了省事直接添加 `--clean`，因为它会删除现有对象。

## 13. 常见故障

### PostgreSQL healthy，但项目连不上

检查：

1. 项目 URL 使用的是宿主机端口 5434，不是容器内部端口 5432。
2. 项目 URL 的用户名、密码、数据库名和基础设施 `.env` 一致。
3. URL 中特殊字符已经进行百分号编码；最简单的本地方案是生成不含 URL 保留字符的强随机密码。
4. `docker compose ps` 中 PostgreSQL 显示 healthy。

### 修改 POSTGRES_PASSWORD 后密码没有变化

官方 PostgreSQL 镜像中的 `POSTGRES_*` 变量只在空数据卷初始化时创建账号。已有 named volume 时，修改 `.env` 不会自动修改数据库内部密码，需要在 PostgreSQL 中执行 `ALTER ROLE`，或在确认可删除数据后重建卷。

### 修改 init SQL 后没有生效

同理，初始化 SQL 只在空数据卷首次启动时执行。对已有数据库应手动执行迁移 SQL。

### Embedding 容器立即退出

依次检查：

1. WSL 模型目录是否真实存在。
2. Compose 中的宿主机路径是否指向正确 WSL 用户。
3. 模型文件是否完整。
4. Docker Desktop 是否启用了 GPU 支持。
5. `docker compose logs embedding` 中是否报告显存不足。

显存不足时，先减小 `--max-num-seqs` 或 `--gpu-memory-utilization`，但要重新验证并发入库性能。

### 返回的向量不是 1024 维

当前表定义是 `embedding vector(1024)`。必须保证 Compose 的：

```text
--hf-overrides {"is_matryoshka":true}
--pooler-config {"dimensions":1024}
```

与项目的：

```text
RAG_EMBEDDING_DIMENSIONS=1024
```

完全一致。

### Windows 异步 psycopg 报 ProactorEventLoop 错误

Windows CLI 入口需要使用 Selector event loop：

```python
asyncio.run(
    main(),
    loop_factory=asyncio.SelectorEventLoop,
)
```

项目现有的 RAG CLI 已采用这一写法。不要在数据库库模块导入时全局修改 event-loop policy，应由程序入口选择事件循环。

## 14. 当前方案的边界

1. PostgreSQL 和 embedding 只绑定 `127.0.0.1`，默认不能从局域网其他机器访问，这是有意的安全边界。
2. Embedding 镜像仍使用 `latest`，长期维护时应考虑固定 digest 或经过验证的版本标签。
3. 数据库目前没有自动迁移工具，首次建表依赖 init SQL，后续变更需要手动迁移。
4. `backups` 没有定时任务，需要手动执行或以后添加计划任务。
5. Python BM25 每次查询会重新加载租户 chunk 并构建索引，只适合当前小规模知识库。
6. 当前没有 HNSW/IVFFlat 向量索引；数据规模增长后应先测量延迟，再决定是否增加。
7. Phoenix 当前使用独立 SQLite named volume，适合单机开发；大量 traces、多用户并发或正式生产环境应迁移到独立 PostgreSQL 数据库。
8. Phoenix 默认认证关闭，但端口只绑定 `127.0.0.1`。启用局域网或公网访问前必须先配置认证和网络边界。
9. `PHOENIX_TRACING_ENABLED=true` 会记录提示词、用户输入、工具参数、工具返回和 RAG 上下文。QQ 历史、个人记忆、密钥或其他敏感内容应在进入 trace 前脱敏，或者对相应入口关闭 tracing。
10. Phoenix 镜像目前使用 `latest` 方便首次安装。验证稳定后应把镜像固定到具体版本或 digest，并在升级前备份 `llm_graph_managed_phoenix_data`。

## 15. 最小重建检查清单

当需要在新机器上重新搭建时，按顺序确认：

- [ ] Docker Desktop、WSL2、NVIDIA GPU 支持可用。
- [ ] WSL 中存在完整的 Qwen3-Embedding-4B 模型目录。
- [ ] 创建 `llm-graph-postgres` 目录结构。
- [ ] 写入 `.env`、Compose 和两个 init SQL。
- [ ] 模型 bind mount 已改成新机器的真实路径。
- [ ] `docker compose up -d` 成功。
- [ ] Phoenix UI 可通过 `http://127.0.0.1:6006` 打开，数据卷 `llm_graph_managed_phoenix_data` 存在。
- [ ] PostgreSQL healthy，pgvector 扩展存在。
- [ ] `rag.document_chunks` 表存在且 embedding 是 `vector(1024)`。
- [ ] `/v1/models` 可访问。
- [ ] `/v1/embeddings` 返回 1024 维向量。
- [ ] LLM-Graph `.env` 中数据库密码与 embedding key 匹配。
- [ ] README 入库、混合检索和评估命令通过。
- [ ] 安装 Phoenix Python 依赖，开启 `PHOENIX_TRACING_ENABLED` 后能在 `llm-graph` project 看到一次 Agent trace。
- [ ] 建立并验证第一份 PostgreSQL 备份。
