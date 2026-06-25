# LLM-Graph

算是第一个正经弄的小项目，参考了其他开源agent框架自己做的（AI辅助理解，但是代码逻辑是自己理出来的），只要是为了完成一些简单的工作流。

使用langgraph框架，以及自己电脑上搭建的Qwen3.6-27B，依赖Vllm运行，现在也能够接入Deepseek API运行。

以及能够为自己的日常生活增加一点可以复用的小工具，娱乐小功能。

## 幽幽子对话机器人

目前QQ群聊里有一个小幽幽子机器人（不是那个大的正版幽幽子bot）
它的实现依靠qq_main_graph
但是小幽幽子机器人的Nonebot框架不在这里


## 自己调试，学习代码用

一般的功能都用tool_agent_graph，他有子agent拉起功能，能够完成一些比较一般的任务。

### 怎么使用tool_agent_graph

`tool_agent_graph` 是带工具调用能力的 agent graph，可以通过 `run_tool_agent()` 传入问题，让模型自动选择工具并返回结果。

示例用法：主目录创建一个test脚本
```python
import json
import logging
from pathlib import Path
from src.graphs.tool_agent_graph import run_tool_agent 

logging.basicConfig(level=logging.INFO)
question = f"你好，介绍一下自己，你有什么能力"

result = run_tool_agent(question, recursion_limit = 50, profile_name="deepseekv4-flash")
messages = result["messages"]

output_path = Path(__file__).resolve().parent / "tool_agent_result.json"

with output_path.open("w", encoding="utf-8") as f:
    json.dump(messages, f, ensure_ascii=False, indent=2, default=str)
```

## 开发计划

- [x] 子Agent编写和正常拉起
- [x] 路径查询以及文件查询，正常文本文件和docx文件的读取
- [x] 两层上下文压缩机制，实现tool返回结果的压缩以及LLM总结摘要
- [x] 能够调用模型的图像理解功能，做成了Imageread
- [x] QQ用的graph能够自动搜索历史记录并按意愿调用图像理解功能
- [x] 支持deepseek版本的接入，QQ用的版本也支持了
- [ ] 两层上下文机制触发仍超限时，更强硬的自动压缩方法
- [ ] ~~用subprocess写python脚本执行功能~~，增加OCR功能，RAG改成用skill读取内容并转md，对接某个正在使用的平台，然后看看能不能用3.5的小模型正常完成功能
- [ ] tool_agent_graph需要可以指定路径，然后能够自动拼接某.md文件到系统提示词中
- [ ] 支持skill载入和网上skill的使用
- [ ] 支持把记忆写成md格式
- [ ] 接入某个奇怪的TTS，实现日常娱乐小工具和任务安排等简单功能
- [ ] 把项目放到docker上（？）
- [ ] 不可能的幻想：想让他能够操作鼠标完成一些操作，比如玩我的世界