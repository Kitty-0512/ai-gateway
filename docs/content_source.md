# msg.content 来源确认

## 结论

在分析中心 SQL 路径下，`msg.content` **等于**最终 SSE `result.answer`（或流式过程中累积的 `delta` 文本，最终被 `answer` 覆盖）。

## 依据

1. 单步：`QueryResponse.answer` = SQL pipeline Step10 的分析报告 → `_single_tool_result` 铺到顶层 `answer`。
2. 多步：`answer` = Synthesizer 综合文本（`synthesized=true`）。
3. [AnalysisView.vue](../frontend/src/views/AnalysisView.vue) `result` 回调：

```js
const content = data.answer || logContent || streamDraft.value || '分析完成'
store.updateLastMessage(..., { content, ... })
```

因此「核心结论」卡片展示的应是最终 AI 结论，不是原问题、不是中间 stage 文案。

## UI 修改

去掉「有表/有图则隐藏 content」条件；有 `msg.content` 即显示「核心结论」，置于结果 Tab 上方。
