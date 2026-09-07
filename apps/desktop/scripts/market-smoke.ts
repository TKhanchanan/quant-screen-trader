/** Account-free native capture + local OCR check. Run via scripts/market-smoke.mjs. */
import { app, BrowserWindow } from 'electron'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import assert from 'node:assert/strict'
import { TesseractOCRProvider } from '../electron/main/market-ocr'
import { normalizeBitmap } from '../electron/main/market-providers'

app.setPath('userData', mkdtempSync(join(tmpdir(), 'qst-ocr-smoke-')))
void app.whenReady().then(async () => {
  const window = new BrowserWindow({ width: 600, height: 500, show: true,
    webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false } })
  const ocr = new TesseractOCRProvider()
  try {
    await window.loadURL('data:text/html,' + encodeURIComponent('<body style="margin:30px;background:white;color:black;font:32px Arial"><p>EUR/USD OTC</p><p>1.23456</p><p>82%</p><p>00:59</p></body>'))
    const start = performance.now()
    const image = await window.webContents.capturePage({ x: 0, y: 0, width: 550, height: 420 })
    const captureMs = performance.now() - start
    const size = image.getSize()
    const normalized = normalizeBitmap(image.toBitmap(), size.width, size.height)
    const parseStart = performance.now()
    const result = await ocr.parseText(normalized)
    const coldParseMs = performance.now() - parseStart
    assert.equal(result.price, '1.23456'); assert.equal(result.asset, 'EUR/USD OTC')
    assert.equal(result.payout, '82%'); assert.equal(result.timer, '00:59')
    assert.ok(result.confidence >= .8)
    const warmStart = performance.now()
    await ocr.parseText(normalized)
    console.log(JSON.stringify({ fixture: 'synthetic UI', captureMs, coldParseMs, warmParseMs: performance.now() - warmStart, confidence: result.confidence }))
    await ocr.stop(); window.destroy(); app.quit()
  } catch {
    console.error('Native capture/OCR fixture check failed')
    await ocr.stop().catch(() => {}); app.exit(1)
  }
})
