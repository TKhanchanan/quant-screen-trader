import { z } from 'zod'
import { AssetDetectionResultSchema, type AssetDetectionResult, type CalibrationSlot, type DetectedAsset, type Platform } from '@quant-screen-trader/shared-types'

const Rect = z.strictObject({ x: z.number().min(0).max(1), y: z.number().min(0).max(1), width: z.number().positive().max(1), height: z.number().positive().max(1) })
export const ChartLabelSchema = z.strictObject({ bounds: Rect, label: z.string().max(120), tooltip: z.string().max(120).nullable() })
export type ChartLabel = z.infer<typeof ChartLabelSchema>
export function normalizeAsset(label: string): string | null {
  const value = label.trim().replace(/\s+/g, ' ').replace(/\s*\/\s*/g, '/').replace(/\s*\(OTC\)$/i, ' OTC')
  if (!/^[\p{L}][\p{L}\p{N} /&+()._-]{1,119}$/u.test(value) || /[.…]{2}|…|\?/.test(value) || /(?:balance|account|deposit|withdraw|password)/i.test(value)) return null
  return value
}
export function emptyAsset(platform: Platform, slotId: number, state: DetectedAsset['state'] = 'NOT_FOUND'): DetectedAsset {
  return { platform, slotId, state, assetName: null, displayName: null, canonicalAssetId: null,
    source: 'DOM', confidence: 0, detectedAt: new Date().toISOString(), evidenceType: 'NO_MAPPING' }
}
function fullLabel(chart: ChartLabel): { label: string; tooltip: boolean } | null {
  const visible = normalizeAsset(chart.label)
  if (visible) return { label: chart.label, tooltip: false }
  const prefix = chart.label.replace(/(?:\.{2,}|…).*/, '').trim()
  if (prefix.length >= 3 && chart.tooltip?.startsWith(prefix) && normalizeAsset(chart.tooltip)) return { label: chart.tooltip, tooltip: true }
  return null
}
export function mapChartLabels(platform: Platform, input: ChartLabel[], calibration?: CalibrationSlot[]): AssetDetectionResult {
  const charts = input.map(c => ChartLabelSchema.parse(c))
  const slots = Array.from({ length: 9 }, (_, i) => emptyAsset(platform, i + 1))
  const cluster = (axis: 'x' | 'y'): number[] => {
    const result: number[] = []
    for (const c of [...charts].sort((a, b) => a.bounds[axis] - b.bounds[axis])) {
      if (!result.some(v => Math.abs(v - c.bounds[axis]) < .04)) result.push(c.bounds[axis])
    }
    return result
  }
  const rows = cluster('y'), columns = cluster('x')
  const assigned = new Map<number, ChartLabel[]>()
  for (const chart of charts) {
    const b = chart.bounds
    let ids: number[]
    if (calibration) {
      // Map the chart center to exactly one calibrated slot. No tab-order inference.
      ids = calibration.filter(s => b.x + b.width / 2 >= s.bounds.x && b.x + b.width / 2 < s.bounds.x + s.bounds.width &&
        b.y + b.height / 2 >= s.bounds.y && b.y + b.height / 2 < s.bounds.y + s.bounds.height).map(s => s.id)
    } else {
      ids = charts.length === 9 && rows.length === 3 && columns.length === 3 ? [rows.findIndex(y => Math.abs(y - b.y) < .04) * 3 + columns.findIndex(x => Math.abs(x - b.x) < .04) + 1] : []
    }
    if (ids.length !== 1) continue
    const id = ids[0]!
    assigned.set(id, [...(assigned.get(id) ?? []), chart])
  }
  for (const [id, candidates] of assigned) {
    const label = candidates.length === 1 ? fullLabel(candidates[0]!) : null
    const assetName = label ? normalizeAsset(label.label) : null
    slots[id - 1] = assetName && label ? { ...emptyAsset(platform, id), state: 'DETECTED', assetName,
      displayName: label.label, canonicalAssetId: `${platform}:${assetName}`, confidence: .98,
      evidenceType: label.tooltip ? 'LABEL_TOOLTIP' : 'CHART_LABEL' } : emptyAsset(platform, id, 'UNCERTAIN')
  }
  return AssetDetectionResultSchema.parse({ platform, slots, overallConfidence: slots.reduce((n, s) => n + s.confidence, 0) / 9, durationMs: 0 })
}
export interface AssetDetector { detectAssets(calibration?: CalibrationSlot[]): Promise<AssetDetectionResult> }
const SELECTORS = {
  capitalbear: { charts: '.chart-container, .chart-wrapper, [data-testid="chart-container"]', labels: '.asset-name, .instrument-name, [data-testid="asset-name"]' },
  iqoption: { charts: '.chart-container, .chart-wrapper, [data-testid="chart-container"]', labels: '.asset-name, .instrument-name, [data-testid="asset-name"]' }
} as const
/** Runs inside the remote page. Only user-facing chart labels and their own tooltips leave it. */
function readChartLabels(selectors: { charts: string; labels: string }): ChartLabel[] {
  const visible = (e: Element): boolean => {
    const b = e.getBoundingClientRect(), style = getComputedStyle(e)
    return b.width > 0 && b.height > 0 && b.left >= 0 && b.top >= 0 && b.right <= innerWidth + 1 && b.bottom <= innerHeight + 1 &&
      style.display !== 'none' && style.visibility === 'visible' && Number(style.opacity) > 0 && !e.closest('form,input,textarea,[contenteditable],[role="tablist"],[role="tab"],[hidden]')
  }
  const candidates = Array.from(document.querySelectorAll(selectors.charts)).filter(c => visible(c) && c.querySelector('canvas,svg'))
  const charts = candidates.filter(c => !candidates.some(child => child !== c && c.contains(child)))
  return charts.slice(0, 18).flatMap(c => {
    const labels = Array.from(c.querySelectorAll(selectors.labels)).filter(visible)
    if (labels.length !== 1) return []
    const node = labels[0]!, b = c.getBoundingClientRect()
    const clean = (s: string): string => /^[\p{L}\p{N} /&+()._…-]{1,120}$/u.test(s.trim()) ? s.trim() : ''
    return [{ bounds: { x: b.x / innerWidth, y: b.y / innerHeight, width: Math.min(1, b.width / innerWidth), height: Math.min(1, b.height / innerHeight) },
      label: clean((node as HTMLElement).innerText ?? ''), tooltip: clean(node.getAttribute('title') ?? node.getAttribute('aria-label') ?? '') || null }]
  })
}
class DOMAssetDetector implements AssetDetector {
  constructor(private readonly platform: Platform, private readonly evaluate: (script: string) => Promise<unknown>) {}
  async detectAssets(calibration?: CalibrationSlot[]): Promise<AssetDetectionResult> {
    const start = Date.now()
    const data = await this.evaluate(`(${readChartLabels.toString()})(${JSON.stringify(SELECTORS[this.platform])})`)
    const result = mapChartLabels(this.platform, z.array(ChartLabelSchema).max(18).parse(data), calibration)
    return { ...result, durationMs: Date.now() - start }
  }
}
export class CapitalBearAssetDetector extends DOMAssetDetector { constructor(evaluate: (script: string) => Promise<unknown>) { super('capitalbear', evaluate) } }
export class IQOptionAssetDetector extends DOMAssetDetector { constructor(evaluate: (script: string) => Promise<unknown>) { super('iqoption', evaluate) } }
