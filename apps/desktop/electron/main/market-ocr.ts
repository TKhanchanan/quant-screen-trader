import { dirname, join } from 'node:path'
import { createRequire } from 'node:module'
import { nativeImage } from 'electron'
import { createWorker, OEM, PSM, type Worker } from 'tesseract.js'
import { parsePrice, parsePayout, parseTimer, type NormalizedImage, type OCRProvider, type ParsedFields } from './market-providers'

export function parseOCRFields(text: string, confidence: number): ParsedFields {
  const lines = text.split(/\r?\n/).map(s => s.trim()).filter(Boolean)
  const unique = (test: (s: string) => boolean): string | undefined => {
    const values = lines.filter(test)
    return values.length === 1 ? values[0] : undefined
  }
  const price = unique(s => parsePrice(s) !== null)
  const payout = unique(s => parsePayout(s) !== null)
  const timer = unique(s => parseTimer(s) !== null)
  const asset = unique(s => /^[A-Z][A-Z0-9 /.-]{2,40}(?: OTC)?$/.test(s))
  return { confidence, ...(price ? { price } : {}), ...(payout ? { payout } : {}),
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
      await worker.setParameters({ tessedit_pageseg_mode: PSM.SPARSE_TEXT })
      const bitmap = Buffer.alloc(image.width * image.height * 4)
      image.grayscale.forEach((v, i) => { bitmap[i * 4] = v; bitmap[i * 4 + 1] = v; bitmap[i * 4 + 2] = v; bitmap[i * 4 + 3] = 255 })
      const png = nativeImage.createFromBitmap(bitmap, { width: image.width, height: image.height }).toPNG()
      const result = await worker.recognize(png)
      return parseOCRFields(result.data.text, result.data.confidence / 100)
    } finally { this.busy = false }
  }
  async stop(): Promise<void> { const worker = this.worker; this.worker = null; if (worker) await (await worker).terminate() }
}
