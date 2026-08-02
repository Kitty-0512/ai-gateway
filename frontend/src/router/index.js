import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'Dashboard',
    component: () => import('@/views/DashboardView.vue'),
  },
  {
    path: '/analysis',
    name: 'Analysis',
    component: () => import('@/views/AnalysisView.vue'),
  },
  {
    path: '/sql-workspace',
    name: 'SQLWorkspace',
    component: () => import('@/views/SQLWorkspaceView.vue'),
  },
  {
    path: '/log-diagnosis',
    name: 'LogDiagnosis',
    component: () => import('@/views/LogDiagnosisView.vue'),
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
