/**
 * 会话 Pinia Store。
 *
 * 所有会话数据持久化到 localStorage，不依赖后端数据库。
 * 每个会话结构：
 *   {
 *     id: string,          // 时间戳 + 随机数
 *     title: string,       // 第一条用户消息的前 20 字
 *     tool: string,        // "sql" | "log" | ""（从后端 routed_tool 获取）
 *     createdAt: string,   // ISO 时间
 *     messages: []         // [{ role, content, tool?, sql?, chartConfig?, ... }]
 *   }
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { load, save } from '@/utils/storage'

const STORAGE_KEY = 'gateway_sessions'

export const useSessionStore = defineStore('sessions', () => {
  // ── 状态 ──
  const sessions = ref(load(STORAGE_KEY, []))
  const currentId = ref(null)

  // ── 计算属性 ──
  const currentSession = computed(() =>
    sessions.value.find((s) => s.id === currentId.value) || null
  )

  const sortedSessions = computed(() =>
    [...sessions.value].sort((a, b) =>
      new Date(b.createdAt) - new Date(a.createdAt)
    )
  )

  // ── 持久化 ──
  function _persist() {
    save(STORAGE_KEY, sessions.value)
  }

  // ── 会话操作 ──
  function create(initialData = {}) {
    const id = Date.now().toString(36) + Math.random().toString(36).slice(2, 6)
    const session = {
      id,
      title: '新会话',
      tool: '',
      createdAt: new Date().toISOString(),
      messages: [],
      meta: {},
      ...initialData,
    }
    sessions.value.push(session)
    currentId.value = id
    _persist()
    return session
  }

  function select(id) {
    currentId.value = id
  }

  function remove(id) {
    sessions.value = sessions.value.filter((s) => s.id !== id)
    if (currentId.value === id) {
      currentId.value = sessions.value[0]?.id || null
    }
    _persist()
  }

  function setTitle(id, title) {
    const s = sessions.value.find((s) => s.id === id)
    if (s && s.title === '新会话') {
      s.title = title.slice(0, 20)
      _persist()
    }
  }

  function setTool(id, tool) {
    const s = sessions.value.find((s) => s.id === id)
    if (s && tool) {
      s.tool = tool
      _persist()
    }
  }

  function setMeta(id, meta = {}) {
    const s = sessions.value.find((s) => s.id === id)
    if (!s) return
    s.meta = { ...(s.meta || {}), ...meta }
    _persist()
  }

  function setPendingSend(id, pendingSend = null) {
    const s = sessions.value.find((s) => s.id === id)
    if (!s) return
    s._pendingSend = pendingSend
    _persist()
  }

  function addMessage(id, role, content, extra = {}) {
    const s = sessions.value.find((s) => s.id === id)
    if (!s) return
    s.messages.push({ role, content, timestamp: new Date().toISOString(), ...extra })
    if (role === 'user' && s.title === '新会话') {
      setTitle(id, content)
    }
    _persist()
  }

  function updateLastMessage(id, updates) {
    const s = sessions.value.find((s) => s.id === id)
    if (!s || !s.messages.length) return
    Object.assign(s.messages[s.messages.length - 1], updates)
    _persist()
  }

  return {
    sessions,
    currentId,
    currentSession,
    sortedSessions,
    create,
    select,
    remove,
    setTitle,
    setTool,
    setMeta,
    setPendingSend,
    addMessage,
    updateLastMessage,
  }
})
