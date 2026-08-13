<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()

const activeMenu = computed(() => route.path)

const menuItems = [
  { path: '/',              label: '数据概览',    icon: 'DataBoard' },
  { path: '/analysis',      label: '分析中心',    icon: 'TrendCharts' },
  { path: '/sql-workspace', label: 'SQL 工作台',  icon: 'Document' },
  { path: '/log-diagnosis', label: '日志诊断',    icon: 'Monitor' },
]
</script>

<template>
  <div class="app-layout">
    <!-- ═══ Sidebar ═══ -->
    <aside class="app-sidebar">
      <div class="sidebar-brand">
        <span class="brand-icon">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="3"/>
            <path d="M12 2v4m0 12v4M2 12h4m12 0h4"/>
            <path d="M5.64 5.64l2.83 2.83m7.07 7.07l2.83 2.83M5.64 18.36l2.83-2.83m7.07-7.07l2.83-2.83"/>
          </svg>
        </span>
        <span class="brand-text">智能分析与运维诊断</span>
      </div>

      <el-menu
        :default-active="activeMenu"
        :router="true"
        class="sidebar-menu"
      >
        <el-menu-item
          v-for="item in menuItems"
          :key="item.path"
          :index="item.path"
        >
          <el-icon><component :is="item.icon" /></el-icon>
          <span>{{ item.label }}</span>
        </el-menu-item>
      </el-menu>

      <div class="sidebar-footer">
        <div class="status-indicator">
          <span class="status-dot" />
          <span class="status-text">系统运行中</span>
        </div>
        <div class="version-text">v3.0.0</div>
      </div>
    </aside>

    <!-- ═══ Main Content ═══ -->
    <main class="app-main">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.app-layout {
  display: flex;
  height: 100vh;
  width: 100%;
  background: #f8fafc;
}

/* ── Sidebar ── */
.app-sidebar {
  width: 220px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: #ffffff;
  border-right: 1px solid #e2e8f0;
}

.sidebar-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 20px 18px 16px;
}

.brand-icon {
  color: #2563eb;
  display: flex;
  align-items: center;
}

.brand-text {
  font-size: 15px;
  font-weight: 700;
  color: #1e293b;
  letter-spacing: -0.01em;
  white-space: nowrap;
}

.sidebar-menu {
  flex: 1;
  padding: 0 8px;
  border-right: none;
}

.sidebar-menu :deep(.el-menu-item) {
  border-radius: 6px;
  margin: 2px 0;
  height: 40px;
  line-height: 40px;
  font-size: 13px;
  color: #475569;
  font-weight: 500;
}

.sidebar-menu :deep(.el-menu-item:hover) {
  background: #f1f5f9;
  color: #1e293b;
}

.sidebar-menu :deep(.el-menu-item.is-active) {
  background: #eff6ff;
  color: #2563eb;
  font-weight: 600;
}

.sidebar-menu :deep(.el-menu-item .el-icon) {
  font-size: 18px;
}

/* ── Sidebar Footer ── */
.sidebar-footer {
  padding: 12px 18px;
  border-top: 1px solid #e2e8f0;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.status-indicator {
  display: flex;
  align-items: center;
  gap: 6px;
}

.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #059669;
}

.status-text {
  font-size: 12px;
  color: #64748b;
  font-weight: 500;
}

.version-text {
  font-size: 11px;
  color: #94a3b8;
}

/* ── Main ── */
.app-main {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  overflow-x: hidden;
}
</style>
