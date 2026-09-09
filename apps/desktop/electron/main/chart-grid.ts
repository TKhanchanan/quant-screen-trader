import { deriveChartGrid, type ChartGridGeometry, type NormalizedBounds, type Platform } from '@quant-screen-trader/shared-types'
import type { NormalizedImage } from './market-providers'

export interface CanvasGridDetection { bounds: NormalizedBounds; columns: 3; rows: 3; confidence: number }

/** Both observed broker adapters reserve a fixed CSS-width order panel in EACH cell. */
export function canvasPriceGeometry(platform: Platform, cell: NormalizedBounds, browserWidth: number, zoomFactor: number): {
  chartBounds: NormalizedBounds; priceBounds: NormalizedBounds
} {
  const panelWidth = ({ iqoption: 132, capitalbear: 132 } as const)[platform] * zoomFactor / browserWidth
  const searchWidth = 120 * zoomFactor / browserWidth
  const chartBounds = { ...cell, width: cell.width - panelWidth }
  if (chartBounds.width < searchWidth * 2) throw new Error('CANVAS_GEOMETRY_UNCERTAIN: chart is too narrow for an isolated price ROI.')
  return { chartBounds, priceBounds: { x: chartBounds.x + chartBounds.width - searchWidth,
    y: cell.y + cell.height * .14, width: searchWidth, height: cell.height * .74 } }
}

/** Long visible edges are candidates only; all four boundaries and every cell must agree. */
export function detectCanvasGrid(image: NormalizedImage): CanvasGridDetection {
  const { width, height, grayscale } = image
  if (width < 600 || height < 360) throw new Error('CANVAS_GEOMETRY_UNCERTAIN: browser surface is too small. Calibrate Chart Area.')
  const edge = (x: number, y: number, vertical: boolean): boolean => {
    const offset = vertical ? 1 : width, point = y * width + x
    return Math.abs(grayscale[point + offset]! - grayscale[point - offset]!) >= 5
  }
  // A separator only spans its own grid, so support is measured across the band the grid
  // actually occupies. Scanning a fixed fraction of the surface makes every column fail
  // whenever a broker panel (an expanded portfolio, a footer) sits outside the charts.
  const lines = (vertical: boolean, from: number, to: number): number[] => {
    const size = vertical ? width : height
    const found: { position: number; support: number }[] = []
    for (let position = Math.ceil(size * (vertical ? .025 : .05)); position < size - 2; position++) {
      let hits = 0, total = 0
      for (let other = Math.max(1, Math.ceil(from)); other < to; other += 2) {
        if (edge(vertical ? position : other, vertical ? other : position, vertical)) hits++
        total++
      }
      if (total && hits / total >= .7) found.push({ position, support: hits / total })
    }
    const groups: (typeof found)[] = []
    for (const candidate of found) {
      const last = groups.at(-1)
      if (last && candidate.position - last.at(-1)!.position <= Math.max(4, width * .004)) last.push(candidate)
      else groups.push([candidate])
    }
    const positions = groups.map(group => Math.round((group[0]!.position + group.at(-1)!.position) / 2))
    if (vertical && !positions.some(p => p >= width - 4)) positions.push(width - 1)
    return positions
  }
  // The charts span nearly the full width, but only part of the height once the broker keeps a
  // panel below them, so a row triple is allowed to cover much less of the surface than a column one.
  const triples = (positions: number[], size: number, minimumSpan: number): number[][] => {
    const result: number[][] = []
    for (const start of positions) for (const end of positions) {
      if (end - start < size * minimumSpan) continue
      const step = (end - start) / 3
      const inner = [1, 2].map(i => positions.filter(p => Math.abs(p - start - step * i) <= Math.max(2, size * .003)))
      if (inner.every(matches => matches.length === 1)) result.push([start, inner[0]![0]!, inner[1]![0]!, end])
    }
    return result
  }
  const candidates: CanvasGridDetection[] = []
  // Rows first: they span the chart area horizontally and bound the vertical scan for columns.
  for (const ys of triples(lines(false, width * .08, width * .92), height, .45))
    for (const xs of triples(lines(true, ys[0]! + 2, ys[3]! - 2), width, .6)) {
      let minimumSupport = 1, valid = true
      for (const vertical of [true, false]) for (const [index, position] of (vertical ? xs : ys).entries()) {
        let hits = 0, total = 0
        for (let other = (vertical ? ys[0]! : xs[0]!) + 2; other < (vertical ? ys[3]! : xs[3]!) - 2; other++) {
          const radius = Math.ceil(width * .004)
          if (vertical && position >= width - 2 || Array.from({ length: radius * 2 + 1 }, (_, i) => i - radius).some(offset => {
            const x = vertical ? position + offset : other, y = vertical ? other : position + offset
            return x > 0 && x < width - 1 && y > 0 && y < height - 1 && edge(x, y, vertical)
          })) hits++
          total++
        }
        // An outer boundary separates the charts from different UI and must be crisp along its
        // whole length. An interior gutter is only spacing: it disappears wherever the two cells
        // it separates happen to share a background, so it is held to a lower bar.
        if (hits / total < (index === 0 || index === 3 ? .9 : .6)) valid = false
        minimumSupport = Math.min(minimumSupport, hits / total)
      }
      if (!valid) continue
      for (let row = 0; row < 3; row++) for (let col = 0; col < 3; col++) {
        let activity = 0, total = 0
        for (let y = ys[row]! + 8; y < ys[row + 1]! - 8; y += 2) for (let x = xs[col]! + 8; x < xs[col + 1]! - 8; x += 2) {
          if (edge(x, y, true) || edge(x, y, false)) activity++
          total++
        }
        if (!total || activity / total < .015) valid = false
      }
      if (valid) candidates.push({ columns: 3, rows: 3, confidence: .9 + .09 * minimumSupport,
        bounds: { x: xs[0]! / width, y: ys[0]! / height, width: (xs[3]! - xs[0]!) / width, height: (ys[3]! - ys[0]!) / height } })
    }
  if (candidates.length !== 1) throw new Error('CANVAS_GEOMETRY_UNCERTAIN: visible 3×3 separators are ambiguous. Use Calibrate Chart Area.')
  return candidates[0]!
}

export class ManualChartGridResolver {
  constructor(private readonly platform: Platform) {}
  resolve(bounds: NormalizedBounds): ChartGridGeometry { return deriveChartGrid(this.platform, bounds, 'MANUAL') }
}
export class CapitalBearChartGridResolver {
  readonly platform = 'capitalbear' as const
  resolve(image: NormalizedImage): ChartGridGeometry {
    const detection = detectCanvasGrid(image)
    return deriveChartGrid(this.platform, detection.bounds, 'AUTO', detection.confidence)
  }
}
export class IQOptionChartGridResolver {
  readonly platform = 'iqoption' as const
  resolve(image: NormalizedImage): ChartGridGeometry {
    const detection = detectCanvasGrid(image)
    return deriveChartGrid(this.platform, detection.bounds, 'AUTO', detection.confidence)
  }
}
export function chartGridResolver(platform: Platform): CapitalBearChartGridResolver | IQOptionChartGridResolver {
  return platform === 'capitalbear' ? new CapitalBearChartGridResolver() : new IQOptionChartGridResolver()
}
