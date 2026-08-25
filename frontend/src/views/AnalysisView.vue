<script setup>
import { ref, nextTick, watch, computed, reactive } from 'vue'
import * as echarts from 'echarts'
import { useSessionStore } from '@/store/sessions'
import { unifiedChatStream, uploadSqlFile } from '@/api/client'
import {
  downloadPng,
  captureChart, captureElement,
  exportSessionPdf, exportTablePdf,
} from '@/utils/export'
import { ElMessage, ElMessageBox } from 'element-plus'
import ExecutionTrace from '@/components/ExecutionTrace.vue'
import OrchestrationFlow from '@/components/OrchestrationFlow.vue'
import NewSessionDialog from '@/components/NewSessionDialog.vue'

const store = useSessionStore()

// ── Session selector ──
const showNewDialog = ref(false)
const showSessionList = ref(true)

// ── Input ──
const input = ref('')
const loading = ref(false)
const stageLabel = ref('')
const streamDraft = ref('')
const displayedSql = ref('')
const errorMsg = ref('')
const contentEl = ref(null)
let abortCtrl = null

// ── Result tab ──
const activeTab = ref('table')

// ── Charts ──
const chartRefs = new Map()
const chartResizeObservers = new Map()
const msgDomRefs = new Map()

const messages = computed(() => store.currentSession?.messages || [])
const currentMode = computed(() => store.currentSession?.meta?.selectedMode || '')
const hasSqlDataset = computed(() =>
  currentMode.value !== 'sql' || !!store.currentSession?.meta?.datasetIds?.length
)
const inputPlaceholder = computed(() => {
  if (currentMode.value === 'sql') {
    if (!hasSqlDataset.value) return '当前会话未绑定数据集，请新建分析会话并上传文件'
    return '输入数据分析需求…（Enter 发送，Shift+Enter 换行）'
  }
  if (currentMode.value === 'log') return '输入追问内容…（Enter 发送）'
  return '输入分析需求…（Enter 发送）'
})

const modeLabel = { sql: '数据分析', log: '日志诊断' }
const TOOL_LABEL = { sql: 'SQL 数据分析', log: '日志诊断', mcp: '数据/文件工具' }

// ── 编排流程实时状态（供 OrchestrationFlow 展示，单次请求共享）──
const liveTrace = reactive({})
function resetLiveTrace() {
  Object.keys(liveTrace).forEach((k) => delete liveTrace[k])
  Object.assign(liveTrace, {
    tool: '', toolReason: '', toolConfidence: '',
    sql: '', sqlRepaired: false, retryCount: 0,
    stages: [], evidence: [],
    plan: [], needsSynthesis: false, structured: null,
    diagnosisMode: '', searchTrace: null, toolResults: null,
    routerStatus: 'pending', plannerStatus: 'pending', synthStatus: 'idle',
  })
}

function flowHasContent(t) {
  return !!t && (t.plan?.length || t.structured?.plan?.length || t.tool || t.routerStatus === 'done')
}

function flowFromTrace(t) {
  if (!t) return null
  const steps = (t.plan || []).map((s) => ({
    step: s.step,
    tool: s.tool,
    label: TOOL_LABEL[s.tool] || s.tool,
    status: s.status || 'pending',
    duration: s.duration_ms,
  }))
  if (t.structured?.tool_calls) {
    for (const c of t.structured.tool_calls) {
      const m = steps.find((x) => x.step === c.step)
      if (m) { m.status = c.status; m.duration = c.duration_ms }
    }
  }
  const synth = t.structured?.synthesis
  const synthStatus = t.synthStatus && t.synthStatus !== 'idle'
    ? t.synthStatus
    : (synth?.invoked ? 'done' : 'pending')
  return {
    router: t.routerStatus || (t.tool ? 'done' : 'pending'),
    routerLabel: TOOL_LABEL[t.tool] || t.toolReason || '',
    planner: t.plannerStatus || (steps.length ? 'done' : 'pending'),
    stepCount: steps.length,
    steps,
    showSynth: !!t.needsSynthesis || !!(synth && synth.invoked),
    synth: synthStatus,
    synthDuration: synth?.duration_ms,
  }
}

// ── Session helpers ──
function formatTime(iso) {
  const d = new Date(iso)
  const now = new Date()
  const diff = now - d
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return Math.floor(diff / 60000) + ' 分钟前'
  if (diff < 86400000) return Math.floor(diff / 3600000) + ' 小时前'
  return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}

async function handleDeleteSession(id, e) {
  e.stopPropagation()
  try {
    await ElMessageBox.confirm('确定删除该会话？', '确认删除', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
    store.remove(id)
  } catch { /* cancelled */ }
}

// ── New session ──
function onNewSession(payload = {}) {
  const fileName = payload.meta?.fileName
  const sessionTitle = fileName
    ? `${payload.tool === 'sql' ? '数据集' : '日志'}：${fileName}`
    : payload.tool === 'sql'
      ? '数据分析'
      : payload.tool === 'log'
        ? '日志诊断'
        : '新会话'

  const session = store.create({
    title: sessionTitle,
    tool: payload.tool || '',
    meta: payload.meta || {},
  })

  if (payload.welcomeMessage) {
    store.addMessage(session.id, 'assistant', payload.welcomeMessage, {
      tool: payload.tool || '',
    })
  }

  if (payload.pendingSend) {
    store.setPendingSend(session.id, payload.pendingSend)
  }
}

// ── Pipeline stage helpers ──
const STAGE_LABELS = {
  understanding:  '需求分析',
  generating_sql: 'SQL 生成',
  executing:      '查询执行',
  analyzing:      '报告生成',
  charting:       '图表生成',
  cleaning:       '日志清洗',
  identifying:    '类型识别',
  identified:     '类型已识别',
  locating:       '根因分析',
  searching:      '外部检索',
  search_done:    '检索完成',
  generating:     '诊断生成',
  refining:       '二次诊断',
  repaired:       'SQL 已修复',
  fallback:       '本地回退',
  tool_start:     '工具启动',
  retrying:       '重试中',
  synthesizing:   '综合分析',
}

function stageLabelText(key) {
  return STAGE_LABELS[key] || key
}

// ── SSE Send ──
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
      throw new Error('当前会话未保存日志内容，请新建日志诊断会话')
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
    errorMsg.value = err.message || '发送失败'
    ElMessage.warning(errorMsg.value)
  }
}

async function sendRequest(payload, userMessage) {
  if (loading.value || !store.currentId) return

  store.addMessage(store.currentId, 'user', userMessage)
  if (input.value.trim() === userMessage) input.value = ''
  errorMsg.value = ''

  store.addMessage(store.currentId, 'assistant', '', { streaming: true, tool: '' })
  loading.value = true
  stageLabel.value = ''
  streamDraft.value = ''
  displayedSql.value = ''

  resetLiveTrace()
  const trace = liveTrace

  abortCtrl?.abort()
  abortCtrl = new AbortController()

  try {
    await unifiedChatStream(
      payload,
      {
        routing(data) {
          trace.tool = trace.tool || data.tool || ''
          trace.toolReason = trace.toolReason || data.reason || ''
          trace.toolConfidence = trace.toolConfidence || data.confidence || ''
          trace.routerStatus = 'done'
          trace.plannerStatus = 'running'
        },
        plan(data) {
          trace.plan = (data.steps || []).map((s) => ({ ...s, status: 'pending' }))
          trace.needsSynthesis = !!data.needs_synthesis
          trace.plannerStatus = 'done'
        },
        tool_done(data) {
          const p = trace.plan.find((s) => s.step === data.step)
          if (p) {
            p.status = data.status
            p.duration_ms = data.duration_ms
            p.summary = data.summary
          }
          if (data.step) trace.plan = [...trace.plan]
        },
        trace(data) {
          trace.structured = data
        },
        stage(data) {
          stageLabel.value = data.label || data.stage
          if (data.stage) trace.stages.push(data.stage)
          if (data.search_trace) trace.searchTrace = data.search_trace
          if (data.step) {
            const p = trace.plan.find((s) => s.step === data.step)
            if (p && p.status === 'pending') { p.status = 'running'; trace.plan = [...trace.plan] }
          }
          if (data.stage === 'synthesizing') trace.synthStatus = 'running'
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

          trace.tool = tool || trace.tool
          trace.toolReason = data.routed_tool?.reason || trace.toolReason
          trace.toolConfidence = data.routed_tool?.confidence || trace.toolConfidence
          trace.diagnosisMode = data.meta?.mode || ''
          trace.sql = trace.sql || data.sql || ''
          trace.structured = data.trace || trace.structured
          trace.toolResults = data.tool_results || null
          trace.routerStatus = 'done'
          trace.plannerStatus = 'done'
          if (trace.needsSynthesis) trace.synthStatus = 'done'

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
            trace: { ...trace },
          })
          if (tool) store.setTool(store.currentId, tool)
          stageLabel.value = ''
          streamDraft.value = ''
          displayedSql.value = ''
        },
        error(data) {
          errorMsg.value = data.message || '请求失败'
          const running = trace.plan.find((s) => s.status === 'running')
          if (running) { running.status = 'failed'; trace.plan = [...trace.plan] }
          if (trace.synthStatus === 'running') trace.synthStatus = 'failed'
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

function scrollToBottom() {
  nextTick(() => {
    if (contentEl.value) contentEl.value.scrollTop = contentEl.value.scrollHeight
  })
}

watch(() => store.currentId, () => { scrollToBottom(); activeTab.value = 'table' })
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

// ── Content formatting ──
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
    mock_fallback: 'LLM 失败回退模板',
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
    result.follow_up_questions.forEach((q, idx) => lines.push(`${idx + 1}. ${q}`))
  }
  return lines.join('\n')
}

// ── Chart ──
function buildChartOption(config, data) {
  const type = config.type || 'bar'
  const title = config.title || ''
  const colors = ['#2563eb','#059669','#d97706','#dc2626','#7c3aed','#0891b2','#db2777','#65a30d']
  const manyCategories = data.length > 8
  const base = {
    title: { text: title, left: 'center', top: 8, textStyle: { fontSize: 14, fontWeight: 600, color: '#1e293b' } },
    tooltip: { trigger: 'axis', textStyle: { fontSize: 12 } },
    color: colors,
    grid: { left: 48, right: 24, top: 56, bottom: manyCategories ? 72 : 48, containLabel: true },
  }
  if (type === 'pie') {
    const catField = config.category_field || config.categoryField || Object.keys(data[0])[0]
    const valField = config.value_field || config.valueField || Object.keys(data[0])[1]
    return {
      ...base,
      tooltip: { trigger: 'item', textStyle: { fontSize: 12 } },
      legend: data.length > 6 ? { type: 'scroll', bottom: 0, textStyle: { fontSize: 11 } } : undefined,
      series: [{
        type: 'pie',
        radius: ['32%', '62%'],
        center: ['50%', '52%'],
        data: data.map((r) => ({ name: r[catField], value: r[valField] })),
        label: { fontSize: 12, formatter: '{b}: {d}%' },
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
      axisLabel: { fontSize: 11, rotate: manyCategories ? 35 : 0, interval: 0, color: '#64748b' },
    },
    yAxis: { type: 'value', axisLabel: { fontSize: 11, color: '#64748b' } },
    dataZoom: manyCategories
      ? [{ type: 'inside' }, { type: 'slider', height: 20, bottom: 8 }]
      : undefined,
    series: [{
      type: type === 'line' ? 'line' : 'bar',
      data: data.map((r) => r[yField]),
      smooth: type === 'line',
      barMaxWidth: 40,
      label: data.length <= 12 ? { show: true, position: 'top', fontSize: 10 } : undefined,
    }],
  }
}

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

// ── Export ──
function exportChartPng(msgIdx) {
  const instance = chartRefs.get(msgIdx)
  if (!instance || typeof instance.getDataURL !== 'function') {
    ElMessage.warning('该图表暂不支持导出')
    return
  }
  const url = instance.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#fff' })
  downloadPng(url, `chart-${msgIdx}.png`)
  ElMessage.success('图表 PNG 已下载')
}

async function exportMsgScreenshot(msgIdx) {
  const el = msgDomRefs.get(msgIdx)
  if (!el) { ElMessage.warning('未找到消息 DOM 节点'); return }
  const url = await captureElement(el)
  if (!url) { ElMessage.error('截图失败'); return }
  downloadPng(url, `result-${msgIdx}.png`)
  ElMessage.success('截图已下载')
}

async function exportMsgTablePdf(msgIdx) {
  const msg = messages.value[msgIdx]
  const data = msg?.chartData || msg?.result?.data || msg?.result
  if (!data || !Array.isArray(data) || !data.length) {
    ElMessage.warning('没有可导出的表格数据')
    return
  }
  try {
    ElMessage.info('正在生成表格 PDF…')
    await exportTablePdf(data, '查询结果数据表')
    ElMessage.success('表格 PDF 已下载')
  } catch (err) {
    ElMessage.error(err?.message || 'PDF 导出失败')
  }
}

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
    ElMessage.success('会话已导出为 PDF')
  } catch (err) {
    ElMessage.error(err?.message || 'PDF 导出失败')
  }
}

function tableColumns(rows) {
  if (!rows?.length) return []
  return Object.keys(rows[0]).map((key) => ({ prop: key, label: key, minWidth: 100 }))
}

function setMsgRef(el, idx) {
  if (el) msgDomRefs.set(idx, el)
}

// ── Last result helper ──
const lastAssistantMsg = computed(() => {
  const msgs = messages.value
  for (let i = msgs.length - 1; i >= 0; i--) {
    if (msgs[i].role === 'assistant' && !msgs[i].streaming) return { msg: msgs[i], idx: i }
  }
  return null
})
</script>

<template>
  <div class="analysis-page">
    <!-- ═══ Session Sidebar ═══ -->
    <aside v-if="showSessionList" class="session-sidebar">
      <div class="session-sidebar-header">
        <span class="session-sidebar-title">会话列表</span>
        <el-button size="small" type="primary" @click="showNewDialog = true">
          <el-icon><Plus /></el-icon>
        </el-button>
      </div>

      <div class="session-list">
        <div
          v-for="s in store.sortedSessions"
          :key="s.id"
          class="session-item"
          :class="{ active: s.id === store.currentId }"
          @click="store.select(s.id)"
        >
          <div class="session-info">
            <div class="session-title">{{ s.title }}</div>
            <div class="session-meta">
              <span class="mode-badge" :class="s.tool">{{ modeLabel[s.tool] || '通用' }}</span>
              <span class="time">{{ formatTime(s.createdAt) }}</span>
            </div>
          </div>
          <el-button
            class="delete-btn"
            type="danger"
            :icon="Delete"
            circle
            size="small"
            plain
            @click="handleDeleteSession(s.id, $event)"
          />
        </div>

        <div v-if="store.sessions.length === 0" class="empty-sessions">
          <p>暂无会话</p>
          <p class="sub">创建新会话以开始数据分析或日志诊断</p>
        </div>
      </div>

      <div class="session-sidebar-footer">
        <span class="count">共 {{ store.sessions.length }} 个会话</span>
      </div>
    </aside>

    <!-- ═══ Main Pipeline Area ═══ -->
    <div class="pipeline-main">
      <!-- Header -->
      <div v-if="store.currentSession" class="pipeline-header">
        <div class="header-left">
          <el-button
            size="small"
            :icon="showSessionList ? 'Fold' : 'Expand'"
            text
            @click="showSessionList = !showSessionList"
          />
          <span class="session-title">{{ store.currentSession.title }}</span>
          <span class="mode-badge" :class="store.currentSession.tool">
            {{ modeLabel[store.currentSession.tool] || '通用' }}
          </span>
        </div>
        <div class="header-right">
          <el-button size="small" @click="exportFullSession" :disabled="!messages.length">
            <el-icon><Download /></el-icon>
            导出 PDF
          </el-button>
        </div>
      </div>

      <!-- Content -->
      <div ref="contentEl" class="pipeline-body">
        <!-- Empty -->
        <div v-if="!store.currentSession" class="empty-state">
          <div class="empty-icon">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" stroke-width="1.5">
              <circle cx="12" cy="12" r="3"/>
              <path d="M12 2v4m0 12v4M2 12h4m12 0h4"/>
              <path d="M5.64 5.64l2.83 2.83m7.07 7.07l2.83 2.83M5.64 18.36l2.83-2.83m7.07-7.07l2.83-2.83"/>
            </svg>
          </div>
          <h2>Data Agent 分析中心</h2>
          <p>面向业务人员的智能问数与经营分析</p>
          <div class="empty-actions">
            <el-button type="primary" @click="showNewDialog = true">
              <el-icon><Plus /></el-icon>
              新建分析
            </el-button>
            <el-button @click="$router.push('/log-diagnosis')">
              <el-icon><Upload /></el-icon>
              上传日志
            </el-button>
          </div>
        </div>

        <!-- Dataset warning -->
        <el-alert
          v-else-if="currentMode === 'sql' && !hasSqlDataset"
          class="inline-alert"
          title="该会话未绑定数据集，请新建数据分析会话并上传 Excel / CSV 文件"
          type="warning"
          :closable="false"
          show-icon
        />

        <!-- ── Messages as Pipeline ── -->
        <template v-for="(msg, i) in messages" :key="i">
          <div class="pipeline-group" :ref="(el) => setMsgRef(el, i)">
            <!-- User Request -->
            <div v-if="msg.role === 'user'" class="request-card">
              <div class="request-label">用户请求</div>
              <div class="request-text">{{ msg.content }}</div>
            </div>

            <!-- Assistant Response -->
            <template v-if="msg.role === 'assistant'">
              <!-- Orchestration Flow (Router → Planner → Tools → Synthesizer) -->
              <OrchestrationFlow
                v-if="msg.streaming || flowHasContent(msg.trace)"
                :flow="flowFromTrace(msg.streaming ? liveTrace : msg.trace)"
                :streaming="!!msg.streaming"
                :current-label="msg.streaming ? stageLabel : ''"
              />

              <!-- SQL Card -->
              <div v-if="msg.sql" class="sql-card">
                <div class="card-header">
                  <span>生成 SQL</span>
                  <div class="sql-card-actions">
                    <span v-if="msg.trace?.sqlRepaired" class="sql-repaired-tag">自动修复</span>
                    <button
                      class="btn-copy"
                      @click="navigator.clipboard.writeText(msg.sql); ElMessage.success('SQL 已复制')"
                    >复制</button>
                  </div>
                </div>
                <pre class="sql-code"><code>{{ msg.sql }}</code></pre>
                <div class="sql-footer">
                  <span class="sql-status">
                    <span class="status-dot-green" />
                    执行成功
                  </span>
                </div>
              </div>

              <!-- 核心结论：有 answer/content 即展示（不再因有表/图而隐藏） -->
              <div v-if="msg.content" class="content-card">
                <div class="card-header">核心结论</div>
                <div class="card-body" v-html="formatContent(msg.content)" />
              </div>

              <!-- Result Tabs (Table / Chart / SQL) -->
              <div v-if="(msg.chartData?.length || msg.chartConfig || msg.sql) && !msg.streaming" class="result-card">
                <div class="card-header">
                  <span>分析结果</span>
                  <div class="result-card-actions">
                    <el-tooltip content="导出图表 PNG" placement="top">
                      <el-button
                        v-if="msg.chartConfig && msg.chartData?.length"
                        size="small" text circle @click="exportChartPng(i)"
                      >
                        <el-icon><PictureFilled /></el-icon>
                      </el-button>
                    </el-tooltip>
                    <el-tooltip content="导出数据表 PDF" placement="top">
                      <el-button
                        v-if="msg.chartData?.length"
                        size="small" text circle @click="exportMsgTablePdf(i)"
                      >
                        <el-icon><Document /></el-icon>
                      </el-button>
                    </el-tooltip>
                    <el-tooltip content="截取结果图片" placement="top">
                      <el-button size="small" text circle @click="exportMsgScreenshot(i)">
                        <el-icon><Camera /></el-icon>
                      </el-button>
                    </el-tooltip>
                  </div>
                </div>

                <el-tabs v-model="activeTab" class="result-tabs">
                  <el-tab-pane label="数据表" name="table" v-if="msg.chartData?.length">
                    <div class="result-table-wrap">
                      <el-table
                        :data="msg.chartData.slice(0, 100)"
                        :border="true"
                        size="small"
                        stripe
                        max-height="400"
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
                      <div v-if="msg.chartData.length > 100" class="table-truncated">
                        仅展示前 100 行，共 {{ msg.chartData.length }} 行。导出 PDF 可查看完整数据。
                      </div>
                    </div>
                  </el-tab-pane>
                  <el-tab-pane label="图表" name="chart" v-if="msg.chartConfig && msg.chartData?.length">
                    <div class="result-chart-wrap">
                      <div
                        class="chart-canvas"
                        :ref="(el) => { if (el) onChartMount(el, i, msg.chartConfig, msg.chartData) }"
                      />
                    </div>
                  </el-tab-pane>
                  <el-tab-pane label="SQL" name="sql" v-if="msg.sql">
                    <pre class="sql-code tab-sql"><code>{{ msg.sql }}</code></pre>
                  </el-tab-pane>
                </el-tabs>
              </div>

              <!-- Streaming text only -->
              <div v-if="msg.streaming && msg.content && !msg.sql && !msg.chartConfig" class="streaming-card">
                <div class="card-body" v-html="formatContent(msg.content)" />
                <span v-if="msg.streaming && !stageLabel" class="typing-cursor">|</span>
              </div>

              <!-- Execution Trace -->
              <ExecutionTrace v-if="!msg.streaming && msg.trace && Object.keys(msg.trace).length" :trace="msg.trace" />
            </template>
          </div>
        </template>

        <!-- Error -->
        <div v-if="errorMsg" class="error-bar">
          <el-icon><WarningFilled /></el-icon>
          {{ errorMsg }}
        </div>
      </div>

      <!-- ═══ Input ═══ -->
      <div class="pipeline-input">
        <el-input
          v-model="input"
          :placeholder="inputPlaceholder"
          :disabled="loading || !store.currentId || !hasSqlDataset"
          @keydown="onKeydown"
          class="query-input"
        >
          <template #suffix>
            <el-button
              type="primary"
              :disabled="!input.trim() || loading || !store.currentId || !hasSqlDataset"
              :loading="loading"
              @click="send"
              size="small"
            >
              <el-icon><Promotion /></el-icon>
            </el-button>
          </template>
        </el-input>
      </div>
    </div>

    <!-- ═══ New Session Dialog ═══ -->
    <NewSessionDialog v-model:visible="showNewDialog" @start="onNewSession" />
  </div>
</template>


<style scoped>
.analysis-page {
  display: flex;
  height: 100%;
  width: 100%;
}

/* ── Session Sidebar ── */
.session-sidebar {
  width: 220px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-right: 1px solid #e2e8f0;
  background: #ffffff;
}

.session-sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
}

.session-sidebar-title {
  font-size: 13px;
  font-weight: 600;
  color: #475569;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.session-sidebar-footer {
  padding: 8px 14px;
  border-top: 1px solid #e2e8f0;
}

.session-sidebar-footer .count {
  font-size: 11px;
  color: #94a3b8;
}

.session-list {
  flex: 1;
  overflow-y: auto;
  padding: 0 6px;
}

.session-item {
  display: flex;
  align-items: center;
  padding: 8px 10px;
  margin: 1px 0;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.1s;
}
.session-item:hover { background: #f1f5f9; }
.session-item.active { background: #eff6ff; }

.session-info { flex: 1; min-width: 0; }
.session-title {
  font-size: 13px;
  font-weight: 500;
  color: #1e293b;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.session-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 2px;
}

.mode-badge {
  font-size: 10px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 4px;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}
.mode-badge.sql { background: #dbeafe; color: #1d4ed8; }
.mode-badge.log { background: #d1fae5; color: #059669; }

.time { font-size: 11px; color: #94a3b8; }

.delete-btn {
  flex-shrink: 0;
  color: #dc2626;
  border-color: #fecaca;
  background: #fee2e2;
  transition: all 0.15s;
  width: 24px; height: 24px;
  padding: 0;
}
.delete-btn:hover {
  color: #fff;
  background: #dc2626;
  border-color: #dc2626;
}

.empty-sessions {
  text-align: center;
  padding: 32px 12px;
  color: #94a3b8;
}
.empty-sessions p { margin: 4px 0; font-size: 13px; }
.empty-sessions .sub { font-size: 11px; }

/* ── Pipeline Main ── */
.pipeline-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  height: 100%;
  background: #f8fafc;
}

.pipeline-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  background: #ffffff;
  border-bottom: 1px solid #e2e8f0;
  flex-shrink: 0;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}
.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.pipeline-header .session-title {
  font-weight: 600;
  font-size: 14px;
  color: #1e293b;
}

/* ── Empty State ── */
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  gap: 12px;
  color: #64748b;
}
.empty-state h2 {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  color: #1e293b;
}
.empty-state p {
  margin: 0;
  font-size: 14px;
  color: #64748b;
}
.empty-actions {
  display: flex;
  gap: 10px;
  margin-top: 8px;
}

/* ── Pipeline Body ── */
.pipeline-body {
  flex: 1;
  overflow-y: auto;
  padding: 20px 28px;
}

.inline-alert {
  margin-bottom: 16px;
}

/* ── Cards ── */
.pipeline-group {
  margin-bottom: 20px;
}

.request-card {
  background: #ffffff;
  border: 1px solid #eef2f7;
  border-left: 3px solid #2563eb;
  border-radius: 14px;
  padding: 13px 18px;
  margin-bottom: 12px;
  box-shadow:
    5px 5px 14px rgba(148, 163, 184, 0.1),
    -5px -5px 14px rgba(255, 255, 255, 0.9);
}
.request-label {
  font-size: 10px;
  font-weight: 700;
  color: #2563eb;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 4px;
}
.request-text {
  font-size: 14px;
  color: #1e293b;
  font-weight: 500;
}

.pipeline-card,
.sql-card,
.content-card,
.result-card,
.streaming-card {
  background: #ffffff;
  border: 1px solid #eef2f7;
  border-radius: 14px;
  margin-bottom: 12px;
  overflow: hidden;
  box-shadow:
    5px 5px 14px rgba(148, 163, 184, 0.12),
    -5px -5px 14px rgba(255, 255, 255, 0.9);
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 11px 18px;
  font-size: 12px;
  font-weight: 600;
  color: #475569;
  background: #fafbfc;
  border-bottom: 1px solid #eef2f7;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.card-body {
  padding: 14px 16px;
  font-size: 13px;
  line-height: 1.7;
  color: #334155;
}

/* ── Pipeline Steps ── */
.pipeline-steps {
  padding: 12px 16px;
}

.pipeline-step {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  font-size: 13px;
  color: #64748b;
}

.pipeline-step.done {
  color: #475569;
}

.step-icon.done {
  color: #059669;
  font-size: 14px;
}

.step-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #2563eb;
  animation: dot-pulse 1.2s ease-in-out infinite;
  flex-shrink: 0;
}

@keyframes dot-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

.pipeline-step.running {
  color: #2563eb;
  font-weight: 500;
}

/* ── SQL Card ── */
.sql-card-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.sql-repaired-tag {
  font-size: 10px;
  font-weight: 600;
  color: #d97706;
  background: #fef3c7;
  padding: 2px 6px;
  border-radius: 3px;
  text-transform: uppercase;
}

.btn-copy {
  background: none;
  border: 1px solid #cbd5e1;
  color: #64748b;
  font-size: 11px;
  padding: 2px 10px;
  border-radius: 4px;
  cursor: pointer;
  font-family: inherit;
}
.btn-copy:hover {
  background: #f1f5f9;
  color: #1e293b;
  border-color: #94a3b8;
}

.sql-code {
  margin: 0;
  padding: 14px 16px;
  overflow-x: auto;
  background: #1e293b;
  font-family: 'IBM Plex Mono', 'Cascadia Code', Consolas, monospace;
  font-size: 12.5px;
  line-height: 1.7;
  color: #e2e8f0;
  white-space: pre;
}

.sql-code code {
  font-family: inherit;
  font-size: inherit;
  color: inherit;
}

.sql-footer {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  background: #f8fafc;
  border-top: 1px solid #e2e8f0;
  font-size: 12px;
  color: #64748b;
}

.status-dot-green {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #059669;
  display: inline-block;
  margin-right: 4px;
}

/* ── Result Card ── */
.result-card-actions {
  display: flex;
  gap: 2px;
}

.result-tabs {
  padding: 0 16px;
}

.result-tabs :deep(.el-tabs__header) {
  margin-bottom: 0;
}

.result-tabs :deep(.el-tabs__item) {
  font-size: 12px;
  font-weight: 500;
}

.result-table-wrap {
  padding-bottom: 12px;
}

.result-chart-wrap {
  padding: 8px 0 12px;
}

.chart-canvas {
  width: 100%;
  height: 380px;
  min-height: 320px;
}

.table-truncated {
  padding: 8px 0;
  font-size: 12px;
  color: #94a3b8;
  text-align: center;
}

.tab-sql {
  margin: 0;
  border-radius: 6px;
}

/* ── Streaming ── */
.streaming-card {
  border-left: 3px solid #2563eb;
}

.typing-cursor {
  color: #2563eb;
  font-weight: 300;
  animation: blink 1s step-end infinite;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

/* ── Error ── */
.error-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 14px;
  border-radius: 6px;
  background: #fee2e2;
  color: #dc2626;
  font-size: 13px;
  border: 1px solid #fecaca;
}

/* ── Input ── */
.pipeline-input {
  padding: 12px 28px 16px;
  background: #f8fafc;
  border-top: 1px solid #e2e8f0;
  flex-shrink: 0;
}

.query-input :deep(.el-input__wrapper) {
  padding-right: 8px;
}
</style>
