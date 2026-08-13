<script setup>
import { ref } from 'vue'

const props = defineProps({
  trace: { type: Object, default: () => ({}) },
})

const expanded = ref(false)

const diagnosisModeLabel = {
  llm: 'LLM 结构化诊断',
  mock: '本地模板诊断',
  mock_fallback: 'LLM 失败回退模板',
}

const STAGE_MAP = {
  understanding:  { label: '需求分析',       color: '#2563eb', icon: 'Search' },
  generating_sql: { label: 'SQL 生成',       color: '#2563eb', icon: 'Edit' },
  executing:      { label: '查询执行',       color: '#d97706', icon: 'CaretRight' },
  analyzing:      { label: '报告生成',       color: '#059669', icon: 'DataAnalysis' },
  charting:       { label: '图表生成',       color: '#059669', icon: 'TrendCharts' },
  cleaning:       { label: '日志清洗',       color: '#2563eb', icon: 'Brush' },
  identifying:    { label: '类型识别',       color: '#2563eb', icon: 'Search' },
  identified:     { label: '类型已识别',     color: '#059669', icon: 'CircleCheck' },
  locating:       { label: '根因分析',       color: '#d97706', icon: 'Aim' },
  searching:      { label: '外部检索',       color: '#7c3aed', icon: 'Connection' },
  search_done:    { label: '检索完成',       color: '#7c3aed', icon: 'Collection' },
  generating:     { label: '诊断生成',       color: '#059669', icon: 'Document' },
  refining:       { label: '二次诊断',       color: '#2563eb', icon: 'Refresh' },
  repaired:       { label: 'SQL 已修复',     color: '#d97706', icon: 'Warning' },
  fallback:       { label: '本地回退',       color: '#dc2626', icon: 'WarningFilled' },
}

const TOOL_LABEL = { sql: 'SQL 数据分析', log: '日志诊断', mcp: '数据/文件工具' }
const STATUS_STYLE = {
  success: { color: '#059669', icon: 'CircleCheck' },
  needs_input: { color: '#d97706', icon: 'QuestionFilled' },
  failed: { color: '#dc2626', icon: 'CircleClose' },
  running: { color: '#2563eb', icon: 'Loading' },
  pending: { color: '#94a3b8', icon: 'More' },
}

function fmtMs(ms) {
  if (ms == null) return ''
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

function buildStructuredSteps(st) {
  const steps = []

  if (st.routing?.tool) {
    steps.push({
      label: '路由判断',
      detail: TOOL_LABEL[st.routing.tool] || st.routing.tool,
      reason: st.routing.reason || '',
      color: '#2563eb',
      icon: 'Guide',
    })
  }

  const plan = st.plan || []
  if (plan.length) {
    steps.push({
      label: `执行计划（${plan.length} 步）`,
      detail: plan.map((p) => `Step ${p.step}: ${TOOL_LABEL[p.tool] || p.tool}`).join('\n'),
      color: '#7c3aed',
      icon: 'List',
    })
  }

  for (const call of st.tool_calls || []) {
    const style = STATUS_STYLE[call.status] || STATUS_STYLE.pending
    const stageText = (call.stages || []).map((s) => STAGE_MAP[s]?.label || s).join(' → ')
    const detailLines = []
    if (call.summary) detailLines.push(call.summary)
    if (call.error) detailLines.push(`错误：${call.error}`)
    if (stageText) detailLines.push(`阶段：${stageText}`)
    if (call.sql) detailLines.push(`SQL：${call.sql}`)
    steps.push({
      label: `Step ${call.step} · ${TOOL_LABEL[call.tool] || call.tool}${call.duration_ms != null ? '  ' + fmtMs(call.duration_ms) : ''}`,
      detail: detailLines.join('\n'),
      color: style.color,
      icon: style.icon,
      code: !!call.sql,
    })
  }

  if (st.synthesis?.invoked) {
    steps.push({
      label: `综合分析${st.synthesis.duration_ms != null ? '  ' + fmtMs(st.synthesis.duration_ms) : ''}`,
      detail: st.synthesis.model ? `模型：${st.synthesis.model}` : '',
      color: '#059669',
      icon: 'MagicStick',
    })
  }

  return steps
}

function buildSteps() {
  const t = props.trace || {}

  // 新版结构化 trace（来自编排层）优先
  if (t.structured && (t.structured.tool_calls?.length || t.structured.plan?.length)) {
    return buildStructuredSteps(t.structured)
  }

  const steps = []

  steps.push({
    label: '路由判断',
    detail: t.tool === 'sql' ? 'SQL 数据分析' : t.tool === 'log' ? '日志诊断' : (t.tool || '未知'),
    reason: t.toolReason || '',
    color: '#2563eb',
    icon: 'Guide',
  })

  if (t.diagnosisMode) {
    steps.push({
      label: '诊断来源',
      detail: diagnosisModeLabel[t.diagnosisMode] || t.diagnosisMode,
      color: t.diagnosisMode === 'llm' ? '#059669' : '#d97706',
      icon: t.diagnosisMode === 'llm' ? 'Monitor' : 'Warning',
    })
  }

  if (t.stages && t.stages.length) {
    for (const s of t.stages) {
      const info = STAGE_MAP[s] || { label: s, color: '#94a3b8', icon: 'More' }
      steps.push({
        label: info.label,
        detail: '',
        color: info.color,
        icon: info.icon,
      })
    }
  }

  if (t.sql) {
    steps.push({
      label: t.sqlRepaired ? 'SQL（自动修复）' : '生成 SQL',
      detail: t.sql,
      color: t.sqlRepaired ? '#d97706' : '#059669',
      icon: t.sqlRepaired ? 'Warning' : 'Document',
      code: true,
    })
  }

  if (t.retryCount > 0) {
    steps.push({
      label: `SQL 自动修复：${t.retryCount} 次重试`,
      detail: t.retryErrors?.join('\n') || `共 ${t.retryCount} 次重试后成功`,
      color: '#d97706',
      icon: 'Refresh',
    })
  }

  if (t.searchTrace) {
    const st = t.searchTrace
    const sources = st.sources || []
    const detailLines = [
      `搜索关键词: ${st.query || ''}`,
      `搜索结果: ${st.source_count || 0} 条，成功抓取 ${st.fetched_count || 0} 条`,
      '',
      '引用来源:',
      ...sources.map((s, i) => `${i + 1}. [${s.title}](${s.url})`),
    ]
    steps.push({
      label: `外部检索（${st.fetched_count || 0} 条引用）`,
      detail: detailLines.join('\n'),
      color: '#7c3aed',
      icon: 'Connection',
    })
  }

  if (t.evidence && t.evidence.length) {
    steps.push({
      label: `引用日志证据（${t.evidence.length} 条）`,
      detail: t.evidence.map((e, i) => `${i + 1}. ${e}`).join('\n'),
      color: '#059669',
      icon: 'Collection',
      code: true,
    })
  }

  return steps
}
</script>

<template>
  <div v-if="buildSteps().length" class="trace-wrap">
    <button class="trace-toggle" @click="expanded = !expanded">
      <el-icon class="toggle-icon"><component :is="expanded ? 'ArrowDown' : 'ArrowRight'" /></el-icon>
      执行过程
      <span class="step-count">{{ buildSteps().length }} 步</span>
    </button>

    <div v-show="expanded" class="trace-body">
      <div
        v-for="(step, i) in buildSteps()"
        :key="i"
        class="trace-step"
      >
        <div class="tl-line">
          <div class="tl-dot" :style="{ background: step.color }" />
          <div v-if="i < buildSteps().length - 1" class="tl-bar" />
        </div>

        <div class="tl-content">
          <div class="tl-label">
            <el-icon class="tl-icon" :style="{ color: step.color }">
              <component :is="step.icon" />
            </el-icon>
            {{ step.label }}
          </div>
          <div v-if="step.reason" class="tl-reason">{{ step.reason }}</div>
          <pre
            v-if="step.detail"
            class="tl-detail"
            :class="{ code: step.code }"
          >{{ step.detail }}</pre>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.trace-wrap {
  margin-top: 8px;
  border-top: 1px solid #e2e8f0;
  padding-top: 8px;
}

.trace-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: none;
  border: none;
  font-size: 12px;
  color: #64748b;
  cursor: pointer;
  padding: 2px 0;
  font-family: inherit;
  font-weight: 500;
}
.trace-toggle:hover { color: #2563eb; }
.toggle-icon { font-size: 11px; }
.step-count { color: #94a3b8; font-size: 10px; font-weight: 400; }

.trace-body {
  margin-top: 8px;
  padding-left: 2px;
}

.trace-step {
  display: flex;
  gap: 10px;
}

.tl-line {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 12px;
  flex-shrink: 0;
}

.tl-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  margin-top: 4px;
}

.tl-bar {
  width: 2px;
  flex: 1;
  min-height: 10px;
  background: #e2e8f0;
  margin: 3px 0;
}

.tl-content {
  flex: 1;
  padding-bottom: 10px;
  min-width: 0;
}

.tl-label {
  font-size: 12px;
  font-weight: 500;
  color: #334155;
  display: flex;
  align-items: center;
  gap: 6px;
}

.tl-icon {
  font-size: 13px;
  flex-shrink: 0;
}

.tl-reason {
  font-size: 11px;
  color: #94a3b8;
  margin-top: 2px;
}

.tl-detail {
  margin: 4px 0 0;
  padding: 6px 8px;
  background: #f8fafc;
  border-radius: 6px;
  font-size: 11px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 200px;
  overflow: auto;
  color: #334155;
}

.tl-detail.code {
  font-family: 'IBM Plex Mono', Consolas, monospace;
  background: #1e293b;
  color: #e2e8f0;
}
</style>
