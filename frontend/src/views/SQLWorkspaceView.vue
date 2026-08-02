<script setup>
import { ref, computed } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import { unifiedChatStream } from '@/api/client'
import { exportTablePdf } from '@/utils/export'

const sqlInput = ref('')
const sqlResult = ref(null)
const loading = ref(false)
const errorMsg = ref('')

const chartEl = ref(null)
let chartInstance = null
const showChart = ref(false)
const chartType = ref('bar')

const columns = computed(() => {
  if (!sqlResult.value?.length) return []
  return Object.keys(sqlResult.value[0]).map((k) => ({ prop: k, label: k, minWidth: 100 }))
})

async function executeSql() {
  const text = sqlInput.value.trim()
  if (!text) { ElMessage.warning('请先输入 SQL 查询'); return }

  loading.value = true
  errorMsg.value = ''
  sqlResult.value = null
  showChart.value = false

  try {
    await unifiedChatStream(
      {
        mode: 'sql',
        question: text,
        content: text,
      },
      {
        result(data) {
          const resultData = data.result || []
          sqlResult.value = Array.isArray(resultData) ? resultData : (resultData.data || [resultData])
          if (sqlResult.value.length && Object.keys(sqlResult.value[0]).length >= 2) {
            showChart.value = true
          }
        },
        error(data) {
          errorMsg.value = data.message || '查询执行失败'
        },
      },
    )
  } catch (err) {
    if (err.name !== 'AbortError') {
      errorMsg.value = err.message || '网络错误'
    }
  } finally {
    loading.value = false
  }
}

function formatSql() {
  const keywords = ['SELECT', 'FROM', 'WHERE', 'GROUP BY', 'ORDER BY', 'HAVING', 'JOIN', 'ON', 'AS', 'AND', 'OR', 'LIMIT', 'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'ALTER', 'DROP']
  let formatted = sqlInput.value
  keywords.forEach(kw => {
    const re = new RegExp(`\\b${kw}\\b`, 'gi')
    formatted = formatted.replace(re, kw)
  })
  sqlInput.value = formatted
}

function renderChart() {
  if (!chartEl.value || !sqlResult.value?.length) return
  if (chartInstance) chartInstance.dispose()

  const data = sqlResult.value.slice(0, 50)
  const keys = Object.keys(data[0])
  const xKey = keys[0]
  const yKey = keys[1] || keys[0]
  const manyCategories = data.length > 8

  chartInstance = echarts.init(chartEl.value)
  chartInstance.setOption({
    title: { text: '查询结果', left: 'center', top: 6, textStyle: { fontSize: 14, fontWeight: 600, color: '#1e293b' } },
    tooltip: { trigger: 'axis', textStyle: { fontSize: 12 } },
    color: ['#2563eb'],
    grid: { left: 48, right: 24, top: 52, bottom: manyCategories ? 64 : 40, containLabel: true },
    xAxis: {
      type: 'category',
      data: data.map(r => r[xKey]),
      axisLabel: { fontSize: 11, rotate: manyCategories ? 35 : 0, color: '#64748b' },
    },
    yAxis: { type: 'value', axisLabel: { fontSize: 11, color: '#64748b' } },
    dataZoom: manyCategories ? [{ type: 'inside' }, { type: 'slider', height: 20, bottom: 8 }] : undefined,
    series: [{
      type: chartType.value === 'line' ? 'line' : 'bar',
      data: data.map(r => r[yKey]),
      smooth: chartType.value === 'line',
      barMaxWidth: 40,
    }],
  })
}

function onChartTypeChange() {
  if (showChart.value) {
    setTimeout(renderChart, 50)
  }
}

async function exportPdf() {
  if (!sqlResult.value?.length) return
  try {
    await exportTablePdf(sqlResult.value, 'SQL 查询结果')
    ElMessage.success('PDF 已下载')
  } catch (err) {
    ElMessage.error(err?.message || '导出失败')
  }
}

function copySql() {
  navigator.clipboard.writeText(sqlInput.value)
  ElMessage.success('SQL 已复制')
}
</script>

<template>
  <div class="sql-page">
    <div class="sql-content">
      <h1 class="sql-title">SQL 工作台</h1>
      <p class="sql-subtitle">编写并执行 SQL 查询，查看结果与图表</p>

      <!-- ═══ Editor ═══ -->
      <div class="editor-card">
        <div class="editor-header">
          <span>SQL 编辑器</span>
          <div class="editor-actions">
            <button class="editor-btn" @click="formatSql">格式化</button>
            <button class="editor-btn" @click="copySql">复制</button>
          </div>
        </div>
        <textarea
          v-model="sqlInput"
          class="sql-editor"
          placeholder="SELECT * FROM orders WHERE amount > 100 ORDER BY created_at DESC LIMIT 50;"
          rows="8"
          spellcheck="false"
        />
        <div class="editor-footer">
          <el-button type="primary" size="small" :loading="loading" @click="executeSql">
            <el-icon><CaretRight /></el-icon>
            执行查询
          </el-button>
          <button class="editor-btn" @click="sqlInput = ''">清空</button>
        </div>
      </div>

      <!-- Error -->
      <div v-if="errorMsg" class="error-bar">
        <el-icon><WarningFilled /></el-icon> {{ errorMsg }}
      </div>

      <!-- ═══ Result ═══ -->
      <div v-if="sqlResult" class="result-card">
        <div class="result-header">
          <span>查询结果</span>
          <div class="result-meta">
            <span class="row-count">{{ sqlResult.length }} 行</span>
            <el-button size="small" text @click="exportPdf">
              <el-icon><Download /></el-icon>
              导出
            </el-button>
          </div>
        </div>

        <div class="result-table">
          <el-table
            :data="sqlResult.slice(0, 100)"
            :border="true"
            size="small"
            stripe
            max-height="400"
            style="width: 100%"
          >
            <el-table-column
              v-for="col in columns"
              :key="col.prop"
              :prop="col.prop"
              :label="col.label"
              :min-width="col.minWidth"
              show-overflow-tooltip
            />
          </el-table>
          <div v-if="sqlResult.length > 100" class="truncation-note">
            仅展示前 100 行，共 {{ sqlResult.length }} 行
          </div>
        </div>

        <div v-if="showChart" class="chart-section">
          <div class="chart-controls">
            <span class="chart-label">图表</span>
            <el-radio-group v-model="chartType" size="small" @change="onChartTypeChange">
              <el-radio-button value="bar">柱状图</el-radio-button>
              <el-radio-button value="line">折线图</el-radio-button>
            </el-radio-group>
          </div>
          <div ref="chartEl" class="chart-area" />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sql-page {
  max-width: 960px;
  margin: 0 auto;
  padding: 36px 32px;
}

.sql-title {
  margin: 0;
  font-size: 24px;
  font-weight: 700;
  color: #1e293b;
}

.sql-subtitle {
  margin: 4px 0 24px;
  font-size: 14px;
  color: #64748b;
}

.editor-card {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  overflow: hidden;
  margin-bottom: 16px;
}

.editor-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  background: #1e293b;
  color: #e2e8f0;
  font-size: 12px;
  font-weight: 600;
}

.editor-actions {
  display: flex;
  gap: 6px;
}

.editor-btn {
  background: rgba(255,255,255,0.1);
  border: 1px solid rgba(255,255,255,0.15);
  color: #cbd5e1;
  font-size: 11px;
  padding: 2px 10px;
  border-radius: 4px;
  cursor: pointer;
  font-family: inherit;
}
.editor-btn:hover {
  background: rgba(255,255,255,0.18);
  color: #e2e8f0;
}

.sql-editor {
  width: 100%;
  border: none;
  padding: 16px;
  font-family: 'IBM Plex Mono', 'Cascadia Code', Consolas, monospace;
  font-size: 13px;
  line-height: 1.7;
  color: #1e293b;
  background: #ffffff;
  resize: vertical;
  outline: none;
  tab-size: 2;
}
.sql-editor::placeholder {
  color: #94a3b8;
}

.editor-footer {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  background: #f8fafc;
  border-top: 1px solid #e2e8f0;
}

.editor-footer .editor-btn {
  background: none;
  border: 1px solid #cbd5e1;
  color: #64748b;
}

.error-bar {
  display: flex; align-items: center; gap: 6px;
  padding: 10px 14px; border-radius: 6px;
  background: #fee2e2; color: #dc2626; font-size: 13px;
  border: 1px solid #fecaca;
  margin-bottom: 16px;
}

.result-card {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  overflow: hidden;
}

.result-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
  font-size: 12px;
  font-weight: 600;
  color: #475569;
}

.result-meta {
  display: flex;
  align-items: center;
  gap: 10px;
}

.row-count {
  font-size: 11px;
  font-weight: 400;
  color: #94a3b8;
}

.result-table {
  padding: 0;
}

.truncation-note {
  padding: 8px 16px;
  font-size: 12px;
  color: #94a3b8;
}

.chart-section {
  border-top: 1px solid #e2e8f0;
  padding: 14px 16px;
}

.chart-controls {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}

.chart-label {
  font-size: 12px;
  font-weight: 600;
  color: #475569;
}

.chart-area {
  width: 100%;
  height: 360px;
}
</style>
