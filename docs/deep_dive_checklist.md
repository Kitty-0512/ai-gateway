# 四遍法吃透（学习清单）

> 自己做。不要让 Cursor「替你完成这个功能」。  
> 本次迭代重点文件：`planner.py`、`orchestrator.py`、`AnalysisView.vue`。  
> **不要在本轮改 sql_validator。** Retry 3→2 只作为你自己的实验，做完可还原。

## 第一遍：只看数据流

```text
Vue AnalysisView
  → POST /api/chat/stream
  → orchestrator.run_stream
  → Router（mode=sql 则 hint=sql）
  → Planner.make_plan（现已不再因 mode 跳过）
  → 按 step 调 sql_tool
  → sql_generator（semantic_layer 注入 → LLM SQL → validator → 执行 → 分析文本）
  → 多步则 Synthesizer
  → SSE：routing / plan / stage / sql / delta / result
  → Vue：content=answer；表图=result/chart_config
```

勾选：能不看函数把这条链路讲完。

## 第二遍：模块为什么存在

每个文件只答两句：解决什么？删掉会怎样？

| 文件 | 你的笔记（自己填） |
|------|-------------------|
| router.py | |
| planner.py | |
| orchestrator.py | |
| tools/registry.py | |
| sql_tool.py | |
| semantic_layer.py | |
| schema_controller.py | |
| sql_validator.py | |
| sql_retry.py | |
| synthesizer.py | |
| trace.py | |

本次改动要能讲清：

- 为什么 `mode=sql` 曾经 `plan.source=manual` 恒 1 步  
- 多步时为何要把最后成功 sql 的 observation 提升到顶层  
- AnalysisView 为何有表有图会藏结论  

## 第三遍：自己改一个很小的参数

建议：`sql_retry` 或 orchestrator `MAX_INFRA_RETRIES` 从 3/2 改成更小，看 Trace 是否记录次数。  
先自己定位文件，再问 Cursor。改完可还原，避免污染 Demo。

## 第四遍：故意制造失败（观察链路，不要修 validator）

可选：

- 提问依赖 `DATE_SUB` 的对比题 → 看 **Validator 直接失败、不进 Retry**（逻辑失败 vs infra retry）  
- 问关键词归因 → 看 Step2 failed → Synthesizer 仍综合  

写下：错误发生 → 哪个模块发现 → 是否 Retry → 最终返回什么。

这与 Failure Case 01/02 是同一条证据链。
