# Demo Smoke Trace（5 题）

> 记录规则：只写 SSE / API 中看到的真实情况，不预设答案。  
> **本文件已按「滚动最近 60 天」mock 数据重跑（2026-08-25），旧版 2024-06 窗口结果作废。**  
> 环境：`uvicorn :8000`，`mode=sql`，`dataset_ids=[7,8]`  
> 数据窗口：`2026-06-27` → `2026-08-25`  
> 原始 JSON：`docs/_smoke_raw_dated.json`

## 数据前提

| 项 | 值 |
|----|-----|
| 生成器 | `seo_data_generator.py` 滚动 `today-(DAYS-1)` … `today` |
| Site A organic 前/后半均值 | ~5500 → ~3774（下降设计保留） |
| 灌库 | `scripts/refresh_seo_seed.py` force_refresh |

## Trace 汇总（日期修复后重跑）

| # | 问题 | Router | plan.source | Planner步数 | 实际执行 | Synthesizer | 结果行数 | 结果摘要 |
|---|------|--------|-------------|-------------|---------|-------------|---------|----------|
| 1 | 最近7天PV是多少？ | sql（手动 mode） | llm | **1** | 1 success | 否 | 1 | 有结论；近7日 PV 总量约 **182,151** |
| 2 | 最近30天SEO流量趋势怎么样？ | sql | llm | **1** | 1 success | 否 | **31** | 用 `organic_traffic`；报告指出持续下行 |
| 3 | 本周和上周自然流量相比有什么变化？ | sql | llm | **1** | 1 **failed** | 否 | 0 | validator 误报 `DATE_SUB/WEEKDAY/...` |
| 4 | 最近30天SEO流量为什么下降？ | sql | llm | **2** | 2（1✓ 1✗） | **是** | **93**（顶层提升） | Step1 趋势成功；Step2 关键词失败；有综合结论 |
| 5 | 哪些关键词导致了下降？ | sql | llm | **2** | 2（1✓ 1✗） | **是** | 60 | Step2 仍因 keyword 字段校验/主表范围失败 |

## 与改日期前对比（勿再用旧结论做 Demo）

| 项 | 改日期前（2024-06 窗口） | 改日期后（最近 60 天） |
|----|-------------------------|------------------------|
| Q1「最近7天 PV」 | 有行但 PV 常为 null/空有效值 | **有真实总量结论** |
| Q2「30 天 SEO 趋势」 | **0 行** | **31 行** + 趋势分析 |
| Q4「为什么下降」 | 0 行空跑 | **93 行** + Synthesizer 有业务叙述 |

## 判定回顾

- 情况 C（`mode=sql` 曾强制 1 步）已在前期修复；本轮确认复杂题仍为 **2 步 + Synthesizer**。
- 简单题仍为 1 步，未过度拆分。
- 残留问题：validator 误伤函数名；关键词表字段未进校验集合 → Q3/Q5 Step2 失败。

## 逐题要点

### Q1
- SQL：`SUM(pv) ... DATE_SUB(CURDATE(), INTERVAL 7 DAY)`
- answer 含近 7 日 PV 总量约 182,151

### Q2
- SQL：按日 `SUM(organic_traffic)`，近 30 天
- answer：周期内自然流量呈下行态势

### Q3
- 字段校验失败：`DATE_SUB`, `WEEKDAY`, `INTERVAL`, `DATE_ADD`, `NULLIF`

### Q4（主 Demo 候选）
- Planner 2 步；Step1 success（93 行）；Step2 failed（关键词）
- Synthesizer 产出综合报告（含核心结论段落）
- 顶层已提升 `sql` / `result` / `chart_config`

### Q5
- 同样 2 步；关键词归因仍失败；综合报告标明关键词模块失败
