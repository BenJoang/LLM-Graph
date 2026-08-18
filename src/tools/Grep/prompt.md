# grep

## DESCRIPTION

在指定文件或目录中按关键词搜索内容，返回匹配文件、行号和代码片段。

## PROMPT

### WHEN_TO_USE

- 不知道关键词出现在哪个文件时
- 需要在项目或指定目录中定位代码、配置或文档时
- 需要获取关键词所在文件和行号时

### WHEN_NOT_TO_USE

- 已经知道文件，并且需要读取大段内容时，使用 read_file
- 不用于语义搜索，只匹配文件中实际出现的文字
- 不用于搜索网络内容

### INPUT_RULES

- `path` 必须是文件或目录的绝对路径
- 搜索当前项目时，使用 system prompt 中 `<working-directory>` 的值
- 也可以搜索其他位置的绝对路径
- `keyword` 默认按普通文本匹配
- 需要正则表达式时设置 `regex=true`
- 使用 `include` 限制文件类型，例如 `*.py`
- `context` 控制匹配位置前后返回多少行
- 结果较多时使用更具体的 `path`、`include` 或 `keyword`

### LIMITS

- 默认最多返回 50 个匹配
- 单行最多返回 500 个字符
- 总结果存在字符限制
- `truncated=true` 表示仍有其他结果
- 搜索超时时应缩小 path 或 include
- 默认遵循 `.gitignore`，且不主动搜索隐藏文件