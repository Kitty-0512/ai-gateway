/**
 * Capture Data Agent feature screenshots.
 * Prerequisites: backend :8000, frontend :3000
 * Usage: node docs/screenshots/_capture_features.mjs
 */
import { createRequire } from 'module'
import { mkdirSync } from 'fs'
import { dirname, join } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const require = createRequire(join(__dirname, '../../frontend/package.json'))
const { chromium } = require('playwright')

const OUT = __dirname
const BASE = 'http://127.0.0.1:3000'
mkdirSync(OUT, { recursive: true })

async function shot(page, name) {
  await page.waitForTimeout(700)
  await page.screenshot({ path: join(OUT, name), fullPage: false })
  console.log('saved', name)
}

async function closeDialog(page) {
  const dialog = page.locator('.el-overlay-dialog, [role="dialog"]')
  if (await dialog.count()) {
    await page.keyboard.press('Escape').catch(() => {})
    await page.waitForTimeout(400)
    const closeBtn = page.locator('.el-dialog__headerbtn, .el-overlay-dialog .el-dialog__close').first()
    if (await closeBtn.isVisible().catch(() => false)) {
      await closeBtn.click({ force: true }).catch(() => {})
      await page.waitForTimeout(300)
    }
  }
}

const SAMPLE_LOG = `[2026-08-20 10:12:01] INFO  gateway started on :8000
[2026-08-20 10:15:33] WARN  sql_timeout query_id=q_9182 elapsed_ms=3200
[2026-08-20 10:15:34] ERROR connection pool exhausted pool=mysql max=20 active=20
[2026-08-20 10:15:35] ERROR Text-to-SQL failed: OperationalError (2013) Lost connection to MySQL server during query
[2026-08-20 10:16:02] WARN  retry attempt=2/3
[2026-08-20 10:16:10] ERROR UpstreamTimeout: LLM provider response exceeded 30s
[2026-08-20 10:17:01] INFO  request completed status=500 path=/api/chat/unified`

async function main() {
  const browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

  // 07 Dashboard
  await page.goto(`${BASE}/`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1200)
  await shot(page, '07-dashboard.png')

  // 08 Analysis home (empty / welcome)
  await page.goto(`${BASE}/analysis`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(1000)
  await closeDialog(page)
  await shot(page, '08-analysis-home.png')

  // Open new session dialog to show SEO builtin option
  const newBtn = page.getByRole('button', { name: /新建会话|新会话|新建/ })
  if (await newBtn.count()) {
    await newBtn.first().click().catch(() => {})
    await page.waitForTimeout(800)
  } else {
    // try empty-state CTA
    const cta = page.getByText(/新建会话|创建新会话/).first()
    if (await cta.count()) await cta.click().catch(() => {})
    await page.waitForTimeout(800)
  }
  await shot(page, '08b-analysis-new-session.png')
  await closeDialog(page)

  // 09 SQL workspace
  await page.goto(`${BASE}/sql-workspace`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(800)
  await closeDialog(page)
  const sqlBox = page.locator('textarea').first()
  await sqlBox.fill(
    "SELECT DATE_FORMAT(`date`, '%Y-%m-%d') AS d, SUM(organic_traffic) AS organic\nFROM ds_seo_site_traffic_daily\nWHERE `date` >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)\nGROUP BY d\nORDER BY d;",
  )
  await shot(page, '09-sql-workspace-input.png')

  await page.getByRole('button', { name: /执行查询/ }).first().click()
  // wait for table rows or error banner
  await Promise.race([
    page.waitForSelector('.el-table__row, .el-alert, .error, .sql-error', { timeout: 45000 }).catch(() => null),
    page.waitForTimeout(45000),
  ])
  await shot(page, '09b-sql-workspace-result.png')

  // 10 Log diagnosis
  await page.goto(`${BASE}/log-diagnosis`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(800)
  await closeDialog(page)
  const paste = page.locator('textarea').first()
  await paste.fill(SAMPLE_LOG)
  await shot(page, '10-log-diagnosis-input.png')

  await page.getByRole('button', { name: /开始诊断/ }).first().click()
  await Promise.race([
    page.waitForSelector('.log-report, .diagnosis, h3:has-text("诊断报告")', { timeout: 90000 }).catch(() => null),
    page.waitForTimeout(90000),
  ])
  await shot(page, '10b-log-diagnosis-result.png')

  // 11 App shell (analysis with sidebar brand)
  await page.goto(`${BASE}/analysis`, { waitUntil: 'networkidle' })
  await page.waitForTimeout(800)
  await closeDialog(page)
  await shot(page, '11-app-shell.png')

  await browser.close()
  console.log('done')
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
