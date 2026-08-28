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

- 搜索内容始终填写在 `pattern` 中。
- 普通文本搜索使用 `match_mode="literal"`。
- 正则表达式搜索使用 `match_mode="regex"`。
- `match_mode` 只表示匹配模式，不能填写搜索表达式。
- `path` 必须是绝对路径。

### EXAMPLES

普通文本搜索：

{"path":"E:\\project\\src","pattern":"build_graph","match_mode":"literal","include":"*.py"}

正则搜索：

{"path":"E:\\project\\src","pattern":"build_graph|run_agent","match_mode":"regex","include":"*.py"}