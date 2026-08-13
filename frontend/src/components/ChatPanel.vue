<script setup>
import { ref, nextTick, watch, computed, reactive } from 'vue'
import * as echarts from 'echarts'
import { useSessionStore } from '@/store/sessions'
import { unifiedChatStream } from '@/api/client'
import {
  downloadPng,
  captureChart, captureElement,
  exportSessionPdf, exportTablePdf,
} from '@/utils/export'
import { ElMessage } from 'element-plus'
import ExecutionTrace from '@/components/ExecutionTrace.vue'

const store = useSessionStore()

const input = ref('')
const loading = ref(false)
const stageLabel = ref('')
const streamDraft = ref('')
const displayedSql = ref('')
const errorMsg = ref('')
const chatEl = ref(null)
let abortCtrl = null

// 图表实例 + 消息 DOM 引用：key = message index
const chartRefs = new Map()
const chartResizeObservers = new Map()
const msgDomRefs = new Map()   // index → DOM element (for html2canvas)

const messages = computed(() => store.currentSession?.messages || [])
const currentMode = computed(() => store.currentSession?.meta?.selectedMode || '')
const hasSqlDataset = computed(() =>
  currentMode.value !== 'sql' || !!store.currentSession?.meta?.datasetIds?.length
)
const inputPlaceholder = computed(() => {
  if (currentMode.value === 'sql') {
    if (!hasSqlDataset.value) {
      return '该会话未绑定数据集，请新建数据分析会话并重新上传文件'
    }
    return '输入数据分析问题…（例如：各门店销售额排名）'
  }
  if (currentMode.value === 'log') {
    return '输入日志问题或补充上下文…（Enter 发送，Shift+Enter 换行）'
  }
  return '输入消息… (Enter 发送，Shift+Enter 换行)'
})

const toolLabel = { sql: '数据分析', log: '日志诊断' }
const toolColorMap = { sql: 'primary', log: 'success' }

// ── 发送消息 ──────────────────────────
function buildPayload(userText) {
  const mode = store.currentSession?.meta?.selectedMode || undefined
  const payload = {
    session_id: store.currentId,
    file_name: store.currentSession?.meta?.fileName || undefined,
    mode,
    dataset_ids: store.currentSession?.meta?.datasetIds || [],
  }

  if (mode === 'log') {
    const logContent = store.currentSession?.meta?.logContent || ''
    if (!logContent) {
      throw new Error('当前会话没有保存日志内容，请新建日志诊断会话并重新上传文件')
    }
    payload.content = logContent
    payload.extra_context = userText
    payload.question = userText
    return payload
  }

  payload.question = userText
  payload.content = userText
  return payload
}

async function send() {
  const text = input.value.trim()
  if (!text || loading.value || !store.currentId || !hasSqlDataset.value) return

  try {
    await sendRequest(buildPayload(text), text)
  } catch (err) {
    errorMsg.value = err.message || '无法发送'
    ElMessage.warning(errorMsg.value)
  }
}

async function sendRequest(payload, userMessage) {
  if (loading.value || !store.currentId) return

  store.addMessage(store.currentId, 'user', userMessage)
  if (input.value.trim() === userMessage) {
    input.value = ''
  }
  errorMsg.value = ''

  store.addMessage(store.currentId, 'assistant', '', { streaming: true, tool: '' })
  loading.value = true
  stageLabel.value = ''
  streamDraft.value = ''
  displayedSql.value = ''

  // 执行过程追踪
  const trace = reactive({
    tool: '',
    toolReason: '',
    toolConfidence: '',
    sql: '',
    sqlRepaired: false,
    retryCount: 0,
    stages: [],
    evidence: [],
  })

  abortCtrl?.abort()
  abortCtrl = new AbortController()

  try {
    await unifiedChatStream(
      payload,
      {
        stage(data) {
          stageLabel.value = data.label || data.stage
          if (data.stage) trace.stages.push(data.stage)
          if (data.search_trace) trace.searchTrace = data.search_trace
        },
        sql(data) {
          displayedSql.value = data.sql
          trace.sql = data.sql
          trace.sqlRepaired = !!data.repaired
          if (data.repaired) trace.retryCount = (trace.retryCount || 0) + 1
        },
        delta(data) {
          streamDraft.value += data.text || ''
          store.updateLastMessage(store.currentId, { content: streamDraft.value })
        },
        result(data) {
          const tool = data.routed_tool?.tool || data.mode || ''
          const isLogFollowUp = tool === 'log' && Boolean(data.meta?.has_extra_context)
          const logContent = tool === 'log'
            ? (isLogFollowUp
              ? buildLogFollowUpAnswer(data.result)
              : buildLogContent(data.result, data.meta))
            : ''
          const content = data.answer || logContent || streamDraft.value || '分析完成'

          // 完善 trace
          trace.tool = tool
          trace.toolReason = data.routed_tool?.reason || ''
          trace.toolConfidence = data.routed_tool?.confidence || ''
          trace.diagnosisMode = data.meta?.mode || ''
          trace.sql = trace.sql || data.sql || ''
          // 日志证据（仅首次完整诊断展示）
          const ev = data.result?.evidence
          if (!isLogFollowUp && ev && Array.isArray(ev) && ev.length) trace.evidence = ev

          store.updateLastMessage(store.currentId, {
            content,
            tool,
            streaming: false,
            sql: data.sql || displayedSql.value,
            result: data.result,
            chartConfig: data.chart_config,
            chartData: data.result?.slice?.(0, 500),
            trace: { ...trace },  // 快照保存
          })
          if (tool) store.setTool(store.currentId, tool)
          stageLabel.value = ''
          streamDraft.value = ''
          displayedSql.value = ''
        },
        error(data) {
          errorMsg.value = data.message || '请求失败'
          store.updateLastMessage(store.currentId, {
            content: errorMsg.value,
            streaming: false,
            trace: { ...trace },
          })
        },
      },
      abortCtrl.signal,
    )
  } catch (err) {
    if (err.name !== 'AbortError') {
      errorMsg.value = err.message || '网络错误'
      store.updateLastMessage(store.currentId, { content: errorMsg.value, streaming: false })
    }
  } finally {
    loading.value = false
    abortCtrl = null
    scrollToBottom()
  }
}

async function consumePendingSend() {
  const session = store.currentSession
  const pending = session?._pendingSend
  if (!session || !pending?.payload || loading.value) return

  if (pending.payload.mode === 'log' && pending.payload.content) {
    store.setMeta(session.id, { logContent: pending.payload.content })
  }

  store.setPendingSend(session.id, null)
  await sendRequest(pending.payload, pending.userMessage || '已提交分析请求')
}

// ── 滚动 ──────────────────────────────
function scrollToBottom() {
  nextTick(() => {
    if (chatEl.value) chatEl.value.scrollTop = chatEl.value.scrollHeight
  })
}
watch(() => store.currentId, () => scrollToBottom())
watch(() => messages.value.length, () => scrollToBottom())
watch(
  () => [store.currentId, store.currentSession?._pendingSend],
  () => { consumePendingSend() },
  { immediate: true },
)

function onKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}

// ── 文字格式化 ────────────────────────
function formatContent(text) {
  if (!text) return ''
  return text.replace(/\n/g, '<br>').replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
}

function buildLogFollowUpAnswer(result = {}) {
  return result.summary || result.root_cause || '暂无回答'
}

function buildLogContent(result = {}, meta = {}) {
  const modeLabelMap = {
    llm: 'LLM 结构化诊断',
    mock: '本地模板诊断',
    mock_fallback: 'LLM 失败后回退模板',
  }
  const lines = []
  lines.push(`**诊断模式**：${modeLabelMap[meta.mode] || meta.mode || '未知'}`)
  if (result.summary) lines.push(`**现象概述**：${result.summary}`)
  if (result.anomaly_type) lines.push(`**异常类型**：${result.anomaly_type}`)
  if (result.root_cause) lines.push(`**可能根因**：${result.root_cause}`)
  if (result.risk_level) lines.push(`**风险等级**：${result.risk_level}`)
  if (typeof result.severity_score === 'number') {
    lines.push(`**严重度评分**：${result.severity_score}/10`)
  }
  if (Array.isArray(result.investigation_steps) && result.investigation_steps.length) {
    lines.push('', '**排查建议**')
    result.investigation_steps.forEach((step, idx) => lines.push(`${idx + 1}. ${step}`))
  }
  if (Array.isArray(result.follow_up_questions) && result.follow_up_questions.length) {
    lines.push('', '**建议追问**')
    result.follow_up_questions.forEach((question, idx) => lines.push(`${idx + 1}. ${question}`))
  }
  return lines.join('\n')
}

// ── 导出：单条消息 → 图表 PNG（ECharts getDataURL）─────
function exportChartPng(msgIdx) {
  const instance = chartRefs.get(msgIdx)
  if (!instance || typeof instance.getDataURL !== 'function') {
    ElMessage.warning('该图表暂不支持导出')
    return
  }
  const url = instance.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#fff' })
  downloadPng(url, `chart-msg-${msgIdx}.png`)
  ElMessage.success('图表 PNG 已下载')
}

// ── 导出：单条消息 → 卡片截图（html2canvas）────────────
async function exportMsgScreenshot(msgIdx) {
  const el = msgDomRefs.get(msgIdx)
  if (!el) { ElMessage.warning('找不到消息 DOM 节点'); return }
  const url = await captureElement(el)
  if (!url) { ElMessage.error('截图失败'); return }
  downloadPng(url, `msg-${msgIdx}.png`)
  ElMessage.success('消息截图已下载')
}

// ── 导出：单条消息数据表 → PDF ────────
async function exportMsgTablePdf(msgIdx) {
  const msg = messages.value[msgIdx]
  const data = msg?.chartData || msg?.result?.data || msg?.result
  if (!data || !Array.isArray(data) || !data.length) {
    ElMessage.warning('没有可导出的表格数据')
    return
  }
  try {
    ElMessage.info('正在生成数据表 PDF…')
    await exportTablePdf(data, '查询结果数据表')
    ElMessage.success('数据表 PDF 已下载')
  } catch (err) {
    ElMessage.error(err?.message || 'PDF 导出失败')
  }
}

function tableColumns(rows) {
  if (!rows?.length) return []
  return Object.keys(rows[0]).map((key) => ({
    prop: key,
    label: key,
    minWidth: 100,
  }))
}

// ── 导出：整个对话 → PDF ──────────────
async function exportFullSession() {
  if (!store.currentSession) return
  try {
    ElMessage.info('正在生成 PDF…')
    const chartImages = new Map()
    for (const [idx, instance] of chartRefs) {
      const url = captureChart(instance)
      if (url) chartImages.set(idx, url)
    }
    await exportSessionPdf(store.currentSession, chartImages)
    ElMessage.success('对话已导出为 PDF')
  } catch (err) {
    ElMessage.error(err?.message || 'PDF 导出失败')
  }
}

// ── ECharts 渲染 hook ─────────────────
function onChartMount(el, msgIdx, config, data) {
  if (!el || !config || !data?.length) return
  const old = chartRefs.get(msgIdx)
  if (old) old.dispose()
  const oldRo = chartResizeObservers.get(msgIdx)
  if (oldRo) oldRo.disconnect()

  const instance = echarts.init(el)
  instance.setOption(buildChartOption(config, data))
  chartRefs.set(msgIdx, instance)

  requestAnimationFrame(() => instance.resize())

  const ro = new ResizeObserver(() => instance.resize())
  ro.observe(el)
  chartResizeObservers.set(msgIdx, ro)
}

function buildChartOption(config, data) {
  const type = config.type || 'bar'
  const title = config.title || ''
  const colors = ['#5470c6','#91cc75','#fac858','#ee6666','#73c0de','#3ba272','#fc8452','#9a60b4']
  const manyCategories = data.length > 8
  const base = {
    title: { text: title, left: 'center', top: 8, textStyle: { fontSize: 16, fontWeight: 600 } },
    tooltip: { trigger: 'axis', textStyle: { fontSize: 13 } },
    color: colors,
    grid: { left: 48, right: 24, top: 56, bottom: manyCategories ? 72 : 48, containLabel: true },
  }
  if (type === 'pie') {
    const catField = config.category_field || config.categoryField || Object.keys(data[0])[0]
    const valField = config.value_field || config.valueField || Object.keys(data[0])[1]
    return {
      ...base,
      tooltip: { trigger: 'item', textStyle: { fontSize: 13 } },
      legend: data.length > 6 ? { type: 'scroll', bottom: 0, textStyle: { fontSize: 12 } } : undefined,
      series: [{
        type: 'pie',
        radius: ['32%', '62%'],
        center: ['50%', '52%'],
        data: data.map((r) => ({ name: r[catField], value: r[valField] })),
        label: { fontSize: 13, formatter: '{b}: {d}%' },
        emphasis: { label: { fontSize: 14, fontWeight: 'bold' } },
      }],
    }
  }
  const xField = config.x_field || config.xField || Object.keys(data[0])[0]
  const yField = config.y_field || config.yField || Object.keys(data[0])[1]
  return {
    ...base,
    xAxis: {
      type: 'category',
      data: data.map((r) => r[xField]),
      axisLabel: { fontSize: 12, rotate: manyCategories ? 35 : 0, interval: 0 },
    },
    yAxis: { type: 'value', axisLabel: { fontSize: 12 } },
    dataZoom: manyCategories
      ? [{ type: 'inside' }, { type: 'slider', height: 20, bottom: 8 }]
      : undefined,
    series: [{
      type: type === 'line' ? 'line' : 'bar',
      data: data.map((r) => r[yField]),
      smooth: type === 'line',
      barMaxWidth: 48,
      label: data.length <= 12 ? { show: true, position: 'top', fontSize: 11 } : undefined,
    }],
  }
}

// 注册消息 DOM ref
function setMsgRef(el, idx) {
  if (el) msgDomRefs.set(idx, el)
}
</script>

<template>
  <div class="chat-panel">
    <!-- ═══ 顶部工具栏 ═══ -->
    <div v-if="store.currentSession" class="chat-header">
      <span class="session-title">{{ store.currentSession.title }}</span>
      <el-tag
        v-if="store.currentSession.tool"
        :type="toolColorMap[store.currentSession.tool] || 'info'"
        size="small" effect="plain"
      >
        {{ toolLabel[store.currentSession.tool] || store.currentSession.tool }}
      </el-tag>
      <div class="header-spacer" />
      <el-button size="small" @click="exportFullSession" :disabled="!messages.length">
        <el-icon><Download /></el-icon>
        导出 PDF
      </el-button>
    </div>

    <!-- ═══ 消息区 ═══ -->
    <div ref="chatEl" class="chat-body">
      <div v-if="!store.currentSession" class="no-session">
        <el-icon :size="48"><ChatLineSquare /></el-icon>
        <h3>企业智能分析与运维诊断平台</h3>
        <p>新建一个会话，开始数据分析或日志诊断</p>
      </div>
      <el-alert
        v-else-if="currentMode === 'sql' && !hasSqlDataset"
        class="dataset-warning"
        title="这个数据分析会话没有绑定数据集。请新建数据分析会话并重新上传 Excel / CSV 文件。"
        type="warning"
        :closable="false"
        show-icon
      />

      <template v-for="(msg, i) in messages" :key="i">
        <div
          class="msg-row"
          :class="[
            msg.role,
            {
              'has-chart': msg.role === 'assistant' && (
                (msg.chartConfig && msg.chartData?.length) || msg.chartData?.length
              ),
            },
          ]"
        >
          <div
            class="msg-bubble"
            :ref="(el) => setMsgRef(el, i)"
          >
            <!-- ═══ 消息操作按钮（hover 显示） ═══ -->
            <div v-if="msg.role === 'assistant' && !msg.streaming" class="msg-actions">
              <!-- 图表 → PNG -->
              <el-tooltip content="导出图表 PNG" placement="top">
                <el-button
                  v-if="msg.chartConfig && msg.chartData?.length"
                  size="small" text circle @click="exportChartPng(i)"
                >
                  <el-icon><PictureFilled /></el-icon>
                </el-button>
              </el-tooltip>
              <!-- 表格 → PDF -->
              <el-tooltip content="导出数据表 PDF" placement="top">
                <el-button
                  v-if="msg.chartData?.length"
                  size="small" text circle @click="exportMsgTablePdf(i)"
                >
                  <el-icon><Document /></el-icon>
                </el-button>
              </el-tooltip>
              <!-- 消息卡片截图（html2canvas） -->
              <el-tooltip content="截取消息为图片" placement="top">
                <el-button
                  size="small" text circle @click="exportMsgScreenshot(i)"
                >
                  <el-icon><Camera /></el-icon>
                </el-button>
              </el-tooltip>
            </div>

            <!-- 进度提示 -->
            <div v-if="msg.streaming && stageLabel" class="stage-label">
              <span class="dot" /> {{ stageLabel }}
            </div>

            <!-- SQL -->
            <div v-if="msg.sql" class="sql-block">
              <div class="sql-head">
                <span>SQL</span>
                <button
                  class="sql-copy-btn"
                  title="复制 SQL"
                  @click="navigator.clipboard.writeText(msg.sql); ElMessage.success('SQL 已复制')"
                >复制</button>
              </div>
              <pre><code>{{ msg.sql }}</code></pre>
            </div>

            <!-- 正文 -->
            <div class="msg-content" v-html="formatContent(msg.content)" />

            <!-- ECharts 图 -->
            <div
              v-if="msg.chartConfig && msg.chartData?.length"
              class="inline-chart"
            >
              <div
                class="chart-canvas"
                :ref="(el) => { if (el) onChartMount(el, i, msg.chartConfig, msg.chartData) }"
              />
            </div>

            <!-- 真实数据表 -->
            <div
              v-if="msg.chartData?.length"
              class="inline-table"
            >
              <div class="table-head">
                <span>数据表</span>
                <span class="table-count">{{ msg.chartData.length }} 行</span>
              </div>
              <el-table
                :data="msg.chartData.slice(0, 100)"
                :border="true"
                size="small"
                stripe
                max-height="360"
                style="width: 100%"
              >
                <el-table-column
                  v-for="col in tableColumns(msg.chartData)"
                  :key="col.prop"
                  :prop="col.prop"
                  :label="col.label"
                  :min-width="col.minWidth"
                  show-overflow-tooltip
                />
              </el-table>
              <div v-if="msg.chartData.length > 100" class="table-more">
                仅展示前 100 行，导出 PDF 可查看更多
              </div>
            </div>

            <!-- 执行过程追踪面板 -->
            <ExecutionTrace v-if="!msg.streaming" :trace="msg.trace || {}" />

            <!-- 打字动画 -->
            <span v-if="msg.streaming && !msg.content && !stageLabel" class="typing">
              <span class="dot-1" /><span class="dot-2" /><span class="dot-3" />
            </span>
          </div>
        </div>
      </template>

      <div v-if="errorMsg" class="error-tip">
        <el-icon><WarningFilled /></el-icon> {{ errorMsg }}
      </div>
    </div>

    <!-- ═══ 输入区 ═══ -->
    <div class="chat-input">
      <el-input
        v-model="input"
        type="textarea"
        :rows="2"
        :placeholder="inputPlaceholder"
        :disabled="loading || !store.currentId || !hasSqlDataset"
        resize="none"
        @keydown="onKeydown"
      />
      <el-button
        type="primary"
        :disabled="!input.trim() || loading || !store.currentId || !hasSqlDataset"
        :loading="loading"
        @click="send"
      >
        <el-icon><Promotion /></el-icon>
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.chat-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  height: 100vh;
  min-width: 0;
  background: var(--el-bg-color-page);
}

.dataset-warning {
  margin: 16px 20px 0;
}

/* ── 头部 ── */
.chat-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 20px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  background: var(--el-bg-color);
  flex-shrink: 0;
}
.session-title { font-weight: 600; font-size: 15px; }
.header-spacer { flex: 1; }

/* ── 消息区 ── */
.chat-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.no-session {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--el-text-color-secondary);
  gap: 8px;
}
.no-session h3 { margin: 0; font-size: 20px; color: var(--el-text-color-regular); }
.no-session p { margin: 0; font-size: 14px; }

/* ── 消息气泡 ── */
.msg-row { display: flex; margin-bottom: 16px; }
.msg-row.user { justify-content: flex-end; }
.msg-row.assistant { justify-content: flex-start; }
.msg-row.assistant.has-chart {
  width: 100%;
}

.msg-bubble {
  max-width: 82%;
  min-width: 180px;
  padding: 10px 14px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.6;
  position: relative;
  word-break: break-word;
}
.msg-row.assistant.has-chart .msg-bubble {
  max-width: 100%;
  width: 100%;
}
.msg-row.user .msg-bubble {
  background: var(--el-color-primary);
  color: #fff;
  border-bottom-right-radius: 4px;
}
.msg-row.assistant .msg-bubble {
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-bottom-left-radius: 4px;
}

/* ── 操作按钮 ── */
.msg-actions {
  position: absolute;
  top: 6px;
  right: 8px;
  display: flex;
  gap: 2px;
  opacity: 0;
  transition: opacity 0.15s;
  z-index: 2;
}
.msg-bubble:hover .msg-actions { opacity: 1; }
.msg-actions :deep(.el-button) { font-size: 14px; }

/* SQL */
.sql-block {
  margin-bottom: 8px;
  background: #1e1e2e;
  border-radius: 8px;
  overflow: hidden;
}
.sql-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 11px;
  color: #89b4fa;
  padding: 4px 10px;
  background: #181825;
}
.sql-copy-btn {
  background: none;
  border: 1px solid #45475a;
  color: #a6adc8;
  font-size: 10px;
  padding: 1px 8px;
  border-radius: 4px;
  cursor: pointer;
}
.sql-copy-btn:hover { background: #313244; color: #cdd6f4; }
.sql-block pre { margin: 0; padding: 10px; overflow-x: auto; }
.sql-block code {
  font-family: 'IBM Plex Mono', Consolas, monospace;
  font-size: 12px;
  color: #a6e3a1;
  white-space: pre;
}

/* 阶段提示 */
.stage-label {
  display: flex; align-items: center; gap: 6px;
  font-size: 12px; color: var(--el-color-primary); margin-bottom: 6px;
}
.dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--el-color-primary);
  animation: pulse 1s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

/* 内嵌图表 */
.inline-chart {
  margin-top: 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 8px 8px 4px;
  background: #fff;
}
.chart-canvas {
  width: 100%;
  height: 420px;
  min-height: 360px;
}

/* 内嵌真实数据表 */
.inline-table {
  margin-top: 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  overflow: hidden;
  background: #fff;
}
.table-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  background: var(--el-fill-color-light);
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.table-count {
  font-size: 12px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
}
.table-more {
  padding: 6px 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  border-top: 1px solid var(--el-border-color-lighter);
}

/* 打字 */
.typing { display: inline-flex; gap: 4px; align-items: center; }
.typing span {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--el-color-primary);
  animation: bounce 1.2s infinite;
}
.typing .dot-2 { animation-delay: 0.2s; }
.typing .dot-3 { animation-delay: 0.4s; }
@keyframes bounce {
  0%, 80%, 100% { transform: translateY(0); }
  40% { transform: translateY(-8px); }
}

/* 错误 */
.error-tip {
  display: flex; align-items: center; gap: 6px;
  padding: 10px 14px; border-radius: 8px;
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger); font-size: 13px;
}

/* 输入 */
.chat-input {
  display: flex; gap: 8px;
  padding: 12px 20px;
  border-top: 1px solid var(--el-border-color-lighter);
  background: var(--el-bg-color);
  flex-shrink: 0;
}
.chat-input :deep(.el-textarea__inner) { font-family: inherit; }
.chat-input .el-button { align-self: flex-end; height: 40px; }
</style>
