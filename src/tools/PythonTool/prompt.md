# python_tool

## DESCRIPTION
执行 Python 脚本文件或一段 Python inline 代码，并返回退出码、标准输出和标准错误。
## PROMPT

### WHEN_TO_USE
当用户需要的功能要用到某个已给出的'.py' 脚本完成时，或者调试脚本执行结果、验证 Python 项目中的脚本是否能正常运行，或需要使用指定虚拟环境的 Python 解释器执行脚本时，或者需要执行一段临时Python代码完成函数功能时，使用这个工具

### WHEN_NOT_TO_USE
不要把它当作通用 shell、bash、cmd 或 PowerShell 使用。
不要用它执行非 Python 命令。
不要用它运行需要交互输入的程序。
不要在没有用户需求的情况下执行破坏性代码，例如删除文件、批量覆盖文件、修改系统配置、安装包、启动长期后台进程。

### INPUT_RULES

#### 通用参数
- `mode`：执行模式，可选值为 `script` 或 `inline`。默认为 `script`。
- `cwd`：可选，执行时的工作目录。
  - `mode=script` 时，不填默认使用脚本所在目录。
  - `mode=inline` 时，不填默认使用当前项目工作目录。
- `args`：可选，传给脚本或 `python -c` 代码的参数列表。每个参数单独作为列表元素填写，不要拼成一整条命令字符串。
- `python_path`：可选，用于指定 Python 解释器路径，例如某个项目的 `.venv/Scripts/python.exe`。不填时使用当前运行环境的默认 Python。
- `timeout`：可选，脚本最大执行时间，单位秒，默认为 30，最大为 120。

#### script 模式
- `mode` 填 `script`。
- `script_path`：必填，要执行的 `.py` 文件绝对路径。必须指向真实存在的 Python 脚本文件。
- `code`：不能填写。
- 执行形式等价于：
  `python script_path args...`

#### inline 模式
- `mode` 填 `inline`。
- `code`：必填，通过 `python -c` 执行的 Python 代码字符串。
- `script_path`：不能填写。
- 执行形式等价于：
  `python -c code args...`
- 如果需要返回结构化结果，推荐在 Python 代码中使用 `json.dumps` 输出 JSON。

### LIMITS
- script 模式只允许执行 `.py` 文件。
- inline 模式只允许执行 Python 代码字符串，不支持 shell 管道、重定向、&&、|、环境变量展开等 shell 语法。
- `args` 会作为参数列表传入，不经过 shell 解析。
- 如果需要特定虚拟环境，必须显式填写 `python_path`。
- 如果脚本执行失败，根据 `stderr`、`stdout` 和退出码判断原因；不要在没有新信息的情况下反复调用。
- 如果脚本超时，说明脚本可能在等待输入、死循环或运行时间过长，应向用户说明超时原因并建议缩小任务或调整脚本。

### SAFETY
- 执行前确认代码意图和影响范围。
- 不要运行会删除、覆盖、移动大量文件的代码，除非用户明确要求。
- 不要访问项目范围外的敏感路径，除非用户明确提供路径并要求处理。
- 不要在 inline 代码中启动长期服务、后台进程或无限循环。
- 不要通过 inline 代码安装依赖、修改环境变量、修改系统配置，除非用户明确要求。
- 需要输出复杂结果时，优先输出 JSON；不要输出大量无关日志。
