---
name: skill-starter-example
description: "示范如何编写一个带脚本和按需参考资料的 Skill：读取本地 JSON 项目列表并生成字段及分组摘要。此目录仅供复制学习，不应加入生产 Skill 列表。"
---

# Skill Starter Example

这是一个可以直接验证的完整样例，同时展示推荐的 Skill 结构。

## 工作流

1. 从当前 Skill 的目录解析 `scripts/summarize_items.py`。
2. 使用 Python 执行脚本，把用户提供的 JSON 文件绝对路径传给 `--input`。
3. 用户要求按字段统计时，增加 `--group-by <字段名>`。
4. 根据脚本输出回答，不虚构输入文件中不存在的字段。

调用示例：

```text
python scripts/summarize_items.py --input C:\data\items.json --group-by status
```

只有需要判断输入格式或排查字段错误时，才读取
[references/input-format.md](references/input-format.md)。

## 输出要求

- 说明总记录数和发现的字段。
- 指定分组字段时，列出各值对应的数量。
- 文件、JSON 或字段不合法时，直接报告脚本错误，不猜测结果。

## 复制成新 Skill

复制此目录后至少修改：目录名、frontmatter 中的 `name` 与
`description`、标题、工作流、脚本、参考资料，以及
`agents/openai.yaml` 中的界面文字和 `$skill-name`。

`description` 应明确“做什么、何时使用、何时不要使用”。只有重复且需要
确定性执行的逻辑才放进 `scripts/`；只在部分任务需要的详细资料放进
`references/`。完成后运行 `skill-creator` 提供的 `quick_validate.py`。
