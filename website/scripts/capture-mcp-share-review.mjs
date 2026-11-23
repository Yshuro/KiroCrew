/**
 * Screenshot runner for capture/mcp-share-review.html (component render, mocked data).
 *
 * From website/, with the dev server up:
 *   npx vite --host 127.0.0.1 --port 6815 --strictPort
 *   node scripts/capture-mcp-share-review.mjs http://127.0.0.1:6815 OUT after
 *
 * The label names the source on disk (before: the base's two components,
 * after: this branch's); each scene is shot at a phone width.
 */
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const BASE = process.argv[2] || 'http://127.0.0.1:6815'
const OUT = process.argv[3] || '../temp-screenshots/mcp-share-review'
const LABEL = process.argv[4] || 'after'

mkdirSync(OUT, { recursive: true })
const browser = await chromium.launch()
let failed = 0
for (const scene of ['page', 'review', 'dialog', 'row']) {
  const ctx = await browser.newContext({ viewport: { width: 420, height: 900 }, deviceScaleFactor: 2 })
  const page = await ctx.newPage()
  const errors = []
  page.on('pageerror', e => errors.push(String(e)))
  try {
    await page.goto(`${BASE}/capture/mcp-share-review.html?scene=${scene}`, { waitUntil: 'networkidle' })
    await page.waitForSelector('[data-capture-root]', { timeout: 15000 })
    await page.waitForTimeout(500)
    await page.locator('[data-capture-root]').screenshot({ path: `${OUT}/${scene}-${LABEL}.png` })
    if (errors.length) throw new Error(errors.join('\n'))
    console.log(`ok ${scene}-${LABEL}.png`)
  } catch (e) {
    failed++
    console.error(`FAIL ${scene}: ${e}`)
  }
  await ctx.close()
}
await browser.close()
process.exit(failed ? 1 : 0)
