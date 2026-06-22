# qq_memory_search

## DESCRIPTION
读取指定 QQ 群的历史消息记录，按时间窗口从最新消息向更早消息翻页。返回文本摘要、发送人、时间和图片 URL。
## PROMPT
当需要查看 QQ 群聊历史、最近群里聊了什么、寻找图片消息、寻找图片 URL 时使用本工具。
第一次调用时 cursor 留空，表示从最新消息开始读取。
如果返回 has_more=true 且当前结果不足以完成任务，应继续调用本工具，并把 next_cursor 作为下一次的 cursor。
如果返回的消息中包含 image_urls，且任务需要理解图片内容，应调用 imageread 分析对应图片。
本工具只读取 jsonl 记录，不分析图片内容。