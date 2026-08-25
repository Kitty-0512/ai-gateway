# 主 Demo：最近30天 SEO 流量为什么下降？

> 记录日期：2026-08-25（**基于滚动近 60 天 mock 重跑**）  
> 环境：本地后端 `:8000`，`mode=sql`，`dataset_ids=[7,8]`  
> 数据窗口：`2026-06-27` → `2026-08-25`  
> **步骤数以本轮真实跑出为准，不强行凑成 3 步。**  
> 原始：`docs/_smoke_raw_dated.json` Q4

## 执行链路（真实 · 本轮）

```text
用户问题：最近30天SEO流量为什么下降？
    ↓
Intent / Router：sql（用户手动指定 mode）
    ↓
Planner（source=llm）：2 步
    Step 1 → sql
    Step 2 → sql
    needs_synthesis = true
    ↓
Step 1（sql）：success
    - 近 30 天 organic_traffic 日序列
    - 返回 93 行（三站点 × ~31 天）
    ↓
Step 2（sql）：failed
    - 试图关键词下钻
    - 字段校验 / 主表列范围问题（keyword 等）
    ↓
Synthesizer：已进入（synthesized=true）
    ↓
最终结论：有综合分析报告（见下方摘要）；同时诚实标明关键词模块失败
```

## 真实步数

| 项 | 值 |
|----|-----|
| Planner 生成步数 | **2** |
| 实际执行步数 | **2** |
| 成功 / 失败 | 1 success + 1 failed |
| Synthesizer | 是 |
| 顶层 sql / result / chart | 有（observation 提升后） |
| result 行数 | 93 |

## 最终结论摘要（Synthesizer）

本轮 answer 开头大意：

- 报告周期：2026-07-26 至 2026-08-25（近 30 天）
- 有「核心结论」段落描述近 30 天 SEO 流量走势
- 关键词归因步骤失败，报告中应体现分析完成度限制

完整文本见 `_smoke_raw_dated.json` 中 Q4 的 `answer_preview` / 前端「核心结论」卡片。

## 截图清单（已完成）

`docs/screenshots/` 已有：

| 文件 | 内容（与界面核对） |
|------|-------------------|
| `01-question.png` | 用户请求：最近30天SEO流量为什么下降？ |
| `02-flow.png` | 已规划 2 步；Step2 失败；Synthesizer 完成 |
| `03-sql.png` | `organic_traffic` + 近30天；执行成功 |
| `04-table.png` | 数据表 |
| `05-chart.png` | 图表 |
| `06-conclusion.png` | 含「一、核心结论」；并写明关键词维度失败 |

结论卡片在表/图上方的产品改动已由截图内容侧证（报告以核心结论开头）。

## 已知缺口（面试可讲）

1. ~~Mock 日期与 CURDATE 错位~~ → **已用滚动 60 天修复**；更优方案「相对数据最大日期」仍见 `product_decisions.md`（待验证）。
2. `sql_validator` 误伤 SQL 函数 / 别名；关键词表列未进入校验集合 → Step2 易失败。
3. 多步时结论来自 Synthesizer；表/图来自最后成功 sql 的 observation 提升。
