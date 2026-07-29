<script setup>
import { ref } from 'vue'
import { useSessionStore } from '@/store/sessions'
import SessionSidebar from '@/components/SessionSidebar.vue'
import NewSessionDialog from '@/components/NewSessionDialog.vue'
import ChatPanel from '@/components/ChatPanel.vue'

const store = useSessionStore()
const showNewDialog = ref(false)

function onNewSession(payload = {}) {
  const fileName = payload.meta?.fileName
  const sessionTitle = fileName
    ? `${payload.tool === 'sql' ? '数据集' : '日志'}：${fileName}`
    : payload.tool === 'sql'
      ? '数据分析会话'
      : payload.tool === 'log'
        ? '日志诊断会话'
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
</script>

<template>
  <div class="home-layout">
    <SessionSidebar @new-session="showNewDialog = true" />
    <ChatPanel />
    <NewSessionDialog v-model:visible="showNewDialog" @start="onNewSession" />
  </div>
</template>

<style scoped>
.home-layout {
  display: flex;
  height: 100vh;
  width: 100%;
}
</style>
