# 产品决策表

> 用途：面试时说明「哪些有证据、哪些还是假设」。  
> 状态约定：`已验证` / `部分验证` / `暂定` / `待验证`。

| 决策 | 解决的问题 | 验证方式 | 状态 |
|------|------------|----------|------|
| Router + Planner | 任务分类与拆解 | 阶段 1 五题 Trace；复杂题可出 2 步 | 部分验证 |
| Planner ≤3 步 | 控制成本与复杂度 | 代码硬限制 `MAX_STEPS=3`；复测未见无限循环 | 暂定 |
| `mode=sql` 仍走 Planner | 分析中心不再永远强制 1 步 | 改前 plan.source=manual 恒 1 步；改后 llm 可 2 步 | 已验证 |
| Semantic Layer | 指标歧义（流量/SEO流量） | metrics.yaml + 注入 Prompt；专项 Case 未系统跑完 | 部分验证 |
| SQL Retry ≤3 | SQL 执行失败自愈 | Failure Case / 代码路径存在 | 暂定 |
| Schema 裁剪 | 减少无关字段进 Prompt | schema_controller 日志可见选列 | 待验证 |
| SSE + Trace | 展示执行过程 | 前端 OrchestrationFlow / ExecutionTrace | 已验证 |
| 结论置顶（AnalysisView） | 有表有图时仍能看见 AI 结论 | 去掉隐藏条件；需 UI 截图确认 | 部分验证 |
| 多步结果提升顶层 sql/result/chart | 多步后前端不丢表/图 | Q4 复跑 has_top_sql/chart=true | 已验证 |
| Mock 滚动近 60 天 | 「最近 N 天」能命中演示数据 | 改日期后 Q1/Q2/Q4 有行有结论 | 已验证 |
| **相对时间锚点 vs 绝对 CURDATE()** | 模型用 `CURDATE()` 时，若数据窗口不是「今天」会空结果；更稳的是锚在「数据集最大日期」做相对区间 | 本次用滚动窗口规避；**未改 Text-to-SQL Prompt/运行时注入 max(date)** | **待验证** |

## 面试候选：「如果再给两周会优化什么？」

优先讲 **相对时间锚点（待验证）**：

1. **发现**：早期 mock 落在 2024-06～07，Agent SQL 仍写 `DATE_SUB(CURDATE(), …)`，导致「最近 30 天」查空，看起来像分析失败，实际是时间坐标系不一致。  
2. **已做权宜**：生成器改为滚动最近 60 天（含今天），Demo/评测可先跑通。  
3. **两周内更优**：在 Prompt 或中间层注入 `data_max_date`，把「最近 N 天」编译为 `[max_date-N, max_date]`，不依赖「数据刚好生成到今天」。  
4. **为何标待验证**：滚动窗口已缓解现象，但未证明相对锚点在「历史归档数据集 / 用户上传旧 CSV」上更稳，需要补一轮对照评测。

## 其它待验证（顺带）

- validator 排除窗口函数与 SQL 日期函数（`DATE_SUB`/`INTERVAL` 等）→ **不修，见 failure_cases Case 01**
- 多表字段校验应合并全部所选 dataset 的列 → **不修，见 failure_cases Case 02**
