<script setup>
import { computed } from 'vue'
import { useSessionStore } from '@/store/sessions'
import { ElMessageBox } from 'element-plus'

const store = useSessionStore()

const emit = defineEmits(['new-session'])

const toolLabel = { sql: '数据分析', log: '日志诊断' }
const toolColor = { sql: 'primary', log: 'success' }

function formatTime(iso) {
  const d = new Date(iso)
  const now = new Date()
  const diff = now - d
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return Math.floor(diff / 60000) + ' 分钟前'
  if (diff < 86400000) return Math.floor(diff / 3600000) + ' 小时前'
  return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}

async function handleDelete(id, e) {
  e.stopPropagation()
  try {
    await ElMessageBox.confirm('确定删除该会话？', '删除', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
    store.remove(id)
  } catch { /* cancelled */ }
}
</script>

<template>
  <aside class="sidebar">
    <!-- 头部 -->
    <div class="sidebar-header">
      <h2 class="brand">AI 分析网关</h2>
      <el-button type="primary" size="small" @click="emit('new-session')">
        <el-icon><Plus /></el-icon>
        新建会话
      </el-button>
    </div>

    <!-- 会话列表 -->
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
            <el-tag
              v-if="s.tool"
              :type="toolColor[s.tool] || 'info'"
              size="small"
              effect="plain"
            >
              {{ toolLabel[s.tool] || s.tool }}
            </el-tag>
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
          @click="handleDelete(s.id, $event)"
        />
      </div>

      <!-- 空状态 -->
      <div v-if="store.sessions.length === 0" class="empty-state">
        <el-icon :size="36"><ChatDotSquare /></el-icon>
        <p>还没有会话</p>
        <p class="sub">点击上方按钮或拖拽文件开始</p>
      </div>
    </div>

    <!-- 底部状态 -->
    <div class="sidebar-footer">
      <el-divider />
      <span class="count">共 {{ store.sessions.length }} 个会话</span>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  width: 280px;
  height: 100vh;
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--el-border-color-light);
  background: var(--el-bg-color);
}

.sidebar-header {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.brand {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
  color: var(--el-color-primary);
  letter-spacing: -0.02em;
}

.session-list {
  flex: 1;
  overflow-y: auto;
  padding: 0 8px;
}

.session-item {
  display: flex;
  align-items: center;
  padding: 10px 12px;
  margin: 2px 0;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s;
}
.session-item:hover { background: var(--el-fill-color-light); }
.session-item.active { background: var(--el-color-primary-light-9); }

.session-info { flex: 1; min-width: 0; }
.session-title {
  font-size: 14px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.session-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 4px;
}
.time { font-size: 12px; color: var(--el-text-color-secondary); }

.delete-btn {
  flex-shrink: 0;
  color: var(--el-color-danger);
  border-color: var(--el-color-danger-light-5);
  background: var(--el-color-danger-light-9);
  transition: background 0.15s, border-color 0.15s, color 0.15s;
}
.delete-btn:hover {
  color: #fff;
  background: var(--el-color-danger);
  border-color: var(--el-color-danger);
}

.empty-state {
  text-align: center;
  padding: 48px 16px;
  color: var(--el-text-color-secondary);
}
.empty-state p { margin: 8px 0 0; font-size: 14px; }
.empty-state .sub { font-size: 12px; }

.sidebar-footer { padding: 8px 16px 12px; }
.count { font-size: 12px; color: var(--el-text-color-placeholder); }
</style>
