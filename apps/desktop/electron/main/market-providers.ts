import { normalizeAsset } from './asset-detector'
import { randomUUID } from 'node:crypto'
import { MarketObservationSchema, type MarketObservation, type NormalizedBounds, type Platform, type SourceType } from '@quant-screen-trader/shared-types'

export interface ObservationContext {
  platform: Platform; slotId: number; assetName: string; contextId: string;
  calibrationProfileId: string | null; bounds: NormalizedBounds; priceBounds?: NormalizedBounds
}
export interface ParsedFields { asset?: string; price?: string; payout?: string; timer?: string; confidence: number }
export interface MarketDataProvider {
  readonly sourceType: SourceType
  start(): void
  stop(): void
  observe(context: ObservationContext): Promise<MarketObservation>
  health(): { available: boolean; version: string }
}
export function parsePrice(text: string | undefined): number | null {
  if (!text || !/^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(text.trim())) return null
  const value = Number(text.trim())
  return Number.isFinite(value) && value > 0 ? value : null
}
export function parsePayout(text: string | undefined): number | null {
  if (!text || !/^(?:100|\d{1,2})(?:\.\d+)?%$/.test(text.trim())) return null
  const value = Number(text.trim().slice(0, -1)) / 100
  return value <= 1 ? value : null
}
export function parseTimer(text: string | undefined): number | null {
  if (!text || !/^\d{1,3}:[0-5]\d$/.test(text.trim())) return null
  const [minutes = 0, seconds = 0] = text.trim().split(':').map(Number)
  return minutes * 60 + seconds
}
export function observation(context: ObservationContext, sourceType: SourceType, fields: ParsedFields,
  observedAt: number, captureLatencyMs = 0): MarketObservation {
  const parsedAt = Date.now(), parseLatencyMs = Math.max(0, parsedAt - observedAt - captureLatencyMs)
  const matched = !!fields.asset && normalizeAsset(fields.asset) === normalizeAsset(context.assetName)
  const confidence = matched ? fields.confidence : 0
  const price = parsePrice(fields.price), payout = parsePayout(fields.payout), timerSeconds = parseTimer(fields.timer)
  const latencyMs = Math.max(0, parsedAt - observedAt)
  const state = latencyMs > 3000 ? 'STALE' : price === null ? 'INVALID' : confidence < .8 ? 'UNCERTAIN' : 'GOOD'
  return MarketObservationSchema.parse({ id: randomUUID(), platform: context.platform, slotId: context.slotId,
    assetName: context.assetName, contextId: context.contextId, calibrationProfileId: context.calibrationProfileId,
    sourceType, observedAt: new Date(observedAt).toISOString(), parsedAt: new Date(parsedAt).toISOString(),
    price, payout, timerSeconds, parserConfidence: confidence, captureLatencyMs, parseLatencyMs,
    parserVersion: 'market-ui/1', dataQuality: { state, confidence,
      freshness: Math.max(0, 1 - latencyMs / 3000), completeness: [price, payout, timerSeconds].filter(v => v !== null).length / 3,
      sourceReliability: sourceType === 'VISUAL' ? .8 : 1, latencyMs } })
}
abstract class Provider implements MarketDataProvider {
  abstract readonly sourceType: SourceType
  protected active = false
  start(): void { this.active = true }
  stop(): void { this.active = false }
  health(): { available: boolean; version: string } { return { available: this.active, version: 'market-ui/1' } }
  abstract observe(context: ObservationContext): Promise<MarketObservation>
}
export class DOMMarketDataProvider extends Provider {
  readonly sourceType = 'DOM' as const
  constructor(private readonly read: (context: ObservationContext) => Promise<ParsedFields>) { super() }
  async observe(context: ObservationContext): Promise<MarketObservation> {
    if (!this.active) throw new Error('Provider stopped')
    const start = Date.now(), fields = await this.read(context)
    return observation(context, this.sourceType, fields, start, Date.now() - start)
  }
}
export interface NormalizedImage { width: number; height: number; grayscale: Uint8Array; purpose?: 'ASSET' | 'PRICE'; png?: Uint8Array }
export interface OCRProvider { parseText(image: NormalizedImage): Promise<ParsedFields> }
export function normalizeBitmap(bitmap: Uint8Array, width: number, height: number, threshold?: number, stretch = true): NormalizedImage {
  if (width < 1 || height < 1 || bitmap.length !== width * height * 4) throw new Error('Invalid bitmap')
  const grayscale = new Uint8Array(width * height)
  let min = 255, max = 0
  for (let i = 0; i < grayscale.length; i++) {
    const gray = Math.round(.114 * bitmap[i * 4]! + .587 * bitmap[i * 4 + 1]! + .299 * bitmap[i * 4 + 2]!)
    grayscale[i] = gray; min = Math.min(min, gray); max = Math.max(max, gray)
  }
  for (let i = 0; i < grayscale.length; i++) {
    const value = stretch && max > min ? Math.round((grayscale[i]! - min) * 255 / (max - min)) : grayscale[i]!
    grayscale[i] = threshold === undefined ? value : value >= threshold ? 255 : 0
  }
  return { width, height, grayscale }
}
export function isolateBrightPriceLabel(image: NormalizedImage): NormalizedImage {
  const mask = new Uint8Array(image.grayscale.length), seen = new Uint8Array(mask.length)
  for (let index = 0; index < mask.length; index++) if (image.grayscale[index]! >= 185) mask[index] = 1
  let best: { x: number; y: number; width: number; height: number; count: number } | null = null
  for (let index = 0; index < mask.length; index++) {
    if (!mask[index] || seen[index]) continue
    const queue = [index]; seen[index] = 1
    let cursor = 0, count = 0, minX = index % image.width, maxX = minX, minY = Math.floor(index / image.width), maxY = minY
    while (cursor < queue.length) {
      const point = queue[cursor++]!, x = point % image.width, y = Math.floor(point / image.width)
      count++; minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, y); maxY = Math.max(maxY, y)
      for (const neighbour of [point - 1, point + 1, point - image.width, point + image.width]) {
        if (neighbour >= 0 && neighbour < mask.length && mask[neighbour] && !seen[neighbour] &&
          Math.abs(neighbour % image.width - x) <= 1) { seen[neighbour] = 1; queue.push(neighbour) }
      }
    }
    const width = maxX - minX + 1, height = maxY - minY + 1, fill = count / (width * height)
    if (width < image.width * .2 || width > image.width * .9 || height < image.height * .05 || height > image.height * .35 ||
      width / height < 2.2 || width / height > 9 || fill < .55) continue
    if (!best || count > best.count) best = { x: minX, y: minY, width, height, count }
  }
  if (!best) throw new Error('Live price callout was not isolated')
  const padding = Math.max(2, Math.round(best.height * .12)), x = Math.max(0, best.x - padding), y = Math.max(0, best.y - padding)
  const width = Math.min(image.width - x, best.width + padding * 2), height = Math.min(image.height - y, best.height + padding * 2)
  const grayscale = new Uint8Array(width * height)
  let min = 255, max = 0
  for (let row = 0; row < height; row++) for (let column = 0; column < width; column++) {
    const value = image.grayscale[(y + row) * image.width + x + column]!
    grayscale[row * width + column] = value; min = Math.min(min, value); max = Math.max(max, value)
  }
  for (let index = 0; index < grayscale.length; index++)
    grayscale[index] = max > min ? Math.round((grayscale[index]! - min) * 255 / (max - min)) : grayscale[index]!
  return { width, height, grayscale, purpose: 'PRICE' }
}
export class VisualMarketDataProvider extends Provider {
  readonly sourceType = 'VISUAL' as const
  constructor(private readonly capture: (context: ObservationContext) => Promise<NormalizedImage>, private readonly ocr: OCRProvider) { super() }
  async observe(context: ObservationContext): Promise<MarketObservation> {
    if (!this.active) throw new Error('Provider stopped')
    const start = Date.now(), image = isolateBrightPriceLabel(await this.capture(context)), latency = Date.now() - start
    const fields = await this.ocr.parseText(image)
    return observation(context, this.sourceType, { ...fields, ...(fields.price ? { asset: context.assetName } : {}) }, start, latency)
  }
}
export class ReplayMarketDataProvider extends Provider {
  readonly sourceType: SourceType = 'REPLAY'
  private cursor = 0
  private readonly records: MarketObservation[]
  constructor(records: MarketObservation[]) { super(); this.records = records.map(r => MarketObservationSchema.parse(r)) }
  async observe(context: ObservationContext): Promise<MarketObservation> {
    if (!this.active) throw new Error('Provider stopped')
    const record = this.records[this.cursor++]
    if (!record) throw new Error('Replay exhausted')
    if (record.platform !== context.platform || record.slotId !== context.slotId || record.assetName !== context.assetName)
      throw new Error('Replay context mismatch')
    return { ...record, sourceType: this.sourceType, contextId: context.contextId }
  }
}
export class SyntheticMarketDataProvider extends ReplayMarketDataProvider {
  override readonly sourceType = 'SYNTHETIC' as const
}

/** Sanitized deterministic input: no wall-clock timestamps or live provenance. */
export function syntheticFixtures(context: ObservationContext, prices: number[], start: number, intervalMs: number): MarketObservation[] {
  if (intervalMs < 1 || !Number.isFinite(start)) throw new Error('Invalid fixture clock')
  return prices.map((price, index) => MarketObservationSchema.parse({
    ...observation(context, 'SYNTHETIC', { asset: context.assetName, price: String(price), confidence: 1 }, Date.now()),
    id: `00000000-0000-4000-8000-${index.toString(16).padStart(12, '0')}`,
    observedAt: new Date(start + index * intervalMs).toISOString(), parsedAt: new Date(start + index * intervalMs).toISOString(),
    captureLatencyMs: 0, parseLatencyMs: 0,
    dataQuality: { state: 'GOOD', confidence: 1, freshness: 1, completeness: 1 / 3, sourceReliability: 1, latencyMs: 0 }
  }))
}
