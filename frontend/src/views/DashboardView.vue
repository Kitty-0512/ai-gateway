<script setup>
import { useRouter } from 'vue-router'
import { useSessionStore } from '@/store/sessions'

const router = useRouter()
const store = useSessionStore()

const statCards = [
  { label: '分析任务',  value: store.totalSessions,     icon: 'DataBoard',   color: '#2563eb' },
  { label: '查询记录',  value: store.sqlQueryCount,     icon: 'TrendCharts', color: '#059669' },
  { label: '诊断报告',  value: store.logDiagnosisCount, icon: 'Monitor',     color: '#d97706' },
  { label: '数据集',    value: store.datasetCount,      icon: 'Document',    color: '#7c3aed' },
]
</script>

<template>
  <div class="dashboard">
    <div class="dashboard-header">
      <div>
        <h1 class="dashboard-title">AI 分析控制台</h1>
        <p class="dashboard-subtitle">统一的数据分析与运维诊断工作空间</p>
      </div>
    </div>

    <!-- ═══ Stats ═══ -->
    <div class="stats-grid">
      <div
        v-for="card in statCards"
        :key="card.label"
        class="stat-card"
      >
        <div class="stat-icon" :style="{ background: card.color + '15', color: card.color }">
          <el-icon :size="20"><component :is="card.icon" /></el-icon>
        </div>
        <div class="stat-info">
          <div class="stat-value">{{ card.value }}</div>
          <div class="stat-label">{{ card.label }}</div>
        </div>
      </div>
    </div>

    <!-- ═══ Two-column ═══ -->
    <div class="dashboard-grid">
      <!-- Recent Tasks -->
      <div class="dashboard-card">
        <div class="card-header">
          <span>最近任务</span>
        </div>
        <div class="card-body">
          <div v-if="store.recentSessions.length === 0" class="card-empty">
            暂无分析记录，创建第一个分析任务或上传日志开始使用。
          </div>
          <div
            v-for="s in store.recentSessions"
            :key="s.id"
            class="recent-item"
            @click="router.push('/analysis'); store.select(s.id)"
          >
            <div class="recent-info">
              <div class="recent-title">{{ s.title }}</div>
              <div class="recent-meta">
                <span class="mode-tag" :class="s.tool">
                  {{ s.tool === 'sql' ? 'SQL' : s.tool === 'log' ? 'LOG' : '通用' }}
                </span>
                <span class="recent-time">{{ new Date(s.createdAt).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) }}</span>
              </div>
            </div>
            <span class="recent-msgs">{{ s.messages.length }} 条消息</span>
          </div>
        </div>
      </div>

      <!-- Quick Actions -->
      <div class="dashboard-card">
        <div class="card-header">
          <span>快捷操作</span>
        </div>
        <div class="card-body">
          <div class="action-grid">
            <button class="action-btn" @click="router.push('/analysis')">
              <el-icon :size="22"><TrendCharts /></el-icon>
              <span class="action-label">新建分析</span>
              <span class="action-desc">上传数据，生成 SQL，可视化结果</span>
            </button>
            <button class="action-btn" @click="router.push('/log-diagnosis')">
              <el-icon :size="22"><Monitor /></el-icon>
              <span class="action-label">日志诊断</span>
              <span class="action-desc">上传日志，检测异常，获取解决方案</span>
            </button>
            <button class="action-btn" @click="router.push('/sql-workspace')">
              <el-icon :size="22"><Document /></el-icon>
              <span class="action-label">SQL 工作台</span>
              <span class="action-desc">浏览数据结构，编写和执行查询</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dashboard {
  max-width: 1100px;
  margin: 0 auto;
  padding: 36px 32px;
}

.dashboard-header {
  margin-bottom: 28px;
}

.dashboard-title {
  margin: 0;
  font-size: 24px;
  font-weight: 700;
  color: #1e293b;
  letter-spacing: -0.02em;
}

.dashboard-subtitle {
  margin: 4px 0 0;
  font-size: 14px;
  color: #64748b;
}

/* ── Stats ── */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
  margin-bottom: 28px;
}

.stat-card {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  padding: 18px 20px;
  display: flex;
  align-items: center;
  gap: 14px;
}

.stat-icon {
  width: 44px;
  height: 44px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.stat-value {
  font-size: 26px;
  font-weight: 700;
  color: #1e293b;
  line-height: 1.1;
}

.stat-label {
  font-size: 12px;
  color: #64748b;
  font-weight: 500;
  margin-top: 2px;
}

/* ── Grid ── */
.dashboard-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.dashboard-card {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  overflow: hidden;
}

.dashboard-card .card-header {
  padding: 12px 18px;
  font-size: 13px;
  font-weight: 600;
  color: #475569;
  background: #f8fafc;
  border-bottom: 1px solid #e2e8f0;
}

.dashboard-card .card-body {
  padding: 14px 18px;
}

.card-empty {
  color: #94a3b8;
  font-size: 13px;
  padding: 16px 0;
}

/* ── Recent Items ── */
.recent-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 0;
  border-bottom: 1px solid #f1f5f9;
  cursor: pointer;
  transition: color 0.1s;
}
.recent-item:last-child { border-bottom: none; }
.recent-item:hover .recent-title { color: #2563eb; }

.recent-title {
  font-size: 13px;
  font-weight: 500;
  color: #1e293b;
}
.recent-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 3px;
}

.mode-tag {
  font-size: 10px;
  font-weight: 700;
  padding: 1px 5px;
  border-radius: 3px;
  letter-spacing: 0.05em;
}
.mode-tag.sql { background: #dbeafe; color: #1d4ed8; }
.mode-tag.log { background: #d1fae5; color: #059669; }

.recent-time {
  font-size: 11px;
  color: #94a3b8;
}
.recent-msgs {
  font-size: 11px;
  color: #94a3b8;
}

/* ── Quick Actions ── */
.action-grid {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.action-btn {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  background: #ffffff;
  cursor: pointer;
  text-align: left;
  transition: all 0.15s;
  font-family: inherit;
  color: #475569;
}
.action-btn:hover {
  border-color: #2563eb;
  background: #fafbff;
  color: #1e293b;
}

.action-label {
  font-size: 14px;
  font-weight: 600;
}

.action-desc {
  font-size: 12px;
  color: #94a3b8;
  margin-left: auto;
}
</style>
