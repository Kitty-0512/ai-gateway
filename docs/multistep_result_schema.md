# 多步 SQL Result Schema（只读结论）

> 步骤 5.1：改代码前的对照结论。依据 `sql_tool` / `orchestrator` / `client.js` / `AnalysisView.vue`。

## 1. 单步 SQL 的 result schema

`SqlTool` 成功时：`ToolResult.observation` = 完整 `QueryResponse.model_dump()`。

`_single_tool_result` 做 `base.update(observation)`，因此 SSE `result` 顶层大致为：

| 字段 | 含义 |
|------|------|
| `routed_tool` | 路由信息 |
| `trace` | 执行轨迹 |
| `tool_results` | 含本步 ToolResult |
| `sql` | 生成的 SQL |
| `result` | 查询行数组 |
| `chart_config` | 图表配置 |
| `answer` | SQL pipeline 二次 LLM 分析文本 |
| `message_id` / `token_usage` / `clarification_needed` | 其它 |

## 2. 多步 SQL 的 result schema（修复前）

`needs_synthesis=true` 时最终 `result` **只有**：

| 字段 | 含义 |
|------|------|
| `routed_tool` | tool=`multi` |
| `trace` | 含 synthesis |
| `tool_results` | 每步 observation 埋在里面 |
| `answer` | **Synthesizer** 综合文本 |
| `synthesized` | true |

**没有**顶层 `sql` / `result` / `chart_config`。  
复测 Q4/Q5：`has_top_sql=false`，`has_top_result=false`。

## 3. SSE result 事件传什么

`/api/chat/stream` 把 orchestrator 的 `("result", dict)` 原样作为 SSE `event: result` 的 data JSON。

过程中还会先发多条 `sql` / `delta` 事件；但最终 UI 落库以 **最后一次 result 回调** 为准。

## 4. client.js 怎么解析

`unifiedChatStream` 按 `event:` / `data:` 拆帧，对已知 event 名调用 `handlers[event](data)`。  
不对 `result` 做字段变换。

## 5. AnalysisView 从哪里读

`result` 回调里：

```js
content = data.answer || ...
sql: data.sql || displayedSql.value
result: data.result
chartConfig: data.chart_config
chartData: data.result?.slice?.(0, 500)
```

多步时：若中途有 `sql` 事件，`displayedSql` 可能暂时有值；但 `data.result` / `chart_config` 为空 → **表/图丢失**。  
`content` 仍可来自 Synthesizer 的 `answer`（或流式 `delta`）。

## 6. 最小兼容修改点

**只改** `orchestrator.py` 多工具成功最终 `yield "result"` 处：

从 `results` 里 **最后一个 `ok` 且 tool==sql** 的 `observation` 提升：

- `sql`
- `result`
- `chart_config`

`answer` 仍用 Synthesizer 文本。不改前端、不改 sql_tool。
