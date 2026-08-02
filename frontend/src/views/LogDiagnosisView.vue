<script setup>
import { ref, reactive, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { unifiedChatStream } from '@/api/client'

const file = ref(null)
const fileText = ref('')
const pasteText = ref('')
const dragging = ref(false)

const fileName = computed(() => file.value?.name || '')
const modeTitle = computed(() => '日志诊断')

const loading = ref(false)
const result = reactive({
  summary: '',
  anomalyType: '',
  rootCause: '',
  riskLevel: '',
  severityScore: null,
  evidence: [],
  investigationSteps: [],
  followUpQuestions: [],
  diagnosisMode: '',
})
const hasResult = ref(false)
const errorMsg = ref('')
let abortCtrl = null

const followUpInput = ref('')
const followUpLoading = ref(false)
const followUpAnswer = ref('')

function onDragOver(e) { e.preventDefault(); dragging.value = true }
function onDragLeave() { dragging.value = false }
function onDrop(e) {
  e.preventDefault()
  dragging.value = false
  const f = e.dataTransfer?.files?.[0]
  if (f) loadFile(f)
}
function onFileChange(e) {
  const f = e.target?.files?.[0]
  if (f) loadFile(f)
}
function loadFile(f) {
  const ext = `.${String(f.name || '').split('.').pop()?.toLowerCase() || ''}`
  if (!['.log', '.txt', '.out'].includes(ext)) {
    ElMessage.warning('仅支持 .log / .txt / .out 格式')
    return
  }
  file.value = f
  const reader = new FileReader()
  reader.onload = () => { fileText.value = String(reader.result ?? '') }
  reader.readAsText(f)
}

async function runDiagnosis() {
  const content = fileText.value || pasteText.value.trim()
  if (!content) {
    ElMessage.warning('请先上传日志文件或粘贴日志内容')
    return
  }

  loading.value = true
  hasResult.value = false
  errorMsg.value = ''
  followUpAnswer.value = ''

  Object.assign(result, {
    summary: '', anomalyType: '', rootCause: '', riskLevel: '',
    severityScore: null, evidence: [], investigationSteps: [],
    followUpQuestions: [], diagnosisMode: '',
  })

  abortCtrl?.abort()
  abortCtrl = new AbortController()

  try {
    await unifiedChatStream(
      {
        mode: 'log',
        content,
        file_name: file.value?.name || undefined,
      },
      {
        delta() {},
        result(data) {
          const res = data.result || {}
          Object.assign(result, {
            summary: res.summary || '',
            anomalyType: res.anomaly_type || '',
            rootCause: res.root_cause || '',
            riskLevel: res.risk_level || '',
            severityScore: res.severity_score ?? null,
            evidence: Array.isArray(res.evidence) ? res.evidence : [],
            investigationSteps: Array.isArray(res.investigation_steps) ? res.investigation_steps : [],
            followUpQuestions: Array.isArray(res.follow_up_questions) ? res.follow_up_questions : [],
            diagnosisMode: data.meta?.mode || '',
          })
          hasResult.value = true
        },
        error(data) {
          errorMsg.value = data.message || '诊断失败'
        },
      },
      abortCtrl.signal,
    )
  } catch (err) {
    if (err.name !== 'AbortError') {
      errorMsg.value = err.message || '网络错误'
    }
  } finally {
    loading.value = false
    abortCtrl = null
  }
}

async function sendFollowUp() {
  const text = followUpInput.value.trim()
  if (!text || followUpLoading.value) return

  const content = fileText.value || pasteText.value.trim()
  if (!content) return

  followUpLoading.value = true
  followUpAnswer.value = ''
  followUpInput.value = ''

  abortCtrl?.abort()
  abortCtrl = new AbortController()

  try {
    await unifiedChatStream(
      {
        mode: 'log',
        content,
        extra_context: text,
        question: text,
      },
      {
        delta(data) {
          followUpAnswer.value += data.text || ''
        },
        result(data) {
          followUpAnswer.value = data.answer || followUpAnswer.value || '无额外发现'
        },
        error(data) {
          followUpAnswer.value = '错误：' + (data.message || '请求失败')
        },
      },
      abortCtrl.signal,
    )
  } catch (err) {
    if (err.name !== 'AbortError') {
      followUpAnswer.value = '网络错误'
    }
  } finally {
    followUpLoading.value = false
    abortCtrl = null
  }
}

const diagnosisModeLabels = {
  llm: 'LLM 结构化诊断',
  mock: '本地模板诊断',
  mock_fallback: 'LLM 失败回退模板',
}

function severityColor(score) {
  if (score >= 8) return '#dc2626'
  if (score >= 5) return '#d97706'
  return '#059669'
}

function riskColor(level) {
  const map = { high: '#dc2626', medium: '#d97706', low: '#059669' }
  return map[String(level).toLowerCase()] || '#64748b'
}

function riskLabel(level) {
  const map = { high: '高', medium: '中', low: '低' }
  return map[String(level).toLowerCase()] || level
}
</script>

<template>
  <div class="log-page">
    <div class="log-content">
      <h1 class="log-title">日志诊断</h1>
      <p class="log-subtitle">上传日志文件或粘贴错误内容，自动检测异常并给出处理建议</p>

      <!-- ═══ Upload Zone ═══ -->
      <div
        class="drop-zone"
        :class="{ dragging }"
        @dragover="onDragOver"
        @dragleave="onDragLeave"
        @drop="onDrop"
      >
        <div v-if="!file" class="drop-inner">
          <el-icon :size="32" color="#64748b"><UploadFilled /></el-icon>
          <p>拖拽{{ modeTitle }}文件到这里</p>
          <p class="drop-hint">支持 .log、.txt、.out 格式</p>
          <label class="file-picker">
            <input type="file" accept=".log,.txt,.out" hidden @change="onFileChange" />
            <el-button size="small" tag="span">选择文件</el-button>
          </label>
        </div>
        <div v-else class="file-loaded">
          <el-tag type="success" size="large" effect="plain" closable @close="file = null; fileText = ''">
            <el-icon><Document /></el-icon>
            {{ fileName }}
          </el-tag>
          <pre class="file-preview">{{ fileText.slice(0, 400) }}{{ fileText.length > 400 ? '...' : '' }}</pre>
        </div>
      </div>

      <div class="paste-area">
        <el-input
          v-model="pasteText"
          type="textarea"
          :rows="4"
          placeholder="或直接粘贴日志内容…"
          :disabled="!!file"
        />
      </div>

      <div class="run-bar">
        <el-button
          type="primary"
          size="large"
          :loading="loading"
          :disabled="!fileText && !pasteText.trim()"
          @click="runDiagnosis"
        >
          <el-icon><Monitor /></el-icon>
          开始诊断
        </el-button>
      </div>

      <div v-if="errorMsg" class="error-bar">
        <el-icon><WarningFilled /></el-icon> {{ errorMsg }}
      </div>

      <!-- ═══ Diagnosis Report ═══ -->
      <div v-if="hasResult" class="report">
        <div class="report-header">
          <h3>诊断报告</h3>
          <div class="report-tags">
            <span v-if="result.riskLevel" class="report-tag" :style="{ background: riskColor(result.riskLevel) + '18', color: riskColor(result.riskLevel) }">
              风险等级：{{ riskLabel(result.riskLevel) }}
            </span>
            <span v-if="result.severityScore !== null" class="report-tag" :style="{ background: severityColor(result.severityScore) + '18', color: severityColor(result.severityScore) }">
              严重度：{{ result.severityScore }}/10
            </span>
            <span v-if="result.anomalyType" class="report-tag category-tag">
              类别：{{ result.anomalyType }}
            </span>
            <span v-if="result.diagnosisMode" class="report-tag source-tag">
              来源：{{ diagnosisModeLabels[result.diagnosisMode] || result.diagnosisMode }}
            </span>
          </div>
        </div>

        <div v-if="result.summary" class="report-section">
          <div class="section-title">现象概述</div>
          <div class="section-body">{{ result.summary }}</div>
        </div>

        <div v-if="result.anomalyType" class="report-section">
          <div class="section-title">异常类型</div>
          <div class="section-body anomaly-type">{{ result.anomalyType }}</div>
        </div>

        <div v-if="result.rootCause" class="report-section">
          <div class="section-title">可能根因</div>
          <div class="section-body">{{ result.rootCause }}</div>
        </div>

        <div v-if="result.evidence.length" class="report-section">
          <div class="section-title">关键证据</div>
          <div
            v-for="(ev, i) in result.evidence"
            :key="i"
            class="evidence-block"
          >
            <pre class="evidence-code"><code>{{ ev }}</code></pre>
          </div>
        </div>

        <div v-if="result.investigationSteps.length" class="report-section">
          <div class="section-title">处理建议</div>
          <ul class="steps-list">
            <li v-for="(step, i) in result.investigationSteps" :key="i">
              <span class="step-num">{{ i + 1 }}</span>
              {{ step }}
            </li>
          </ul>
        </div>

        <div v-if="result.followUpQuestions.length" class="report-section">
          <div class="section-title">建议追问</div>
          <ul class="followup-list">
            <li v-for="(q, i) in result.followUpQuestions" :key="i">{{ q }}</li>
          </ul>
        </div>
      </div>

      <!-- ═══ Follow-up ═══ -->
      <div v-if="hasResult" class="follow-up-area">
        <div class="follow-up-input-row">
          <el-input
            v-model="followUpInput"
            placeholder="输入追问内容…"
            :disabled="followUpLoading"
            @keydown.enter="sendFollowUp"
          />
          <el-button
            type="primary"
            :disabled="!followUpInput.trim() || followUpLoading"
            :loading="followUpLoading"
            @click="sendFollowUp"
          >
            发送
          </el-button>
        </div>
        <div v-if="followUpAnswer" class="follow-up-answer">
          <div class="section-title">追问回答</div>
          <div class="section-body">{{ followUpAnswer }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.log-page {
  max-width: 860px;
  margin: 0 auto;
  padding: 36px 32px;
}

.log-title {
  margin: 0;
  font-size: 24px;
  font-weight: 700;
  color: #1e293b;
}

.log-subtitle {
  margin: 4px 0 24px;
  font-size: 14px;
  color: #64748b;
}

.drop-zone {
  border: 2px dashed #cbd5e1;
  border-radius: 6px;
  padding: 32px;
  text-align: center;
  background: #ffffff;
  transition: all 0.15s;
  margin-bottom: 14px;
}
.drop-zone.dragging {
  border-color: #2563eb;
  background: #f8faff;
}
.drop-inner p { margin: 8px 0 0; color: #475569; font-size: 14px; }
.drop-hint { font-size: 12px !important; color: #94a3b8 !important; }
.file-picker { margin-top: 12px; display: inline-block; cursor: pointer; }

.file-loaded { text-align: left; }
.file-preview {
  margin: 12px 0 0;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 11px;
  line-height: 1.5;
  color: #64748b;
  max-height: 100px;
  overflow: hidden;
  white-space: pre-wrap;
  word-break: break-all;
}

.paste-area { margin-bottom: 14px; }
.run-bar { margin-bottom: 24px; }

.error-bar {
  display: flex; align-items: center; gap: 6px;
  padding: 10px 14px; border-radius: 6px;
  background: #fee2e2; color: #dc2626; font-size: 13px;
  border: 1px solid #fecaca;
  margin-bottom: 20px;
}

.report {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  overflow: hidden;
}

.report-header {
  padding: 16px 20px;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
}
.report-header h3 {
  margin: 0 0 10px;
  font-size: 15px;
  font-weight: 700;
  color: #1e293b;
}
.report-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.report-tag {
  font-size: 11px;
  font-weight: 600;
  padding: 3px 8px;
  border-radius: 4px;
}
.category-tag {
  background: #f1f5f9;
  color: #475569;
}
.source-tag {
  background: #f1f5f9;
  color: #64748b;
}

.report-section {
  padding: 16px 20px;
  border-bottom: 1px solid #f1f5f9;
}
.report-section:last-child { border-bottom: none; }

.section-title {
  font-size: 11px;
  font-weight: 700;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 6px;
}

.section-body {
  font-size: 14px;
  line-height: 1.7;
  color: #334155;
}

.anomaly-type {
  font-weight: 600;
  font-size: 15px;
  color: #dc2626;
}

.evidence-block { margin-top: 6px; }
.evidence-code {
  margin: 0;
  padding: 12px 14px;
  background: #1e293b;
  color: #e2e8f0;
  border-radius: 6px;
  font-family: 'IBM Plex Mono', Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}
.evidence-code code {
  font-family: inherit;
  font-size: inherit;
  color: inherit;
}

.steps-list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.steps-list li {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 5px 0;
  font-size: 14px;
  color: #334155;
  line-height: 1.6;
}
.step-num {
  width: 20px; height: 20px;
  background: #f1f5f9;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 600;
  color: #64748b;
  flex-shrink: 0;
}

.followup-list {
  margin: 0;
  padding: 0 0 0 18px;
}
.followup-list li {
  padding: 3px 0;
  font-size: 13px;
  color: #475569;
}

.follow-up-area { margin-top: 24px; }
.follow-up-input-row { display: flex; gap: 8px; }
.follow-up-answer {
  margin-top: 16px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 16px 20px;
}
</style>
