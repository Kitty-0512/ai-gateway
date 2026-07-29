/**
 * 导出工具：
 *   - CSV 生成与下载
 *   - ECharts getDataURL() 截图
 *   - html2canvas 截图
 *   - 会话级 PDF 报告（真实 HTML 表格 + 图表）
 */

// ── CSV ────────────────────────────────────────────────

export function toCsv(rows, columns) {
  if (!rows || !rows.length) return ''
  const headers = columns || Object.keys(rows[0])
  const bom = '\uFEFF'
  const headerLine = headers.join(',')
  const bodyLines = rows.map((row) =>
    headers
      .map((h) => {
        const val = row[h] ?? ''
        const str = String(val)
        if (str.includes(',') || str.includes('"') || str.includes('\n')) {
          return '"' + str.replace(/"/g, '""') + '"'
        }
        return str
      })
      .join(',')
  )
  return bom + [headerLine, ...bodyLines].join('\n')
}

export function downloadCsv(rows, filename, columns) {
  const csv = toCsv(rows, columns)
  downloadBlob(csv, filename || 'export.csv', 'text/csv;charset=utf-8')
}

// ── 通用下载 ───────────────────────────────────────────

export function downloadBlob(content, filename, mimeType = 'text/plain') {
  const blob = new Blob([content], { type: mimeType })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

// ── ECharts 截图 ───────────────────────────────────────

export function captureChart(chartInstance) {
  if (!chartInstance) return null
  try {
    if (typeof chartInstance.getDataURL === 'function') {
      return chartInstance.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#fff' })
    }
    return null
  } catch {
    return null
  }
}

// ── html2canvas 截图 ───────────────────────────────────

export async function captureElement(domEl) {
  if (!domEl) return null
  try {
    const html2canvas = (await import('html2canvas')).default
    const canvas = await html2canvas(domEl, {
      backgroundColor: '#ffffff',
      scale: 2,
      useCORS: true,
      logging: false,
    })
    return canvas.toDataURL('image/png')
  } catch (err) {
    console.warn('html2canvas 截图失败:', err)
    return null
  }
}

export function downloadPng(dataUrl, filename) {
  if (!dataUrl) return
  const a = document.createElement('a')
  a.href = dataUrl
  a.download = filename || 'screenshot.png'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

// ── HTML / PDF 工具 ────────────────────────────────────

const TOOL_LABEL = { sql: 'SQL 数据分析', log: '日志诊断' }

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function formatCell(value) {
  if (value == null) return ''
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

/** 把行数据转成真实 HTML 表格 */
export function rowsToHtmlTable(rows, maxRows = 200) {
  if (!rows?.length) return '<p class="muted">暂无数据</p>'
  const headers = Object.keys(rows[0])
  const sliced = rows.slice(0, maxRows)
  const thead = headers.map((h) => `<th>${escapeHtml(h)}</th>`).join('')
  const tbody = sliced
    .map(
      (row) =>
        `<tr>${headers.map((h) => `<td>${escapeHtml(formatCell(row[h]))}</td>`).join('')}</tr>`,
    )
    .join('')
  const more =
    rows.length > maxRows
      ? `<p class="muted">共 ${rows.length} 行，PDF 中展示前 ${maxRows} 行</p>`
      : ''
  return `
    <table class="data-table">
      <thead><tr>${thead}</tr></thead>
      <tbody>${tbody}</tbody>
    </table>
    ${more}
  `
}

function reportStyles() {
  return `
    * { box-sizing: border-box; }
    body { margin: 0; font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans SC", sans-serif; color: #1f2329; background: #fff; }
    .report { padding: 28px 32px; width: 800px; }
    h1 { font-size: 22px; margin: 0 0 8px; }
    .meta { color: #646a73; font-size: 12px; margin-bottom: 18px; line-height: 1.7; }
    .msg { margin: 0 0 22px; padding-bottom: 18px; border-bottom: 1px solid #e5e6eb; page-break-inside: avoid; }
    .role { font-size: 14px; font-weight: 700; margin-bottom: 6px; }
    .role.user { color: #3370ff; }
    .role.ai { color: #2ea121; }
    .time { font-weight: 400; color: #8f959e; font-size: 12px; margin-left: 8px; }
    .content { font-size: 13px; line-height: 1.7; white-space: pre-wrap; word-break: break-word; }
    .sql { background: #1e1e2e; color: #a6e3a1; padding: 10px 12px; border-radius: 6px; font-size: 11px; white-space: pre-wrap; margin: 8px 0; font-family: Consolas, "Courier New", monospace; }
    .section-title { font-size: 13px; font-weight: 700; margin: 12px 0 8px; color: #1f2329; }
    .chart-img { max-width: 100%; height: auto; display: block; margin: 8px 0; border: 1px solid #e5e6eb; border-radius: 6px; }
    table.data-table { width: 100%; border-collapse: collapse; font-size: 11px; margin: 8px 0 4px; }
    table.data-table th, table.data-table td { border: 1px solid #d0d3d9; padding: 6px 8px; text-align: left; vertical-align: top; }
    table.data-table th { background: #f2f3f5; font-weight: 700; color: #1f2329; }
    table.data-table tr:nth-child(even) td { background: #fafafa; }
    .muted { color: #8f959e; font-size: 11px; margin: 4px 0 0; }
    .tag { display: inline-block; padding: 1px 8px; border-radius: 4px; background: #e8f3ff; color: #1456f0; font-size: 11px; margin-bottom: 6px; }
  `
}

/**
 * 构建会话 HTML 报告（含真实表格）。
 */
export function buildSessionHtml(session, chartImages) {
  const chartImgs = chartImages || new Map()
  const tool = TOOL_LABEL[session?.tool] || session?.tool || '通用'
  const created = session?.createdAt ? new Date(session.createdAt).toLocaleString('zh-CN') : ''
  const parts = [
    `<div class="report">`,
    `<h1>${escapeHtml(session?.title || '对话导出')}</h1>`,
    `<div class="meta">工具：${escapeHtml(tool)}　|　创建时间：${escapeHtml(created)}　|　消息数：${session?.messages?.length || 0}</div>`,
  ]

  for (let i = 0; i < (session?.messages || []).length; i++) {
    const msg = session.messages[i]
    const isUser = msg.role === 'user'
    const time = msg.timestamp ? new Date(msg.timestamp).toLocaleString('zh-CN') : ''
    parts.push(`<div class="msg">`)
    parts.push(
      `<div class="role ${isUser ? 'user' : 'ai'}">${isUser ? '用户' : 'AI'}<span class="time">${escapeHtml(time)}</span></div>`,
    )

    if (isUser) {
      parts.push(`<div class="content">${escapeHtml(msg.content || '')}</div>`)
    } else {
      if (msg.tool) {
        parts.push(`<div class="tag">${escapeHtml(TOOL_LABEL[msg.tool] || msg.tool)}</div>`)
      }
      if (msg.sql) {
        parts.push(`<div class="section-title">SQL</div>`)
        parts.push(`<div class="sql">${escapeHtml(msg.sql)}</div>`)
      }
      if (msg.content) {
        parts.push(`<div class="content">${escapeHtml(msg.content)}</div>`)
      }
      const chartImg = chartImgs.get(i)
      if (chartImg) {
        parts.push(`<div class="section-title">图表</div>`)
        parts.push(`<img class="chart-img" src="${chartImg}" alt="chart-${i}" />`)
      }
      if (msg.chartData?.length) {
        parts.push(`<div class="section-title">数据表</div>`)
        parts.push(rowsToHtmlTable(msg.chartData))
      }
    }
    parts.push(`</div>`)
  }

  parts.push(`</div>`)
  return parts.join('\n')
}

/**
 * 将 DOM / HTML 转为多页 A4 PDF 并下载。
 */
async function renderHtmlToPdf(html, filename) {
  const host = document.createElement('div')
  host.setAttribute('data-pdf-export', '1')
  host.style.cssText = 'position:fixed;left:-10000px;top:0;width:800px;background:#fff;z-index:-1;'
  host.innerHTML = `<style>${reportStyles()}</style>${html}`
  document.body.appendChild(host)

  try {
    const html2canvas = (await import('html2canvas')).default
    const { jsPDF } = await import('jspdf')

    const target = host.querySelector('.report') || host
    const canvas = await html2canvas(target, {
      backgroundColor: '#ffffff',
      scale: 2,
      useCORS: true,
      logging: false,
      windowWidth: 800,
    })

    const pdf = new jsPDF({ orientation: 'p', unit: 'mm', format: 'a4' })
    const pageWidth = pdf.internal.pageSize.getWidth()
    const pageHeight = pdf.internal.pageSize.getHeight()
    const margin = 8
    const usableWidth = pageWidth - margin * 2
    const usableHeight = pageHeight - margin * 2
    const imgWidth = usableWidth
    const imgHeight = (canvas.height * imgWidth) / canvas.width

    const pageCanvas = document.createElement('canvas')
    const pageCtx = pageCanvas.getContext('2d')
    const pxPerMm = canvas.width / imgWidth
    const pageHeightPx = Math.floor(usableHeight * pxPerMm)

    let srcY = 0
    let pageIndex = 0
    while (srcY < canvas.height) {
      const sliceHeight = Math.min(pageHeightPx, canvas.height - srcY)
      pageCanvas.width = canvas.width
      pageCanvas.height = sliceHeight
      pageCtx.clearRect(0, 0, pageCanvas.width, pageCanvas.height)
      pageCtx.drawImage(
        canvas,
        0,
        srcY,
        canvas.width,
        sliceHeight,
        0,
        0,
        canvas.width,
        sliceHeight,
      )
      const sliceData = pageCanvas.toDataURL('image/jpeg', 0.92)
      const sliceMm = sliceHeight / pxPerMm
      if (pageIndex > 0) pdf.addPage()
      pdf.addImage(sliceData, 'JPEG', margin, margin, imgWidth, sliceMm)
      srcY += sliceHeight
      pageIndex += 1
    }

    // silence unused if single page edge case
    void imgHeight
    pdf.save(filename || 'export.pdf')
  } finally {
    document.body.removeChild(host)
  }
}

/**
 * 导出整段会话为 PDF（含真实表格与图表）。
 */
export async function exportSessionPdf(session, chartImages) {
  if (!session) return
  const html = buildSessionHtml(session, chartImages)
  const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
  const safeTitle = String(session.title || 'chat').replace(/[\\/:*?"<>|]/g, '_').slice(0, 40)
  await renderHtmlToPdf(html, `${safeTitle}-${stamp}.pdf`)
}

/**
 * 导出单张数据表为 PDF（真实表格）。
 */
export async function exportTablePdf(rows, title = '数据表') {
  if (!rows?.length) return
  const html = `
    <div class="report">
      <h1>${escapeHtml(title)}</h1>
      <div class="meta">导出时间：${escapeHtml(new Date().toLocaleString('zh-CN'))}　|　行数：${rows.length}</div>
      <div class="section-title">数据表</div>
      ${rowsToHtmlTable(rows)}
    </div>
  `
  const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
  await renderHtmlToPdf(html, `table-${stamp}.pdf`)
}

// ── 兼容旧 Markdown 导出（保留工具函数） ────────────────

export function buildSessionMarkdown(session, chartImages, cardImages) {
  const chartImgs = chartImages || new Map()
  const cardImgs = cardImages || new Map()
  const tool = TOOL_LABEL[session?.tool] || session?.tool || '通用'
  const created = session?.createdAt ? new Date(session.createdAt).toLocaleString('zh-CN') : ''
  const lines = [
    `# ${session?.title || '空会话'}`,
    '',
    `- 工具：**${tool}**`,
    `- 创建时间：${created}`,
    `- 消息数：${session?.messages?.length || 0}`,
    '',
    '---',
    '',
  ]

  for (let i = 0; i < (session?.messages || []).length; i++) {
    const msg = session.messages[i]
    const role = msg.role === 'user' ? '用户' : 'AI'
    const time = msg.timestamp ? new Date(msg.timestamp).toLocaleString('zh-CN') : ''
    lines.push(`### ${role}  \`${time}\``)
    lines.push('')

    if (msg.role === 'user') {
      lines.push(msg.content || '')
    } else {
      if (msg.tool) {
        lines.push(`> 路由到：**${TOOL_LABEL[msg.tool] || msg.tool}**`)
        lines.push('')
      }
      if (msg.sql) {
        lines.push('**SQL：**')
        lines.push('```sql')
        lines.push(msg.sql)
        lines.push('```')
        lines.push('')
      }
      if (msg.content) {
        lines.push(msg.content)
        lines.push('')
      }
      const chartImg = chartImgs.get(i)
      if (chartImg) {
        lines.push('### 图表')
        lines.push(`![chart-${i}](${chartImg})`)
        lines.push('')
      }
      const cardImg = cardImgs.get(i)
      if (!chartImg && cardImg) {
        lines.push('### 消息截图')
        lines.push(`![card-${i}](${cardImg})`)
        lines.push('')
      }
      if (msg.chartData?.length) {
        const headers = Object.keys(msg.chartData[0])
        lines.push('### 数据表')
        lines.push('')
        lines.push(`| ${headers.join(' | ')} |`)
        lines.push(`| ${headers.map(() => '---').join(' | ')} |`)
        msg.chartData.slice(0, 50).forEach((row) => {
          lines.push(`| ${headers.map((h) => String(row[h] ?? '').replace(/\|/g, '\\|')).join(' | ')} |`)
        })
        lines.push('')
      }
    }
    lines.push('---')
    lines.push('')
  }
  return lines.join('\n')
}

export function exportSessionMarkdown(session, chartImages, cardImages) {
  const md = buildSessionMarkdown(session, chartImages, cardImages)
  const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
  downloadBlob(md, `chat-export-${stamp}.md`, 'text/markdown;charset=utf-8')
}
