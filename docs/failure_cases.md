# Failure Cases（真实失败，不粉饰）

> 来源：阶段 1 五题 Trace（滚动近 60 天数据重跑后）。  
> **本轮不修 `sql_validator.py`。** 这两个失败留作评测与面试素材。

---

## Case 01 — Validator 把 SQL 函数当成字段（Q3）

| 项 | 内容 |
|----|------|
| 问题 | 本周和上周自然流量相比有什么变化？ |
| 类型 | 对比分析 |
| 发生时间 | 2026-08-25，`mode=sql`，`dataset_ids=[7,8]` |
| 严重程度 | **高、系统性**：凡是对比题 LLM 写出 `DATE_SUB` / `WEEKDAY` / `INTERVAL` 等，就会在执行前被拦 |

### 用户说了什么 / 系统做了什么

- 用户要的是两周 `organic_traffic` 对比（合法 SELECT）。
- Planner 1 步 sql（合理）。
- SQL 生成后，`verify_columns_exist` 从语句里抽「像标识符的词」，与流量表列比对。

### 真实报错（原文）

```text
抱歉，当前数据集中没有找到字段：['DATE_SUB', 'WEEKDAY', 'INTERVAL', 'DATE_ADD', 'NULLIF']，无法进行相关分析。
当前数据集包含的字段有：['_id', 'site', 'date', 'pv', 'uv', 'organic_traffic']
```

被误伤的词（都不是业务字段）示例：

- `DATE_SUB` / `DATE_ADD` / `WEEKDAY` / `INTERVAL` / `NULLIF`
- 以及后续评测中出现的 **`_FORMAT`**（多半来自 `DATE_FORMAT` 被正则拆坏）

同类复现：Evaluation **03、10、15**（见 `eval/evaluation_results.md`）。

### 正确答案应是什么

应允许日期函数 / 空值处理函数，只校验真实列名（`site/date/pv/uv/organic_traffic` 以及关键词表的 `keyword/rank`）。

### 为什么错

`extract_columns_from_sql()` 用宽松正则捞标识符，`sql_keywords` 黑名单覆盖了部分聚合函数，**没有**覆盖 MySQL 日期函数与 `NULLIF`。  
详见 `sql_validator.py` 的 SELECT 拆分 + `findall` 标识符逻辑。

### 改进方向（未做）

- 黑名单补 `DATE_SUB` / `DATE_ADD` / `INTERVAL` / `WEEKDAY` / `NULLIF` / 窗口函数等
- 或：只抽取「出现在 schema 中的列」做存在性检查（白名单），而不是抽全量标识符再反查

### 影响面

对比类、环比类、周/月窗口类问题命中率高；简单 `SUM(pv) WHERE date >= CURDATE()-7` 有时也能过，取决于模型是否写出被黑名单漏掉的函数名。

---

## Case 02 — 关键词下钻被校验成「字段不存在」（Q5）

| 项 | 内容 |
|----|------|
| 问题 | 哪些关键词导致了下降？ |
| 类型 | 复杂分析 / 跨表下钻 |
| 发生时间 | 同上 |
| 严重程度 | **高、在本 Demo 上接近必然**：会话绑了流量表+关键词表，但校验 `primary_table` 往往是流量表 |

### 用户说了什么 / 系统做了什么

- 用户要关键词归因，数据在 `ds_seo_keyword_ranking`（`keyword`, `rank`）。
- Planner 拆成 2 步：Step1 查流量趋势成功；Step2 下钻关键词 **failed**。
- Synthesizer 仍出综合报告，并标明「关键词维度归因失败」。

### 真实报错（多轮 Trace 中出现过的原文）

较早一轮：

```text
抱歉，当前数据集中没有找到字段：['t1', 't2', 'keyword', 'NULLIF', 'k']
当前数据集包含的字段有：['_id', 'site', 'date', 'pv', 'uv', 'organic_traffic']
```

日期修复后综合报告侧写明：当前用于校验的列集合只有流量表字段，**不含 `keyword`**。

### 正确答案应是什么

Step2 应对 `ds_seo_keyword_ranking` 查排名下滑的关键词（Site A 后 30 天有 15 个核心词掉 4～8 位，生成器里写死了）。

### 为什么错（两层叠在一起）

1. **表范围**：`verify_columns_exist(..., primary_table=...)` 常用流量主表列集合；`keyword` 真实存在于第二张表，却被判缺失。
2. **抽词过宽**：表别名 `t1`/`t2`/`k`、函数 `NULLIF` 同样被当成字段（与 Case 01 同源）。

### 偶发还是必然？

在「分析中心绑定两张 SEO 表 + 问关键词」这条主路径上，**多次复跑 Step2 均失败**，应视为当前实现下的必然失败，不是偶发 LLM 胡写。

### 改进方向（未做）

- 校验时合并 **本次请求全部 table_info 的列**
- 与 Case 01 同一套：排除别名、函数名
- Planner Step2 的 `input.question` 可显式指定关键词表（仍须校验器认多表）

---

## Case 03 — 数据正确但结论曾不可见（历史，已改 UI）

| 项 | 内容 |
|----|------|
| 问题 | 任意成功 SQL（有表有图） |
| 现象 | AnalysisView 在有 `chartData/chartConfig` 时隐藏 `msg.content` |
| 状态 | **已用最小改动修复**（结论卡片置顶）；保留作产品决策「结论置顶」的证据 |

根因：`v-if="msg.content && !msg.chartData?.length && !msg.chartConfig"`。

---

## Case 04 — 相对时间 vs CURDATE（已用数据侧规避，架构待验证）

见 [product_decisions.md](product_decisions.md)：mock 曾锚在 2024-06，`CURDATE()-30` 查空。  
本轮只改生成器滚动 60 天，**未改 Text-to-SQL 时间编译**。

---

## 严重程度对照（给阶段 10 决策表用）

| Case | 用户可感知失败？ | 是否修了 | 为何本轮不修 |
|------|------------------|----------|--------------|
| 01 validator 函数名 | 是，整题失败 | 否 | 超出 planner/orchestrator/AnalysisView 范围；留证据 |
| 02 关键词跨表 | 是，归因步失败 | 否 | 同上 |
| 03 结论隐藏 | 是 | 是 | 阶段 4 范围内 |
| 04 日期锚定 | 曾是 | 数据侧规避 | 架构方案标待验证 |
