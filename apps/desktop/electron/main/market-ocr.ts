import { dirname, join } from 'node:path'
import { createRequire } from 'node:module'
import { nativeImage } from 'electron'
import { createWorker, OEM, PSM, type Worker } from 'tesseract.js'
import { normalizeAsset } from './asset-detector'
import { parsePrice, parsePayout, parseTimer, type NormalizedImage, type OCRProvider, type ParsedFields } from './market-providers'

interface OCRLine { text: string; confidence: number; words: { text: string; confidence: number }[] }
function assetLineConfidence(asset: string, lines: OCRLine[]): number | null {
  const target = normalizeAsset(asset), matches: number[] = []
  for (const line of lines) {
    const words: { text: string; confidence: number }[] = []
    for (const word of line.words) {
      words.push(word)
      if (normalizeAsset(words.map(w => w.text).join(' ')) === target) {
        if (words.every(w => Number.isFinite(w.confidence) && w.confidence >= 0 && w.confidence <= 100))
          matches.push(words.reduce((sum, w) => sum + w.confidence, 0) / words.length / 100)
        break
      }
    }
    if (!line.words.length && normalizeAsset(line.text) === target && Number.isFinite(line.confidence) && line.confidence >= 0 && line.confidence <= 100)
      matches.push(line.confidence / 100)
  }
  return matches.length === 1 ? matches[0]! : null
}
export function parseOCRFields(text: string, confidence: number, layoutLines: OCRLine[] = []): ParsedFields {
  const lines = text.split(/\r?\n/).map(s => s.trim().replace(/\s+[vV]$/, '').trim()).filter(Boolean)
  const unique = (test: (s: string) => boolean): string | undefined => {
    const values = lines.filter(test)
    return values.length === 1 ? values[0] : undefined
  }
  const price = unique(s => parsePrice(s) !== null)
  const payout = unique(s => parsePayout(s) !== null)
  const timer = unique(s => parseTimer(s) !== null)
  const asset = unique(s => normalizeAsset(s) !== null)
  const assetConfidence = asset && !price && !payout && !timer ? assetLineConfidence(asset, layoutLines) : null
  return { rawText: text, confidence: assetConfidence ?? confidence, ...(price ? { price } : {}), ...(payout ? { payout } : {}),
    ...(timer ? { timer } : {}), ...(asset ? { asset } : {}) }
}
export class TesseractOCRProvider implements OCRProvider {
  private worker: Promise<Worker> | null = null
  private busy = false
  async parseText(image: NormalizedImage): Promise<ParsedFields> {
    // ponytail: one worker per platform; skip contention instead of queuing 9 images.
    if (this.busy) throw new Error('OCR busy')
    this.busy = true
    try {
      const localRequire = createRequire(join(process.cwd(), 'package.json'))
      this.worker ??= createWorker('eng', OEM.LSTM_ONLY, {
        langPath: join(dirname(localRequire.resolve('@tesseract.js-data/eng')), '4.0.0'),
        cacheMethod: 'none', gzip: true, logger: () => {}, errorHandler: () => {}
      }).catch(() => { this.worker = null; throw new Error('OCR unavailable') })
      const worker = await this.worker
      await worker.setParameters({ tessedit_pageseg_mode: image.purpose ? PSM.SINGLE_LINE : PSM.SPARSE_TEXT,
        tessedit_char_whitelist: image.purpose === 'PRICE' ? '0123456789.' : image.purpose === 'ASSET'
          ? 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 /&+()._-' : '' })
      const bitmap = Buffer.alloc(image.width * image.height * 4)
      image.grayscale.forEach((v, i) => { bitmap[i * 4] = v; bitmap[i * 4 + 1] = v; bitmap[i * 4 + 2] = v; bitmap[i * 4 + 3] = 255 })
      const png = image.png ? Buffer.from(image.png) : nativeImage.createFromBitmap(bitmap, { width: image.width, height: image.height }).toPNG()
      const result = await worker.recognize(png, {}, { blocks: true })
      const lines = result.data.blocks?.flatMap(block => block.paragraphs.flatMap(paragraph => paragraph.lines)) ?? []
      return parseOCRFields(result.data.text, result.data.confidence / 100, lines)
    } finally { this.busy = false }
  }
  async stop(): Promise<void> { const worker = this.worker; this.worker = null; if (worker) await (await worker).terminate() }
}
