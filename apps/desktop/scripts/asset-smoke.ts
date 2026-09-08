/** Real rendered-DOM adapter check using only generated instruments and a temporary profile. */
import { app, BrowserWindow } from 'electron'
import assert from 'node:assert/strict'
import { CapitalBearAssetDetector, IQOptionAssetDetector } from '../electron/main/asset-detector'
const profile = process.env.QST_SMOKE_DATA_DIR
if (!profile) throw new Error('Run scripts/market-smoke.mjs --assets')
app.setPath('userData', profile)
void app.whenReady().then(async () => {
  const window = new BrowserWindow({ width: 1000, height: 850, show: true,
    webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false } })
  try {
    const charts = Array.from({ length: 9 }, (_, i) => `<section class="chart-container"><span class="asset-name" ${i === 0 ? 'title="Injective (OTC)"' : ''}>${i === 0 ? 'Injective (OT...' : `Fixture ${i + 1} (OTC)`}</span><canvas width="100" height="100"></canvas></section>`).join('')
    await window.loadURL('data:text/html,' + encodeURIComponent(`<style>body{margin:0}main{margin:60px 20px;display:grid;grid-template-columns:repeat(3,300px);gap:0}.chart-container{height:180px}.asset-name{display:block}canvas{display:block}</style><nav role="tablist"><span class="asset-name">Wrong tab order</span></nav><main>${charts}</main>`))
    for (const Adapter of [CapitalBearAssetDetector, IQOptionAssetDetector]) {
      const start = performance.now()
      const result = await new Adapter(script => window.webContents.executeJavaScript(script)).detectAssets()
      assert.equal(result.slots.filter(s => s.state === 'DETECTED').length, 9)
      assert.equal(result.slots[0]?.assetName, 'Injective OTC')
      assert.equal(result.slots[8]?.assetName, 'Fixture 9 OTC')
      assert.equal(result.slots[0]?.evidenceType, 'LABEL_TOOLTIP')
      console.log(JSON.stringify({ platform: result.platform, fixture: 'generated DOM charts', detected: 9, durationMs: performance.now() - start }))
    }
    window.destroy(); app.quit()
  } catch (error) {
    console.error(error instanceof Error ? error.message : 'DOM fixture failed'); app.exit(1)
  }
})
