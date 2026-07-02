---
name: wuxiwaterskill
description: 查询巫溪水雨情站点数据。可获取各站点近若干小时的水雨情摘要，或获取各站点最近一次测流数据。需要通过 python_tool 执行 scripts 目录下的入口脚本。当出现关键词“重庆”、“巫溪”、“大宁河”、“田坝镇”时可以调用
---
# wuxiwaterskill
这个 skill 用于查询无锡水雨情站点数据，并把接口返回的 JSON 数据处理成适合 LLM 回答问题的纯文本。

当前提供两个入口脚本：

1. `scripts/get_one_day_data.py`
   - 用于获取各站点最近一天的综合水雨情摘要，也可以指定hours获取最近n小时内的情况。
   - 默认查询最近 24 小时。
   - 输出内容包括各站点最大累计雨量、最大雷达水深、最大雷达表面流速、最大算法测量水深、最大算法测量流速等。
   - 适合问题：
     - “过去24小时各站点水雨情怎么样？”
     - “最近一天哪些站点水位/流速/雨量最大？”
     - “汇总一下近24小时水雨情”
     - “分析最近 n 小时水雨情”

2. `scripts/get_latest_discharge.py`
   - 用于获取各站点最近一次测流数据。如果询问最近情况，指的是这一个功能。
   - 默认查询最近 2 小时。
   - 输出内容包括各站点最近一次算法测量水位、平均流速、流量、时间、状态等，总结时要把雷达和算法测量的结果都包含在内。
   - 适合问题：
     - "最近一小时/两小时情况如何？"
     - “各站点最新测流数据是多少？”
     - “最近一次测流结果”
     - “当前各站点水位和流速”
     - “最新算法测量数据”

## 调用方式

必须使用 `python_tool` 执行脚本。

不要直接读取接口，不要手写 HTTP 请求，不要自己拼接环境变量。脚本内部已经完成 token 获取、接口请求和数据处理。

## 脚本路径

综合水雨情摘要脚本：
E:\Code Program\LLM-Graph\skills\wuxiwaterskill\scripts\get_one_day_data.py
最近一次测流数据脚本：
E:\Code Program\LLM-Graph\skills\wuxiwaterskill\scripts\get_latest_discharge.py

## 参数
两个脚本都支持：
--hours
查询最近多少小时的数据。
get_one_day_data.py 默认值是 24。
get_latest_discharge.py 默认值是 2。

--limit
每类数据最大返回条数。
get_one_day_data.py 默认值是 20000。
get_latest_discharge.py 默认值是 200。

## 选择脚本规则
当用户询问“过去 n 小时”“最近一天”“水雨情摘要”“最大雨量”“最大水深”“最大流速”“分析水雨情”时，调用：
get_one_day_data.py
当用户询问“最新测流”“最近一次测流”“当前测流数据”“最新水位”“最新流速”“最新算法测量数据”时，调用：
get_latest_discharge.py