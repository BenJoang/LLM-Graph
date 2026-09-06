# Phoenix 工具记录缺失修复与另一台电脑同步步骤

更新日期：2026-09-05。

本文适用于 GUI 和独立 NoneBot 项目复用 LLM-Graph 的情况。另一台电脑上的项目路径请替换为实际路径；示例默认 Phoenix 与 Python 程序运行在同一台电脑。

## 1. 问题和修复内容

本次排查发现，一轮 GUI 对话有 14 次工具调用和 14 条工具返回，但 Phoenix 仅收到 2 条工具 Span；部分模型 Span 的父节点也缺失。因此不能把 Phoenix 中的调用数当作完整执行次数。

原配置使用 `register(batch=True)`，未明确指定协议。当前安装的 `arize-phoenix-otel==0.17.1` 默认选择 gRPC。本地实测约 5 MiB 的请求被拒绝：

```text
RESOURCE_EXHAUSTED
Received message larger than max (5243062 vs. 4194304)
```

图节点的输入输出包含历史消息，多个较大的 Span 合并上报可能超过本地 gRPC 的 4 MiB 接收限制，造成整批记录丢失。已复现这个限制，但没有取得原执行期间的客户端错误日志，不能逐条确认历史缺失记录的原因。

修复位于 `src/observability/phoenix.py`：保留批量上报，明确使用 HTTP/protobuf。

```python
register(
    project_name=project_name or "llm-graph",
    protocol="http/protobuf",
    auto_instrument=True,
    batch=True,
    verbose=False,
)
```

SDK 会从 `PHOENIX_COLLECTOR_ENDPOINT` 推导 HTTP 的 `/v1/traces` 路径。此修复没有裁剪提示词、历史消息或工具返回，也没有改变模型调用逻辑。HTTP 仍有服务器资源及部署环境限制，不代表支持无限大的请求。

## 2. 另一台电脑上的 LLM-Graph

1. 同步包含本次修复的代码，重点检查 `src/observability/phoenix.py` 中是否有 `protocol="http/protobuf"`。仅修改 `.env.example` 不会使旧代码生效。
2. 在该电脑的 LLM-Graph 根目录安装依赖：

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements-LLMv1.txt
   ```

3. 编辑已有 `.env`，确认以下三项；不要用示例文件覆盖已经填写的模型配置：

   ```dotenv
   PHOENIX_TRACING_ENABLED=true
   PHOENIX_COLLECTOR_ENDPOINT=http://127.0.0.1:6006
   PHOENIX_PROJECT_NAME=llm-graph
   ```

4. 确认本机 Phoenix 页面能打开，然后完全退出 GUI 并重新启动；独立启动的 API、CLI 进程也需要重启。

若 Python 后端运行在与 Phoenix 相同的 Compose 网络中，地址用 `http://phoenix:6006`。如果实际需要连接另一台电脑上的 Phoenix，`127.0.0.1` 不指向那台电脑，请参考 [部署文档的 Phoenix 部分](POSTGRES_DOCKER_SETUP.md#83-phoenix) 配置跨机器访问。HTTP 上报不要填 gRPC 的 `4317` 端口。

这次修复不需要修改或重启现有 Phoenix 容器，也不需要重建数据卷。

## 3. 独立 NoneBot 项目

NoneBot 项目不在本仓库里，因此同步 LLM-Graph **不会自动修改 NoneBot 插件或安装它的依赖**。

先使用真正运行机器人的 Python 安装：

```powershell
& "E:\Code Program\Nonebot_t1\test2\.venv\Scripts\python.exe" -m pip install "arize-phoenix-otel==0.17.1" "openinference-instrumentation-langchain==0.1.73"
& "E:\Code Program\Nonebot_t1\test2\.venv\Scripts\python.exe" -m pip check
```

在 `plugins/my_rules/__init__.py` 中，将初始化放在 LLM-Graph 路径加入 `sys.path` 之后、导入图之前。已经添加过的不要重复添加：

```python
LLM_TEST_DIR = Path(r"E:\Code Program\LLM-Graph")
if str(LLM_TEST_DIR) not in sys.path:
    sys.path.insert(0, str(LLM_TEST_DIR))

from src.observability import setup_phoenix_tracing

setup_phoenix_tracing()

from src.graphs.qq_main_graph import run_qq_main_agent
```

函数读取的是 LLM-Graph 根目录的 `.env`，不需要复制到 NoneBot。已有进程环境变量优先于 `.env`；如果终端或启动脚本设置过同名变量，需一并检查。调整后重启 NoneBot。只有触发图执行的消息才会产生相关追踪，普通 QQ 消息不会自动全部入库。

## 4. 验证是否生效

先检查容器 HTTP 接收日志（如果容器名称不同，请替换）：

```powershell
docker logs --tail 100 llm-graph-phoenix-managed
```

新调用应产生 `POST /v1/traces` 的成功响应。只有 UI 的 `GET /` 或 `POST /graphql` 成功，不能证明追踪已经上报。

在 GUI 中完成一轮包含多次工具调用的任务，然后在 Phoenix 的 `llm-graph` 项目中按开始时间找到对应 Trace：

- 将本轮实际完成的工具调用与 `TOOL` Span 逐项对照，重复使用同一工具也应分别记录。
- `tools`、`turn_aware_tools` 是包装节点，不应计为额外的工具调用。
- 检查模型和工具节点的父节点是否存在。查看同一轮请求，避免把历史消息中的工具调用混入本轮计数。
- 批量发送和服务端入库有延迟，待执行结束后稍等并刷新；若依然缺失，检查 GUI/NoneBot 终端的导出错误，而不只看容器健康状态。

本机修复验收：分别使用 LLM-Graph 和 NoneBot 的 `.venv`，执行不访问真实模型或外部工具的合成测试。14 次工具输出合计约 5.6 MB，另有 1 次模拟模型调用；两套环境均完整收到 17 个 Span（14 工具 + 1 模型 + 2 父节点），没有缺失父节点。测试记录放在独立的 `phoenix-transport-diagnostic` 项目中。

这证明新配置在本机能接收超过旧限制的测试记录；另一台电脑仍应完成上述实际任务核对。旧进程不会自动加载修复，以前漏掉的 Span 也不会自动补回。历史 Trace 的总耗时可能仍存在，但不完整的子节点不能用于精确统计调用数量或分解全部耗时。

参考：[Phoenix 上报端点](https://arize.com/docs/phoenix/resources/frequently-asked-questions/what-is-my-phoenix-endpoint/)、[Phoenix OTEL SDK](https://arize-phoenix.readthedocs.io/projects/otel/)。
