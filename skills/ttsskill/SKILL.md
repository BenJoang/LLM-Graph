---
name: ttsskill
description: "使用局域网 IndexTTS 2.5 服务，以项目内的胡桃参考音频克隆角色音色并生成或播放中英混合语音。用于用户要求让胡桃说一句话、朗读文本、生成角色语音或播放 TTS；不要用于普通文本回复或其他角色音色。"
---

# 胡桃语音合成

使用本 skill 的 `scripts/speak.py` 调用 OpenAI 兼容接口 `/v1/audio/speech`。默认服务地址是 `http://192.168.10.71:8092`，可用环境变量 `INDEXTTS_URL` 或脚本参数 `--api-base` 覆盖。

默认参考音频是重新筛选的 `assets/hutao-default.wav`，对应文字是 `assets/hutao-default.txt`。这是约 9.74 秒、响度适中、停顿自然的单人对白。脚本会把音频作为 data URI 发送，因此 TTS 服务不需要访问本机磁盘，也不需要先注册 voice。

## 调用

先从 `skill_tool` 返回值取得 `skill_dir`，再用 `python_tool` 执行绝对路径：

```text
<skill_dir>\scripts\speak.py
```

把待朗读文本作为第一个参数传入，`python_tool` 的 `timeout` 设为 `120` 秒。

- 默认行为就是仅播放：不加模式参数。脚本使用系统临时 WAV 播放，并在播放结束后立即删除，不写入 `outputs/tts/`；`--play-only` 是这个默认行为的显式写法。
- 用户明确要求生成或保存音频时，加 `--save`。
- 用户明确要求既保存又播放时，加 `--play`。
- 用户指定输出位置时，加 `--output <绝对路径.wav>`；`--output` 本身也代表保存。未指定路径的保存模式写入项目的 `outputs/tts/`。
- 可用 `--speed 0.25..4.0` 调节语速，用 `--seed <整数>` 固定随机种子。
- 默认按中英混合文本处理（IndexTTS 语言代码 `zhen`）。纯中文、英文、日文或粤语可分别传 `--language zh`、`--language en`、`--language ja` 或 `--language yue`；同时兼容 `Chinese`、`English` 等原有写法。
- 用户明确希望使用最初导入的样本时，可同时传 `--reference-audio <skill_dir>\vo_hutao_teammate_yunjin_01.wav` 和 `--reference-text-file <skill_dir>\vo_hutao_teammate_yunjin_01.lab`。
- 用户明确希望使用另一段较短的备用样本时，可同时传 `--reference-audio <skill_dir>\assets\hutao-reference.wav` 和 `--reference-text-file <skill_dir>\assets\hutao-reference.txt`。

示例参数：

```text
["今天天气真不错，要不要一起出去走走？"]
```

脚本成功时会输出 JSON。仅播放时返回 `saved: false` 和 `output: null`；保存模式下 `output` 是生成的 WAV 绝对路径，应在回复中提供可点击文件链接。

## 失败处理

- 连接失败：提示确认 IndexTTS 服务仍在 `8092` 端口运行，或设置正确的 `INDEXTTS_URL`。
- HTTP 4xx/5xx：转述脚本输出的服务错误，不要用相同参数连续重试。
- 参考音频或文字缺失：不要改用不匹配的台词；恢复同名的 `.wav + .lab` 成对文件。
- 默认使用重新筛选的约 9.74 秒样本；用户导入的约 14.7 秒样本和先前选择的约 7.8 秒样本仅作备用。除非用户明确指定新角色或新样本，不要再随机替换它们。
