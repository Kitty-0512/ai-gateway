<script setup>
import { ref } from 'vue'

const props = defineProps({
  trace: { type: Object, default: () => ({}) },
})

const expanded = ref(false)

// ── 步骤数据 ──
function buildSteps() {
  const t = props.trace || {}
  const steps = []
  const diagnosisModeLabel = {
    llm: 'LLM 结构化诊断',
    mock: '本地模板诊断',
    mock_fallback: 'LLM 失败后回退模板',
  }

  // 1. 路由判断
  steps.push({
    label: '路由判断',
    detail: t.tool === 'sql' ? 'SQL 数据分析' : t.tool === 'log' ? '日志诊断' : (t.tool || '未知'),
    reason: t.toolReason || '',
    color: '#409eff',
    icon: '📍',
  })

  if (t.diagnosisMode) {
    steps.push({
      label: '诊断来源',
      detail: diagnosisModeLabel[t.diagnosisMode] || t.diagnosisMode,
      color: t.diagnosisMode === 'llm' ? '#67c23a' : '#e6a23c',
      icon: t.diagnosisMode === 'llm' ? '🤖' : '⚠️',
    })
  }

  // 2. 执行阶段
  if (t.stages && t.stages.length) {
    for (const s of t.stages) {
      const stageInfo = STAGE_MAP[s] || { label: s, color: '#909399' }
      steps.push({
        label: stageInfo.label,
        detail: '',
        color: stageInfo.color,
        icon: stageInfo.icon,
      })
    }
  }

  // 3. SQL 详情
  if (t.sql) {
    steps.push({
      label: t.sqlRepaired ? 'SQL（已自动修复）' : '生成的 SQL',
      detail: t.sql,
      color: t.sqlRepaired ? '#e6a23c' : '#67c23a',
      icon: t.sqlRepaired ? '🔧' : '📝',
      code: true,
    })
  }

  // 4. SQL 重试信息
  if (t.retryCount > 0) {
    steps.push({
      label: `SQL 自动修复：${t.retryCount} 次重试`,
      detail: t.retryErrors?.join('\n') || `共 ${t.retryCount} 次重试后成功`,
      color: '#e6a23c',
      icon: '🔄',
    })
  }

  // 5. 外部检索
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
      label: `外部检索 (${st.fetched_count || 0} 条引用)`,
      detail: detailLines.join('\n'),
      color: '#9b59b6',
      icon: '🌐',
    })
  }

  // 6. 日志证据
  if (t.evidence && t.evidence.length) {
    steps.push({
      label: `引用日志证据（${t.evidence.length} 条）`,
      detail: t.evidence.map((e, i) => `${i + 1}. ${e}`).join('\n'),
      color: '#67c23a',
      icon: '📋',
      code: true,
    })
  }

  return steps
}

const STAGE_MAP = {
  understanding:  { label: '理解问题',       color: '#409eff', icon: '🧠' },
  generating_sql: { label: '生成 SQL',       color: '#409eff', icon: '⚙️' },
  executing:      { label: '执行查询',       color: '#e6a23c', icon: '⏳' },
  analyzing:      { label: '生成分析报告',   color: '#67c23a', icon: '📊' },
  charting:       { label: '生成图表',       color: '#67c23a', icon: '📈' },
  cleaning:       { label: '清洗日志',       color: '#409eff', icon: '🧹' },
  identifying:    { label: '识别日志类型',   color: '#409eff', icon: '🔍' },
  identified:     { label: '已识别类型',     color: '#67c23a', icon: '✅' },
  locating:       { label: '定位根因',       color: '#e6a23c', icon: '🎯' },
  searching:      { label: '检索外部知识库', color: '#9b59b6', icon: '🌐' },
  search_done:    { label: '外部检索完成',   color: '#9b59b6', icon: '📚' },
  generating:     { label: '生成诊断建议',   color: '#67c23a', icon: '💡' },
  refining:       { label: '二次诊断',       color: '#409eff', icon: '🔁' },
  repaired:       { label: 'SQL 已修复',     color: '#e6a23c', icon: '🔧' },
  fallback:       { label: 'LLM 失败，本地回退', color: '#f56c6c', icon: '⚠️' },
}
</script>

<template>
  <div v-if="buildSteps().length" class="trace-wrap">
    <button class="trace-toggle" @click="expanded = !expanded">
      <span class="toggle-icon">{{ expanded ? '▾' : '▸' }}</span>
      执行过程
      <span class="step-count">{{ buildSteps().length }} 步</span>
    </button>

    <div v-show="expanded" class="trace-body">
      <div
        v-for="(step, i) in buildSteps()"
        :key="i"
        class="trace-step"
      >
        <!-- 时间线 -->
        <div class="tl-line">
          <div class="tl-dot" :style="{ background: step.color }" />
          <div v-if="i < buildSteps().length - 1" class="tl-bar" />
        </div>

        <!-- 内容 -->
        <div class="tl-content">
          <div class="tl-label">
            <span class="tl-icon">{{ step.icon }}</span>
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
  border-top: 1px solid var(--el-border-color-lighter);
  padding-top: 6px;
}

.trace-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: none;
  border: none;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  cursor: pointer;
  padding: 2px 0;
}
.trace-toggle:hover { color: var(--el-color-primary); }
.toggle-icon { font-size: 10px; width: 12px; text-align: center; }
.step-count { color: var(--el-text-color-placeholder); font-size: 11px; }

/* ── 时间线 ── */
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
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
  margin-top: 3px;
}

.tl-bar {
  width: 2px;
  flex: 1;
  min-height: 10px;
  background: var(--el-border-color-light);
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
  color: var(--el-text-color-regular);
  display: flex;
  align-items: center;
  gap: 4px;
}

.tl-icon { font-size: 12px; }

.tl-reason {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  margin-top: 2px;
  font-style: italic;
}

.tl-detail {
  margin: 4px 0 0;
  padding: 6px 8px;
  background: #f5f7fa;
  border-radius: 6px;
  font-size: 11px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 200px;
  overflow: auto;
  color: var(--el-text-color-regular);
}

.tl-detail.code {
  font-family: 'IBM Plex Mono', Consolas, monospace;
  background: #1e1e2e;
  color: #a6e3a1;
}
</style>
