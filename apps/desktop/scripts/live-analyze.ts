/** Offline replay of the production detectors over a captured surface PNG. */
import { app, nativeImage } from 'electron'
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { PlatformSchema, deriveChartGrid, normalizedToPixel, type NormalizedBounds } from '@quant-screen-trader/shared-types'
import { chartSurfaceActivity, findAssetTabs } from '../electron/main/platform-browser'
import { canvasPriceGeometry, detectCanvasGrid } from '../electron/main/chart-grid'
import { isolateBrightPriceLabel, normalizeBitmap, type NormalizedImage } from '../electron/main/market-providers'
import { TesseractOCRProvider } from '../electron/main/market-ocr'
import { normalizeAsset } from '../electron/main/asset-detector'

const platform = PlatformSchema.parse(process.env.QST_LIVE_PLATFORM)
const source = process.env.QST_LIVE_IMAGE!
const out = process.env.QST_LIVE_OUT!
// The captured surface is in device pixels; the browser view it came from was this many DIP wide.
const browserWidth = Number(process.env.QST_LIVE_WIDTH ?? 1440)
const zoomFactor = Number(process.env.QST_LIVE_ZOOM ?? .7)

function pixels(bounds: NormalizedBounds, width: number, height: number): { x: number; y: number; width: number; height: number } {
  const r = normalizedToPixel(bounds, width, height)
  return { x: Math.max(0, Math.floor(r.x)), y: Math.max(0, Math.floor(r.y)),
    width: Math.max(1, Math.floor(r.width)), height: Math.max(1, Math.floor(r.height)) }
}

void app.whenReady().then(async () => {
  const native = nativeImage.createFromPath(source)
  const size = native.getSize()
  const image: NormalizedImage = normalizeBitmap(native.toBitmap(), size.width, size.height, undefined, false)
  const ocr = new TesseractOCRProvider()
  const summary: Record<string, unknown> = { size, activity: chartSurfaceActivity(image), tabs: findAssetTabs(image, platform) }
  try {
    const detection = detectCanvasGrid(image)
    summary.detection = detection
    const grid = deriveChartGrid(platform, detection.bounds, 'AUTO', detection.confidence)
    const cells: unknown[] = []
    for (const slot of grid.slots) {
      const geometry = canvasPriceGeometry(platform, slot.chartBounds, browserWidth, zoomFactor)
      const cellPx = pixels(slot.chartBounds, size.width, size.height)
      const title = { x: Math.floor(cellPx.x + cellPx.width * .12), y: Math.floor(cellPx.y + cellPx.height * .025),
        width: Math.max(1, Math.floor(cellPx.width * .5)), height: Math.max(1, Math.floor(cellPx.height * .085)) }
      const titleCrop = native.crop(title).resize({ width: title.width * 3, height: title.height * 3 })
      const titleSize = titleCrop.getSize(), titleBitmap = titleCrop.toBitmap()
      writeFileSync(join(out, `analyze-cell-${slot.slotId}-name.png`), titleCrop.toPNG())
      const titleVotes: string[] = []
      for (const threshold of [undefined, 125, 145, 165]) {
        const read = await ocr.parseText({ ...normalizeBitmap(titleBitmap, titleSize.width, titleSize.height, threshold, true), purpose: 'ASSET' })
        titleVotes.push(read.asset ? normalizeAsset(read.asset) ?? `raw:${read.rawText?.trim() ?? ''}` : `raw:${read.rawText?.trim() ?? ''}`)
      }
      const roi = pixels(geometry.priceBounds, size.width, size.height)
      // Match production: the ROI is resized to bounded pixel dimensions before isolation.
      const scale = Math.min(2, 1024 / roi.width, 1024 / roi.height)
      const resized = native.crop(roi).resize({ width: Math.max(1, Math.round(roi.width * scale)), height: Math.max(1, Math.round(roi.height * scale)) })
      const resizedSize = resized.getSize()
      try {
        const label = isolateBrightPriceLabel({ ...normalizeBitmap(resized.toBitmap(), resizedSize.width, resizedSize.height, undefined, false),
          pixelBounds: geometry.priceBounds })
        writeFileSync(join(out, `analyze-cell-${slot.slotId}-label.png`), nativeImage.createFromBitmap(
          Buffer.from(Uint8Array.from({ length: label.width * label.height * 4 },
            (_, i) => i % 4 === 3 ? 255 : label.grayscale[Math.floor(i / 4)]!)), { width: label.width, height: label.height }).toPNG())
        const parsed = await ocr.parseText(label)
        cells.push({ canvasSlotId: slot.slotId, roi, rawOCR: parsed.rawText?.trim(), price: parsed.price ?? null,
          confidence: parsed.confidence, labelBounds: label.pixelBounds, titleVotes })
      } catch (error) {
        cells.push({ canvasSlotId: slot.slotId, roi, titleVotes, error: error instanceof Error ? error.message : 'failed' })
      }
    }
    summary.cells = cells
    summary.tabAssets = (summary.tabs as { x: number; y: number; width: number; height: number }[]).map((tab, index) => {
      const name = { x: Math.floor(tab.x + tab.width * .28), y: Math.floor(tab.y + tab.height * .12),
        width: Math.max(1, Math.floor(tab.width * .7)), height: Math.max(1, Math.floor(tab.height * .43)) }
      return { tabIndex: index + 1, pixelBounds: tab, nameBounds: name }
    })
  } catch (error) { summary.detection = { error: error instanceof Error ? error.message : 'failed' } }
  await ocr.stop().catch(() => {})
  writeFileSync(join(out, 'analysis.json'), JSON.stringify(summary, null, 2))
  for (const cell of (summary.cells ?? []) as Record<string, unknown>[])
    console.log(`cell ${String(cell.canvasSlotId)}: ${JSON.stringify({ price: cell.price, title: cell.titleVotes, error: cell.error })}`)
  app.quit()
})
