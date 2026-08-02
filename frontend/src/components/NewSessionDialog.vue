<script setup>
import { ref, computed } from 'vue'
import { ElMessage } from 'element-plus'
import { uploadSqlFile } from '@/api/client'

const emit = defineEmits(['start'])
const visible = defineModel('visible', { type: Boolean, default: false })

const tool = ref('log')
const text = ref('')
const file = ref(null)
const fileText = ref('')
const dragging = ref(false)
const submitting = ref(false)

const acceptExtensions = computed(() =>
  tool.value === 'sql' ? '.xlsx,.xls,.csv' : '.log,.txt,.out'
)
const fileName = computed(() => file.value?.name || '')
const modeTitle = computed(() =>
  tool.value === 'sql' ? '数据分析' : '日志诊断'
)
const inputPlaceholder = computed(() =>
  tool.value === 'sql'
    ? '上传 Excel / CSV 数据集，之后可在会话中提问分析'
    : '直接粘贴日志内容，或上传日志文件后自动分析'
)

function onDragOver(e) {
  e.preventDefault()
  dragging.value = true
}
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
  const allowed = tool.value === 'sql'
    ? ['.xlsx', '.xls', '.csv']
    : ['.log', '.txt', '.out']
  if (!allowed.includes(ext)) {
    ElMessage.warning(tool.value === 'sql'
      ? '仅支持 .xlsx / .xls / .csv 格式'
      : '仅支持 .log / .txt / .out 格式')
    return
  }
  file.value = f
  if (tool.value === 'log') {
    const reader = new FileReader()
    reader.onload = () => { fileText.value = String(reader.result ?? '') }
    reader.readAsText(f)
  } else {
    fileText.value = ''
  }
}

function resetForm() {
  text.value = ''
  file.value = null
  fileText.value = ''
  visible.value = false
}

function changeTool(nextTool) {
  tool.value = nextTool
  file.value = null
  fileText.value = ''
  text.value = ''
}

async function handleStart() {
  if (submitting.value) return
  submitting.value = true

  try {
    if (tool.value === 'sql') {
      if (!file.value) {
        ElMessage.warning('请先上传数据文件')
        return
      }

      const result = await uploadSqlFile(file.value)
      emit('start', {
        tool: 'sql',
        meta: {
          fileName: file.value.name,
          selectedMode: 'sql',
          datasetIds: result.dataset_id ? [result.dataset_id] : [],
          datasetInfo: result,
        },
        welcomeMessage: `已上传数据集《${file.value.name}》\n表名：${result.table_name}\n行数：${result.row_count}\n现在可以继续提问，例如：各门店销售额排名、月度趋势分析。`,
      })
      ElMessage.success('数据集上传成功')
      resetForm()
      return
    }

    const logContent = file.value && fileText.value
      ? fileText.value
      : text.value.trim()

    const payload = {
      tool: 'log',
      meta: {
        fileName: file.value?.name || '',
        selectedMode: 'log',
        logContent,
      },
    }

    if (file.value && fileText.value) {
      payload.pendingSend = {
        payload: {
          mode: 'log',
          file_name: file.value.name,
          content: fileText.value,
        },
        userMessage: `分析日志文件：${file.value.name}`,
      }
    } else if (text.value.trim()) {
      payload.pendingSend = {
        payload: {
          mode: 'log',
          content: text.value.trim(),
        },
        userMessage: '分析以下日志内容',
      }
    } else {
      ElMessage.warning('请先上传日志文件或输入日志内容')
      return
    }

    emit('start', payload)
    resetForm()
  } catch (err) {
    ElMessage.error(err.message || '启动会话失败')
  } finally {
    submitting.value = false
  }
}

const canStart = computed(() =>
  tool.value === 'sql' ? !!file.value : !!text.value.trim() || !!(file.value && fileText.value)
)
</script>

<template>
  <el-dialog
    v-model="visible"
    title="新建会话"
    width="520px"
    :close-on-click-modal="false"
    destroy-on-close
  >
    <div class="tool-switch">
      <button
        type="button"
        class="tool-card"
        :class="{ active: tool === 'sql' }"
        @click="changeTool('sql')"
      >
        <el-icon :size="18"><TrendCharts /></el-icon>
        <div class="tool-card-text">
          <strong>数据分析</strong>
          <span>上传 Excel / CSV，生成 SQL 查询</span>
        </div>
      </button>
      <button
        type="button"
        class="tool-card"
        :class="{ active: tool === 'log' }"
        @click="changeTool('log')"
      >
        <el-icon :size="18"><Monitor /></el-icon>
        <div class="tool-card-text">
          <strong>日志诊断</strong>
          <span>上传日志或粘贴错误内容</span>
        </div>
      </button>
    </div>

    <!-- Drop zone -->
    <div
      class="drop-zone"
      :class="{ dragging }"
      @dragover="onDragOver"
      @dragleave="onDragLeave"
      @drop="onDrop"
    >
      <div v-if="!file" class="drop-hint">
        <el-icon :size="32" color="#94a3b8"><UploadFilled /></el-icon>
        <p>拖拽{{ modeTitle }}文件到这里</p>
        <p class="sub">当前支持 {{ acceptExtensions }}</p>
        <label class="file-btn">
          <input type="file" :accept="acceptExtensions" hidden @change="onFileChange" />
          <el-button size="small" tag="span">选择文件</el-button>
        </label>
      </div>
      <div v-else class="file-loaded">
        <el-tag type="success" size="large" effect="plain" closable @close="file = null; fileText = ''">
          <el-icon><Document /></el-icon>
          {{ fileName }}
        </el-tag>
        <p v-if="tool === 'log'" class="preview">{{ fileText.slice(0, 500) }}{{ fileText.length > 500 ? '...' : '' }}</p>
        <p v-else class="preview">文件将导入为数据集，之后可在会话中提问分析。</p>
      </div>
    </div>

    <!-- Text area -->
    <div v-if="tool === 'log'" class="text-area">
      <el-input
        v-model="text"
        type="textarea"
        :rows="3"
        :placeholder="inputPlaceholder"
      />
    </div>
    <div v-else class="sql-tip">
      <el-alert
        title="文件上传后将解析为数据集表，随后可用自然语言查询分析。"
        type="info"
        :closable="false"
        show-icon
      />
    </div>

    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :disabled="!canStart || submitting" :loading="submitting" @click="handleStart">
        开始会话
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.tool-switch {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-bottom: 16px;
}

.tool-card {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 14px;
  background: #ffffff;
  text-align: left;
  cursor: pointer;
  transition: all 0.15s;
  font-family: inherit;
  color: #475569;
}

.tool-card:hover {
  border-color: #cbd5e1;
  background: #f8fafc;
}

.tool-card.active {
  border-color: #2563eb;
  background: #f8faff;
  color: #1e293b;
}

.tool-card-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.tool-card strong {
  display: block;
  font-size: 14px;
  font-weight: 600;
}

.tool-card span {
  color: #64748b;
  font-size: 11px;
}

.drop-zone {
  border: 2px dashed #cbd5e1;
  border-radius: 6px;
  padding: 24px;
  text-align: center;
  transition: all 0.15s;
  margin-bottom: 16px;
}
.drop-zone.dragging {
  border-color: #2563eb;
  background: #f8faff;
}
.drop-hint p { margin: 8px 0 0; color: #475569; }
.drop-hint .sub { font-size: 12px; color: #94a3b8; }
.file-btn { margin-top: 10px; display: inline-block; cursor: pointer; }

.file-loaded { text-align: left; }
.preview {
  margin: 10px 0 0;
  font-family: 'IBM Plex Mono', monospace;
  font-size: 11px;
  line-height: 1.5;
  color: #64748b;
  max-height: 100px;
  overflow: hidden;
  white-space: pre-wrap;
  word-break: break-all;
}

.text-area { margin-top: 4px; }
.sql-tip { margin-top: 4px; }
</style>
