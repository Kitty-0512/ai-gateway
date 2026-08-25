/**
 * 网关 API 客户端。
 *
 * - unifiedChat(payload)          → 非流式 POST /api/chat
 * - unifiedChatStream(payload, handlers, signal) → SSE 流式
 */

const API_BASE = '/api'

// ── 非流式 ──────────────────────────────────────────────

export async function unifiedChat(payload) {
  const res = await fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const detail = await _parseError(res)
    throw new Error(detail)
  }
  return res.json()
}

export async function fetchDefaultDatasets() {
  const res = await fetch(`${API_BASE}/sql/default-datasets`)
  if (!res.ok) {
    throw new Error(await _parseError(res))
  }
  return res.json()
}

export async function uploadSqlFile(file) {
  const formData = new FormData()
  formData.append('file', file)

  const res = await fetch(`${API_BASE}/sql/upload`, {
    method: 'POST',
    body: formData,
  })

  if (!res.ok) {
    throw new Error(await _parseError(res))
  }

  return res.json()
}

export async function uploadLogFile(file, extra = {}) {
  const formData = new FormData()
  formData.append('file', file)
  if (extra.log_type) formData.append('log_type', extra.log_type)
  if (extra.extra_context) formData.append('extra_context', extra.extra_context)

  const res = await fetch(`${API_BASE}/log/upload`, {
    method: 'POST',
    body: formData,
  })

  if (!res.ok) {
    throw new Error(await _parseError(res))
  }

  return res.json()
}

// ── SSE 流式 ─────────────────────────────────────────────

/**
 * SSE 事件处理器类型（统一编排入口 /api/chat/stream）：
 *   session(data)   → { session_id }
 *   routing(data)   → { tool, reason, confidence }
 *   plan(data)      → { steps: [{step, tool}], needs_synthesis, source }
 *   stage(data)     → { step?, tool?, stage, label }
 *   sql(data)       → { step?, sql, repaired? }
 *   tool_done(data) → { step, tool, status, summary, duration_ms, error? }
 *   delta(data)     → { text, source? }
 *   trace(data)     → 完整 trace JSON
 *   result(data)    → 完整响应 JSON（含 routed_tool, answer / result 等）
 *   error(data)     → { message }
 */
export async function unifiedChatStream(payload, handlers = {}, signal) {
  const res = await fetch(`${API_BASE}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  })

  if (!res.ok) {
    throw new Error(await _parseError(res))
  }

  // 如果后端返回的不是 SSE（比如直接返回 JSON），直接当 result 处理
  const ct = res.headers.get('content-type') || ''
  if (!ct.includes('text/event-stream')) {
    const json = await res.json()
    handlers.result?.(json)
    return
  }

  // SSE 解析
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    let idx
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      const block = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)

      if (block.startsWith(':')) continue // keep-alive

      const eventMatch = block.match(/^event:\s*(.+)$/m)
      const dataMatch = block.match(/^data:\s*(.+)$/m)
      if (!dataMatch) continue

      const event = eventMatch ? eventMatch[1].trim() : 'message'
      try {
        const data = JSON.parse(dataMatch[1])
        if (event === 'stage') handlers.stage?.(data)
        else if (event === 'sql') handlers.sql?.(data)
        else if (event === 'delta') handlers.delta?.(data)
        else if (event === 'result') handlers.result?.(data)
        else if (event === 'error') handlers.error?.(data)
        else if (event === 'session') handlers.session?.(data)
        else if (event === 'routing') handlers.routing?.(data)
        else if (event === 'plan') handlers.plan?.(data)
        else if (event === 'tool_done') handlers.tool_done?.(data)
        else if (event === 'trace') handlers.trace?.(data)
      } catch (e) {
        console.warn('SSE parse warning:', e)
      }
    }
  }
}

// ── 健康检查 ─────────────────────────────────────────────

export async function checkHealth() {
  try {
    const res = await fetch(`${API_BASE}/sessions`)
    return res.ok
  } catch {
    return false
  }
}

// ── helpers ──────────────────────────────────────────────

async function _parseError(res) {
  try {
    const data = await res.json()
    return data.detail || JSON.stringify(data)
  } catch {
    return res.statusText || '请求失败'
  }
}
