import { normalizeAsset } from './asset-detector'
import { randomUUID } from 'node:crypto'
import { MarketObservationSchema, type MarketObservation, type MarketSnapshot, type NormalizedBounds, type Platform, type SourceType } from '@quant-screen-trader/shared-types'

export interface ObservationContext {
  platform: Platform; slotId: number; assetName: string; contextId: string;
  calibrationProfileId: string | null; bounds: NormalizedBounds; priceBounds?: NormalizedBounds
  diagnostics?: NonNullable<MarketSnapshot['slots'][number]['diagnostics']>
}
export interface ParsedFields { rawText?: string; asset?: string; price?: string; payout?: string; timer?: string; confidence: number }
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
export interface NormalizedImage { width: number; height: number; grayscale: Uint8Array; purpose?: 'ASSET' | 'PRICE'; png?: Uint8Array; pixelBounds?: NormalizedBounds }
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
  const owner = new Int32Array(image.grayscale.length).fill(-1)
  const candidates: { x: number; y: number; width: number; height: number; id: number }[] = []
  for (let index = 0; index < owner.length; index++) {
    if (image.grayscale[index]! < 185 || owner[index]! >= 0) continue
    const id = candidates.length, queue = [index]
    owner[index] = id
    let cursor = 0, count = 0, minX = index % image.width, maxX = minX, minY = Math.floor(index / image.width), maxY = minY
    while (cursor < queue.length) {
      const point = queue[cursor++]!, x = point % image.width, y = Math.floor(point / image.width)
      count++; minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, y); maxY = Math.max(maxY, y)
      for (const neighbour of [point - 1, point + 1, point - image.width, point + image.width]) {
        if (neighbour >= 0 && neighbour < owner.length && image.grayscale[neighbour]! >= 185 && owner[neighbour]! < 0 &&
          Math.abs(neighbour % image.width - x) <= 1) { owner[neighbour] = id; queue.push(neighbour) }
      }
    }
    const width = maxX - minX + 1, height = maxY - minY + 1, fill = count / (width * height)
    if (width < image.width * .2 || width > image.width * .9 || height < image.height * .05 || height > image.height * .35 ||
      width / height < 2.2 || width / height > 9 || fill < .55) continue
    candidates.push({ x: minX, y: minY, width, height, id })
  }
  if (candidates.length !== 1) throw new Error('PRICE ROI: exactly one live price callout must be isolated')
  const best = candidates[0]!
  // A chart axis label drawn against the callout merges into the same bright region. The plate is
  // the solid part: keep only the rows carrying most of it, which drops the thin text hanging off
  // its edge, and rebuild each kept row from its own span so nothing outside the plate survives.
  // Width of the plate on each row, not its lit pixel count: the digits are unlit holes, so
  // counting lit pixels would cut the plate off at its own first line of text.
  const perRow = Array.from({ length: best.height }, (_, row) => {
    let first = -1, last = -1
    for (let column = 0; column < best.width; column++)
      if (owner[(best.y + row) * image.width + best.x + column] === best.id) {
        if (first < 0) first = column
        last = column
      }
    return { first, last, span: first < 0 ? 0 : last - first + 1 }
  })
  const peak = perRow.reduce((widest, row, index) => row.span > perRow[widest]!.span ? index : widest, 0)
  const solid = perRow[peak]!.span * .6
  let top = peak, bottom = peak
  while (top > 0 && perRow[top - 1]!.span >= solid) top--
  while (bottom < best.height - 1 && perRow[bottom + 1]!.span >= solid) bottom++
  const plateHeight = bottom - top + 1
  const padding = Math.max(2, Math.round(plateHeight * .12))
  const cropWidth = best.width + padding * 2, cropHeight = plateHeight + padding * 2
  let total = 0, totalCount = 0
  for (let row = top; row <= bottom; row++) for (let column = perRow[row]!.first; column <= perRow[row]!.last; column++) {
    const value = image.grayscale[(best.y + row) * image.width + best.x + column]!
    if (value >= 185) { total += value; totalCount++ }
  }
  const background = totalCount ? Math.round(total / totalCount) : 255
  const crop = new Uint8Array(cropWidth * cropHeight).fill(background)
  let min = background, max = background
  for (let row = top; row <= bottom; row++) for (let column = perRow[row]!.first; column <= perRow[row]!.last; column++) {
    const value = image.grayscale[(best.y + row) * image.width + best.x + column]!
    crop[(row - top + padding) * cropWidth + column + padding] = value
    min = Math.min(min, value); max = Math.max(max, value)
  }
  for (let index = 0; index < crop.length; index++)
    crop[index] = max > min ? Math.round((crop[index]! - min) * 255 / (max - min)) : crop[index]!
  // Broker callouts are small; OCR loses decimal separators below roughly 90 px of plate height.
  const factor = Math.max(1, Math.min(6, Math.round(90 / cropHeight)))
  const width = cropWidth * factor, height = cropHeight * factor
  const grayscale = factor === 1 ? crop : new Uint8Array(width * height)
  if (factor > 1) for (let row = 0; row < height; row++) for (let column = 0; column < width; column++) {
    const y = (row + .5) / factor - .5, x = (column + .5) / factor - .5
    const y0 = Math.max(0, Math.min(cropHeight - 1, Math.floor(y))), x0 = Math.max(0, Math.min(cropWidth - 1, Math.floor(x)))
    const y1 = Math.min(cropHeight - 1, y0 + 1), x1 = Math.min(cropWidth - 1, x0 + 1)
    const dy = Math.max(0, Math.min(1, y - y0)), dx = Math.max(0, Math.min(1, x - x0))
    grayscale[row * width + column] = Math.round(
      crop[y0 * cropWidth + x0]! * (1 - dx) * (1 - dy) + crop[y0 * cropWidth + x1]! * dx * (1 - dy) +
      crop[y1 * cropWidth + x0]! * (1 - dx) * dy + crop[y1 * cropWidth + x1]! * dx * dy)
  }
  const source = image.pixelBounds ?? { x: 0, y: 0, width: image.width, height: image.height }
  return { width, height, grayscale, purpose: 'PRICE', pixelBounds: {
    x: source.x + best.x / image.width * source.width, y: source.y + (best.y + top) / image.height * source.height,
    width: best.width / image.width * source.width, height: plateHeight / image.height * source.height } }
}
export class PriceStability {
  private readonly previous = new Map<string, { identity: string; price: number; bounds: NormalizedBounds; at: number }>()
  reset(): void { this.previous.clear() }
  accept(context: ObservationContext, price: number | null, confidence: number, bounds: NormalizedBounds, now: number): boolean {
    const key = `${context.platform}:${context.slotId}`, identity = `${context.contextId}:${context.assetName}`
    const previous = this.previous.get(key)
    if (price === null || confidence < .8) { this.previous.delete(key); return false }
    this.previous.set(key, { identity, price, bounds, at: now })
    return !!previous && previous.identity === identity && now - previous.at <= 6000 &&
      Math.abs(price - previous.price) / previous.price <= .025 &&
      Math.abs(bounds.x - previous.bounds.x) <= Math.max(2, bounds.width * .1) &&
      Math.abs(bounds.y - previous.bounds.y) <= bounds.height * 2 &&
      Math.abs(bounds.width - previous.bounds.width) <= Math.max(2, bounds.width * .15)
  }
}
export class VisualMarketDataProvider extends Provider {
  readonly sourceType = 'VISUAL' as const
  private readonly stability = new PriceStability()
  override stop(): void { super.stop(); this.stability.reset() }
  constructor(private readonly capture: (context: ObservationContext) => Promise<NormalizedImage>, private readonly ocr: OCRProvider) { super() }
  async observe(context: ObservationContext): Promise<MarketObservation> {
    if (!this.active) throw new Error('Provider stopped')
    const start = Date.now(), image = isolateBrightPriceLabel(await this.capture(context)), latency = Date.now() - start
    const fields = await this.ocr.parseText(image)
    const stable = this.stability.accept(context, parsePrice(fields.price), fields.confidence, image.pixelBounds!, start)
    if (context.diagnostics) Object.assign(context.diagnostics, { stage: stable ? 'READY' : 'OCR',
      message: stable ? undefined : 'Waiting for consecutive same-slot, same-region prices', rawPrice: fields.price ?? '',
      parsedPrice: parsePrice(fields.price), priceConfidence: fields.confidence, labelPixelBounds: image.pixelBounds })
    return observation(context, this.sourceType, { ...fields, confidence: stable ? fields.confidence : Math.min(.79, fields.confidence),
      ...(fields.price ? { asset: context.assetName } : {}) }, start, latency)
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
