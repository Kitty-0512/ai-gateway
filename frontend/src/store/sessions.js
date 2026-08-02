/**
 * Session Pinia Store.
 *
 * All session data persisted to localStorage.
 * Each session:
 *   {
 *     id: string,
 *     title: string,
 *     tool: string,        // "sql" | "log" | ""
 *     createdAt: string,   // ISO
 *     messages: []         // [{ role, content, tool?, sql?, chartConfig?, ... }]
 *     meta: {}
 *   }
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { load, save } from '@/utils/storage'

const STORAGE_KEY = 'gateway_sessions'

export const useSessionStore = defineStore('sessions', () => {
  // ── State ──
  const sessions = ref(load(STORAGE_KEY, []))
  const currentId = ref(null)

  // ── Computed ──
  const currentSession = computed(() =>
    sessions.value.find((s) => s.id === currentId.value) || null
  )

  const sortedSessions = computed(() =>
    [...sessions.value].sort((a, b) =>
      new Date(b.createdAt) - new Date(a.createdAt)
    )
  )

  /** Recent 5 sessions for Dashboard */
  const recentSessions = computed(() =>
    sortedSessions.value.slice(0, 5)
  )

  /** Total session count */
  const totalSessions = computed(() => sessions.value.length)

  /** Sessions with SQL tool */
  const sqlSessions = computed(() =>
    sessions.value.filter((s) => s.tool === 'sql')
  )

  /** Sessions with log tool */
  const logSessions = computed(() =>
    sessions.value.filter((s) => s.tool === 'log')
  )

  /** Count of assistant messages with SQL result (query completions) */
  const sqlQueryCount = computed(() => {
    let count = 0
    for (const s of sessions.value) {
      for (const m of s.messages) {
        if (m.role === 'assistant' && m.sql) count++
      }
    }
    return count
  })

  /** Count of sessions with at least one message */
  const activeSessionCount = computed(() =>
    sessions.value.filter((s) => s.messages.length > 0).length
  )

  /** Count of data files uploaded (SQL sessions with dataset) */
  const datasetCount = computed(() =>
    sqlSessions.value.filter((s) => s.meta?.datasetIds?.length).length
  )

  /** Count log diagnoses completed (log sessions with at least one assistant message containing result) */
  const logDiagnosisCount = computed(() => {
    let count = 0
    for (const s of logSessions.value) {
      const hasResult = s.messages.some(
        (m) => m.role === 'assistant' && m.result && !m.streaming
      )
      if (hasResult) count++
    }
    return count
  })

  // ── Persist ──
  function _persist() {
    save(STORAGE_KEY, sessions.value)
  }

  // ── Session CRUD ──
  function create(initialData = {}) {
    const id = Date.now().toString(36) + Math.random().toString(36).slice(2, 6)
    const session = {
      id,
      title: 'New Session',
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
    if (s && s.title === 'New Session') {
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
    if (role === 'user' && s.title === 'New Session') {
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
    recentSessions,
    totalSessions,
    sqlSessions,
    logSessions,
    sqlQueryCount,
    activeSessionCount,
    datasetCount,
    logDiagnosisCount,
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
