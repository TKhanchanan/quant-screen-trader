/**
 * Read-only live geometry probe. Opens the real authenticated broker session in its own
 * persisted partition, captures the visible surface and runs the production detectors.
 * No input events, no clicks, no order or stake controls are ever touched.
 * Run through scripts/live-acceptance.mjs.
 */
import { app, BrowserWindow, WebContentsView, nativeImage } from 'electron'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { PlatformSchema, deriveChartGrid, normalizedToPixel, type NormalizedBounds } from '@quant-screen-trader/shared-types'
import { getPlatformConfig } from '../electron/platforms/config'
import { chartSurfaceActivity, findAssetTabs, type PixelBounds } from '../electron/main/platform-browser'
import { canvasPriceGeometry, detectCanvasGrid } from '../electron/main/chart-grid'
import { controlCenter, findOrderButtons, orderPanelBounds } from '../electron/main/order-panel'
import { isolateBrightPriceLabel, normalizeBitmap, type NormalizedImage } from '../electron/main/market-providers'
import { TesseractOCRProvider } from '../electron/main/market-ocr'
import { normalizeAsset } from '../electron/main/asset-detector'

const platform = PlatformSchema.parse(process.env.QST_LIVE_PLATFORM)
const out = process.env.QST_LIVE_OUT!
const userData = process.env.QST_LIVE_USER_DATA!
const settleMs = Number(process.env.QST_LIVE_SETTLE_MS ?? 15000)
const width = Number(process.env.QST_LIVE_WIDTH ?? 1440), height = Number(process.env.QST_LIVE_HEIGHT ?? 940)
const headerHeight = Number(process.env.QST_LIVE_HEADER ?? 0)
if (!out || !userData) throw new Error('Run through scripts/live-acceptance.mjs')
app.setPath('userData', userData)
app.setPath('sessionData', userData)
mkdirSync(out, { recursive: true })

const wait = (ms: number): Promise<void> => new Promise(resolve => setTimeout(resolve, ms))
const report: Record<string, unknown> = { platform, requestedZoom: 0.7 }
const log = (message: string): void => console.log(`[live] ${message}`)

function save(name: string, image: Electron.NativeImage): void {
  writeFileSync(join(out, `${name}.png`), image.toPNG())
}
function toImage(native: Electron.NativeImage): NormalizedImage {
  const size = native.getSize()
  return normalizeBitmap(native.toBitmap(), size.width, size.height, undefined, false)
}
function pixels(bounds: NormalizedBounds, surface: { width: number; height: number }): PixelBounds {
  const r = normalizedToPixel(bounds, surface.width, surface.height)
  return { x: Math.max(0, Math.floor(r.x)), y: Math.max(0, Math.floor(r.y)),
    width: Math.max(1, Math.floor(r.width)), height: Math.max(1, Math.floor(r.height)) }
}
function tabNameBounds(tab: PixelBounds): PixelBounds {
  return { x: Math.floor(tab.x + tab.width * .28), y: Math.floor(tab.y + tab.height * .12),
    width: Math.max(1, Math.floor(tab.width * .7)), height: Math.max(1, Math.floor(tab.height * .43)) }
}

void app.whenReady().then(async () => {
  const config = getPlatformConfig(platform)
  const window = new BrowserWindow({ width, height, show: true, title: `Live probe — ${config.displayName}`,
    backgroundColor: '#080d18' })
  const view = new WebContentsView({ webPreferences: { partition: config.sessionPartition,
    contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true,
    navigateOnDragDrop: false, spellcheck: false } })
  window.contentView.addChildView(view)
  const bounds = { x: 0, y: headerHeight, width, height: height - headerHeight }
  view.setBounds(bounds)
  view.setVisible(true)
  const contents = view.webContents
  contents.session.setPermissionRequestHandler((_c, _p, callback) => callback(false))
  contents.setZoomFactor(.7)
  contents.on('zoom-changed', () => contents.setZoomFactor(.7))
  contents.on('did-finish-load', () => contents.setZoomFactor(.7))
  const ocr = new TesseractOCRProvider()
  try {
    await contents.loadURL(config.startUrl)
    contents.setZoomFactor(.7)
    log(`loaded ${new URL(contents.getURL()).origin}; waiting up to ${settleMs} ms for the live canvas grid`)
    const deadline = Date.now() + settleMs
    for (let attempt = 0; Date.now() < deadline; attempt++) {
      await wait(3000)
      contents.setZoomFactor(.7)
      const probe = await contents.capturePage()
      if (probe.isEmpty()) continue
      const activity = chartSurfaceActivity(toImage(probe))
      const found = findAssetTabs(toImage(probe), platform).length
      log(`  settle ${attempt}: activity ${activity.middle.toFixed(2)} ready=${String(activity.ready)} tabs=${found}`)
      if (activity.ready && found >= 1) { await wait(3000); break }
    }
    contents.setZoomFactor(.7)
    report.zoomFactor = contents.getZoomFactor()
    report.currentOrigin = new URL(contents.getURL()).origin
    report.browserBounds = bounds

    const native = await contents.capturePage()
    if (native.isEmpty()) throw new Error('Empty capture')
    const size = native.getSize()
    report.captureSize = size
    save('surface', native)
    const image = toImage(native)

    // ---- Tabs -------------------------------------------------------------
    const tabs = findAssetTabs(image, platform)
    const again = findAssetTabs(toImage(await contents.capturePage()), platform)
    report.tabCount = tabs.length
    report.tabGeometryStable = JSON.stringify(tabs) === JSON.stringify(again)
    log(`findAssetTabs -> ${tabs.length} tabs (stable: ${String(report.tabGeometryStable)})`)
    const tabReport: unknown[] = []
    for (const [index, tab] of tabs.entries()) {
      const name = tabNameBounds(tab)
      const crop = native.crop(name).resize({ width: name.width * 4, height: name.height * 4 })
      save(`tab-${index + 1}`, crop)
      const cropSize = crop.getSize(), bitmap = crop.toBitmap()
      const variants = []
      for (const threshold of [undefined, 125, 145, 165])
        variants.push(await ocr.parseText({ ...normalizeBitmap(bitmap, cropSize.width, cropSize.height, threshold, true), purpose: 'ASSET' }))
      const votes = new Map<string, number>()
      for (const variant of variants) {
        const asset = variant.asset ? normalizeAsset(variant.asset) : null
        if (asset) votes.set(asset, (votes.get(asset) ?? 0) + 1)
      }
      const ranked = [...votes.entries()].sort((a, b) => b[1] - a[1])
      const agreed = ranked[0] && ranked[0][1] >= 2 && ranked[0][1] > (ranked[1]?.[1] ?? 0) ? ranked[0][0] : null
      tabReport.push({ tabIndex: index + 1, pixelBounds: tab, nameBounds: name,
        rawOCR: variants.map(v => v.rawText?.trim() ?? v.asset ?? ''), normalizedAsset: agreed,
        confidence: agreed ? Math.max(...variants.map(v => v.confidence)) : 0 })
      log(`  tab ${index + 1}: ${agreed ?? 'UNCERTAIN'} @ ${JSON.stringify(tab)}`)
    }
    report.tabs = tabReport

    // ---- Canvas grid ------------------------------------------------------
    try {
      const detection = detectCanvasGrid(image)
      const confirm = detectCanvasGrid(toImage(await contents.capturePage()))
      report.canvasGrid = { ...detection, stable: (['x', 'y', 'width', 'height'] as const)
        .every(key => Math.abs(detection.bounds[key] - confirm.bounds[key]) <= .002) }
      log(`detectCanvasGrid -> ${JSON.stringify(detection.bounds)} confidence ${detection.confidence.toFixed(3)}`)
      const grid = deriveChartGrid(platform, detection.bounds, 'AUTO', detection.confidence)
      const cells: unknown[] = []
      for (const slot of grid.slots) {
        const geometry = canvasPriceGeometry(platform, slot.chartBounds, bounds.width, contents.getZoomFactor())
        const chartPx = pixels(geometry.chartBounds, size), pricePx = pixels(geometry.priceBounds, size)
        const titlePx = pixels(slot.assetTitleBounds!, size)
        save(`cell-${slot.slotId}`, native.crop(chartPx))
        const titleCrop = native.crop(titlePx).resize({ width: titlePx.width * 3, height: titlePx.height * 3 })
        save(`cell-${slot.slotId}-title`, titleCrop)
        const titleSize = titleCrop.getSize()
        const title = await ocr.parseText({ ...normalizeBitmap(titleCrop.toBitmap(), titleSize.width, titleSize.height, undefined, true), purpose: 'ASSET' })
        const priceCrop = native.crop(pricePx)
        save(`cell-${slot.slotId}-price-roi`, priceCrop)
        let price: unknown = null
        try {
          const priceSize = priceCrop.getSize()
          const scale = Math.min(4, 1024 / pricePx.width, 1024 / pricePx.height)
          const resized = priceCrop.resize({ width: Math.round(pricePx.width * scale), height: Math.round(pricePx.height * scale) })
          const resizedSize = resized.getSize()
          const label = isolateBrightPriceLabel({ ...normalizeBitmap(resized.toBitmap(), resizedSize.width, resizedSize.height, undefined, false),
            pixelBounds: geometry.priceBounds })
          save(`cell-${slot.slotId}-price-label`, nativeImage.createFromBitmap(
            Buffer.from(Uint8Array.from({ length: label.width * label.height * 4 },
              (_, i) => i % 4 === 3 ? 255 : label.grayscale[Math.floor(i / 4)]!)),
            { width: label.width, height: label.height }))
          const parsed = await ocr.parseText(label)
          price = { rawOCR: parsed.rawText?.trim(), price: parsed.price ?? null, confidence: parsed.confidence,
            labelBounds: label.pixelBounds, sourceHeight: priceSize.height }
        } catch (error) { price = { error: error instanceof Error ? error.message : 'price isolation failed' } }
        // The reserved strip the chart ROI already excludes. Captured and measured only: the probe
        // reports where this broker draws its entry controls, and never sends an input event.
        const panelPx = pixels(orderPanelBounds(platform, slot.chartBounds, bounds.width, contents.getZoomFactor()), size)
        const panelCrop = native.crop(panelPx)
        save(`cell-${slot.slotId}-order-panel`, panelCrop)
        let controls: unknown
        try {
          const panelSize = panelCrop.getSize()
          const reading = findOrderButtons({ width: panelSize.width, height: panelSize.height, bgra: panelCrop.toBitmap() })
          controls = { ...reading,
            greenCenter: reading.green ? controlCenter(reading.green, panelPx) : null,
            redCenter: reading.red ? controlCenter(reading.red, panelPx) : null }
        } catch (error) { controls = { error: error instanceof Error ? error.message : 'panel read failed' } }
        cells.push({ canvasSlotId: slot.slotId, chartPixelBounds: chartPx, priceRoiPixelBounds: pricePx,
          orderPanelPixelBounds: panelPx, controls,
          titleOCR: title.rawText?.trim(), titleAsset: title.asset ? normalizeAsset(title.asset) : null, price })
        log(`  cell ${slot.slotId}: title="${title.rawText?.trim().replace(/\n/g, ' ') ?? ''}" price=${JSON.stringify(price)}`)
        log(`    order panel @ ${JSON.stringify(panelPx)} -> ${JSON.stringify(controls)}`)
      }
      report.cells = cells
    } catch (error) {
      report.canvasGrid = { error: error instanceof Error ? error.message : 'grid detection failed' }
      log(`detectCanvasGrid FAILED: ${String(report.canvasGrid)}`)
    }
  } catch (error) {
    report.fatal = error instanceof Error ? error.message : String(error)
    log(`FATAL: ${report.fatal as string}`)
  } finally {
    writeFileSync(join(out, 'report.json'), JSON.stringify(report, null, 2))
    await ocr.stop().catch(() => {})
    if (!window.isDestroyed()) window.destroy()
    app.quit()
  }
})
