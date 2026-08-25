# 主 Demo 截图（阶段 3 交付）

请在本机浏览器完成（Agent 无法代替你点分析页截图）。

1. 打开 http://localhost:3000/analysis  
2. 新建会话，勾选 **SEO 内置数据集**  
3. 提问：**最近30天SEO流量为什么下降？**  
4. 确认「**核心结论**」卡片在数据表 / 图表 **上方**  
5. 按下面文件名保存到本目录：

| 文件 | 拍什么 |
|------|--------|
| `01-question.png` | 输入的问题 |
| `02-flow.png` | 分析流程（Router / Planner / 2 步 / 最终结果） |
| `03-sql.png` | 生成的 SQL |
| `04-table.png` | 数据表（应有行） |
| `05-chart.png` | 图表 Tab |
| `06-conclusion.png` | 「核心结论」全文可见 |

若结论仍不在表图上方，核对 `AnalysisView.vue` 是否已是「有 content 就渲染核心结论」。
