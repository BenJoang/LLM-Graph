# memory_write

## DESCRIPTION
修改 memory 目录中的 YAML 长期记忆文件。

## PROMPT
仅在确认需要保存长期信息后使用本工具。

action 的含义：
- `set`：替换指定字段。
- `append`：向列表追加一条记录，适合追加事件或曾用名。
- `merge`：向 mapping 合并字段，适合新增成员或更新成员资料。

限制：
- 禁止覆盖整个文件。
- 写入前应先使用 memory_search 检查已有内容。
- 不要重复追加已经存在的记录。
- `create_missing=true` 仅用于明确需要创建的新字段。