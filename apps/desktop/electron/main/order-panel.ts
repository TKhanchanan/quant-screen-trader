import type { NormalizedBounds, Platform } from '@quant-screen-trader/shared-types'
import type { PixelBounds } from './platform-browser'

/**
 * Read-only order-panel vision. Every cell of the broker grid reserves a fixed CSS-width
 * strip that `canvasPriceGeometry` already subtracts from the chart ROI; the broker's own
 * entry controls live in that strip. This module measures where they are.
 *
 * It holds no WebContents, sends no input event and exposes nothing that can reach a broker.
 * Locating a control is not pressing it: whether anything is ever pressed is decided outside
 * this file, behind its own gate.
 */

/** Electron `NativeImage.toBitmap()` order: blue, green, red, alpha. */
export interface PanelImage { width: number; height: number; bgra: Uint8Array }

export type ControlColor = 'GREEN' | 'RED'

export interface ControlCandidate {
  color: ControlColor
  /** Pixel bounds inside the panel crop, not the browser surface. */
  bounds: PixelBounds
  /** Filled fraction of the bounding box. A real button is a solid slab, not a scattered glyph. */
  coverage: number
}

export interface OrderPanelReading {
  green: ControlCandidate | null
  red: ControlCandidate | null
  /** Which control sits higher in the panel. The direction each color means is a broker fact,
   *  established by calibration against the platform's own labels — never assumed here. */
  verticalOrder: 'GREEN_TOP' | 'RED_TOP' | null
  confidence: number
  reasons: string[]
}

const PANEL_CSS_WIDTH: Record<Platform, number> = { iqoption: 132, capitalbear: 132 }

/**
 * The strip `canvasPriceGeometry` excludes, expressed on its own. Kept as the exact complement
 * of the chart ROI so the two can never drift apart and start overlapping the candles.
 */
export function orderPanelBounds(platform: Platform, cell: NormalizedBounds,
  browserWidth: number, zoomFactor: number): NormalizedBounds {
  const panelWidth = PANEL_CSS_WIDTH[platform] * zoomFactor / browserWidth
  if (panelWidth <= 0 || panelWidth >= cell.width)
    throw new Error('ORDER_PANEL_GEOMETRY_UNCERTAIN: the reserved panel does not fit inside the cell.')
  return { x: cell.x + cell.width - panelWidth, y: cell.y, width: panelWidth, height: cell.height }
}

function classify(bgra: Uint8Array, index: number): ControlColor | null {
  const blue = bgra[index]!, green = bgra[index + 1]!, red = bgra[index + 2]!
  // A saturated slab, not a tinted background: the winning channel has to lead both others by a
  // wide margin. Broker greens and reds clear this easily; chart wicks and text antialiasing do not.
  if (green >= 70 && green - Math.max(red, blue) >= 40) return 'GREEN'
  if (red >= 70 && red - Math.max(green, blue) >= 40) return 'RED'
  return null
}

interface BlobSearch { best: ControlCandidate | null; bestCount: number; runnerUpCount: number }

function largestBlob(image: PanelImage, color: ControlColor): BlobSearch {
  const { width, height, bgra } = image
  const seen = new Uint8Array(width * height)
  let best: ControlCandidate | null = null, bestCount = 0, runnerUpCount = 0
  for (let start = 0; start < seen.length; start++) {
    if (seen[start] || classify(bgra, start * 4) !== color) continue
    const queue = [start]
    seen[start] = 1
    let cursor = 0, count = 0
    let minX = start % width, maxX = minX, minY = Math.floor(start / width), maxY = minY
    while (cursor < queue.length) {
      const point = queue[cursor++]!, x = point % width, y = Math.floor(point / width)
      count++
      minX = Math.min(minX, x); maxX = Math.max(maxX, x)
      minY = Math.min(minY, y); maxY = Math.max(maxY, y)
      const neighbours = [x > 0 ? point - 1 : -1, x < width - 1 ? point + 1 : -1,
        y > 0 ? point - width : -1, y < height - 1 ? point + width : -1]
      for (const next of neighbours) {
        if (next < 0 || seen[next] || classify(bgra, next * 4) !== color) continue
        seen[next] = 1; queue.push(next)
      }
    }
    if (count <= bestCount) { runnerUpCount = Math.max(runnerUpCount, count); continue }
    const bounds = { x: minX, y: minY, width: maxX - minX + 1, height: maxY - minY + 1 }
    best = { color, bounds, coverage: count / (bounds.width * bounds.height) }
    runnerUpCount = bestCount
    bestCount = count
  }
  return { best, bestCount, runnerUpCount }
}

/**
 * Locate the two entry controls in one cell's panel crop. Returns what it can prove and says why
 * when it cannot: an unreadable panel must stay unreadable rather than resolve to a plausible
 * rectangle, because the coordinate this produces is the coordinate something would later press.
 */
export function findOrderButtons(image: PanelImage): OrderPanelReading {
  if (image.width < 20 || image.height < 40 || image.bgra.length !== image.width * image.height * 4)
    throw new Error('ORDER_PANEL_GEOMETRY_UNCERTAIN: panel crop is too small to read.')
  const reasons: string[] = []
  const accept = (search: BlobSearch, label: string): ControlCandidate | null => {
    const candidate = search.best
    if (!candidate) { reasons.push(`${label}_NOT_FOUND`); return null }
    // A rival slab of the same color — a second chip, a filled candle body bleeding in — makes
    // "the button" a guess. The midpoint of the wrong one is still a real coordinate on screen.
    if (search.runnerUpCount >= search.bestCount * .6) { reasons.push(`${label}_AMBIGUOUS`); return null }
    // A button spans most of its panel, is a slab rather than an outline, and is neither a hairline
    // nor half the cell. Each rejection is named so a failed read is diagnosable from the report.
    if (candidate.bounds.width < image.width * .5) { reasons.push(`${label}_TOO_NARROW`); return null }
    if (candidate.bounds.height < image.height * .04) { reasons.push(`${label}_TOO_SHORT`); return null }
    if (candidate.bounds.height > image.height * .45) { reasons.push(`${label}_TOO_TALL`); return null }
    if (candidate.coverage < .55) { reasons.push(`${label}_NOT_SOLID`); return null }
    // A slab running into an edge of the crop is a slab whose real extent is outside it, so its
    // midpoint is the midpoint of the visible part, not of the button. Off by half of whatever was
    // cut — which is how a grid that drifts a few pixels per row makes the bottom row press air.
    const { x, y, width, height } = candidate.bounds
    if (x === 0 || y === 0 || x + width === image.width || y + height === image.height) {
      reasons.push(`${label}_CLIPPED`); return null
    }
    return candidate
  }
  const green = accept(largestBlob(image, 'GREEN'), 'GREEN')
  const red = accept(largestBlob(image, 'RED'), 'RED')
  const confidence = green && red
    ? Math.min(1, Math.min(green.coverage, red.coverage) *
      Math.min(1, Math.min(green.bounds.width, red.bounds.width) / (image.width * .8)))
    : 0
  return { green, red, confidence, reasons,
    verticalOrder: green && red ? (green.bounds.y < red.bounds.y ? 'GREEN_TOP' : 'RED_TOP') : null }
}

/** Where a press would land: the middle of a located control, in browser-surface pixels. */
export function controlCenter(candidate: ControlCandidate, panel: PixelBounds): { x: number; y: number } {
  return { x: panel.x + candidate.bounds.x + candidate.bounds.width / 2,
    y: panel.y + candidate.bounds.y + candidate.bounds.height / 2 }
}
