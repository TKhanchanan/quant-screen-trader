import { describe, expect, it } from 'vitest'
import { controlCenter, findOrderButtons, orderPanelBounds, type PanelImage } from '../electron/main/order-panel'
import { canvasPriceGeometry } from '../electron/main/chart-grid'

const CELL = { x: .05, y: .1, width: .3, height: .25 }

function panel(options: { width?: number; height?: number; green?: [number, number] | null
  red?: [number, number] | null; inset?: number; hollow?: boolean } = {}): PanelImage {
  const width = options.width ?? 92, height = options.height ?? 300
  const bgra = new Uint8Array(width * height * 4)
  for (let i = 0; i < width * height; i++) { bgra[i * 4] = 24; bgra[i * 4 + 1] = 22; bgra[i * 4 + 2] = 20; bgra[i * 4 + 3] = 255 }
  const inset = options.inset ?? 6
  const slab = (band: [number, number], blue: number, green: number, red: number): void => {
    for (let y = band[0]; y < band[1]; y++) for (let x = inset; x < width - inset; x++) {
      if (options.hollow && x > inset + 2 && x < width - inset - 3 && y > band[0] + 2 && y < band[1] - 3) continue
      const i = (y * width + x) * 4
      bgra[i] = blue; bgra[i + 1] = green; bgra[i + 2] = red
    }
  }
  if (options.green !== null) slab(options.green ?? [30, 70], 90, 200, 40)
  if (options.red !== null) slab(options.red ?? [110, 150], 70, 45, 220)
  return { width, height, bgra }
}

describe('order panel geometry', () => {
  it('is the exact complement of the chart ROI, so the two never overlap', () => {
    const browserWidth = 1440, zoom = .7
    const chart = canvasPriceGeometry('capitalbear', CELL, browserWidth, zoom).chartBounds
    const strip = orderPanelBounds('capitalbear', CELL, browserWidth, zoom)
    expect(strip.x).toBeCloseTo(chart.x + chart.width, 6)
    expect(strip.x + strip.width).toBeCloseTo(CELL.x + CELL.width, 6)
    expect(strip.y).toBe(CELL.y)
    expect(strip.height).toBe(CELL.height)
  })
  it('refuses a cell the reserved panel cannot fit inside', () => {
    expect(() => orderPanelBounds('iqoption', { ...CELL, width: .02 }, 1440, .7))
      .toThrow('ORDER_PANEL_GEOMETRY_UNCERTAIN')
  })
})

describe('order control detection', () => {
  it('locates both controls and reports which sits on top', () => {
    const reading = findOrderButtons(panel())
    expect(reading.green?.bounds).toEqual({ x: 6, y: 30, width: 80, height: 40 })
    expect(reading.red?.bounds).toEqual({ x: 6, y: 110, width: 80, height: 40 })
    expect(reading.verticalOrder).toBe('GREEN_TOP')
    expect(reading.confidence).toBeGreaterThan(.9)
    expect(reading.reasons).toEqual([])
  })
  it('reads a broker that puts the red control first without assuming a direction', () => {
    const reading = findOrderButtons(panel({ green: [110, 150], red: [30, 70] }))
    expect(reading.verticalOrder).toBe('RED_TOP')
    expect(reading).not.toHaveProperty('direction')
  })
  it('reports a missing control instead of pairing the one it found', () => {
    const reading = findOrderButtons(panel({ red: null }))
    expect(reading.red).toBeNull()
    expect(reading.verticalOrder).toBeNull()
    expect(reading.confidence).toBe(0)
    expect(reading.reasons).toContain('RED_NOT_FOUND')
  })
  it('rejects an outline, a hairline and a slab that swallows the panel', () => {
    expect(findOrderButtons(panel({ hollow: true })).reasons).toContain('GREEN_NOT_SOLID')
    expect(findOrderButtons(panel({ green: [30, 33] })).reasons).toContain('GREEN_TOO_SHORT')
    expect(findOrderButtons(panel({ green: [10, 250], red: null })).reasons).toContain('GREEN_TOO_TALL')
  })
  it('rejects a narrow colored glyph such as a rising price tag', () => {
    const image = panel({ green: null }), width = image.width
    for (let y = 20; y < 40; y++) for (let x = 4; x < 20; x++) {
      const i = (y * width + x) * 4
      image.bgra[i] = 90; image.bgra[i + 1] = 200; image.bgra[i + 2] = 40
    }
    expect(findOrderButtons(image).reasons).toContain('GREEN_TOO_NARROW')
  })
  it('refuses to pick between two rival slabs of the same color', () => {
    const image = panel(), width = image.width
    for (let y = 200; y < 236; y++) for (let x = 6; x < width - 6; x++) {
      const i = (y * width + x) * 4
      image.bgra[i] = 90; image.bgra[i + 1] = 200; image.bgra[i + 2] = 40
    }
    const reading = findOrderButtons(image)
    expect(reading.green).toBeNull()
    expect(reading.confidence).toBe(0)
    expect(reading.reasons).toContain('GREEN_AMBIGUOUS')
    expect(reading.red).not.toBeNull()
  })
  it('refuses a control the crop cut off, rather than pressing its visible half', () => {
    // The bottom row's cell is a few pixels short, so the LOWER slab runs off the crop. Its
    // midpoint would sit above the real button's centre — the shape of a bottom-row misfire.
    const clipped = findOrderButtons(panel({ green: [30, 70], red: [260, 300] }))
    expect(clipped.red).toBeNull()
    expect(clipped.reasons).toContain('RED_CLIPPED')
    expect(clipped.confidence).toBe(0)
    // Fully inside the crop, the same pair reads normally.
    expect(findOrderButtons(panel({ green: [30, 70], red: [250, 290] })).red).not.toBeNull()
  })
  it('refuses a crop too small to hold a control', () => {
    expect(() => findOrderButtons({ width: 10, height: 10, bgra: new Uint8Array(400) }))
      .toThrow('ORDER_PANEL_GEOMETRY_UNCERTAIN')
  })
  it('places a press point at the middle of a located control, in surface pixels', () => {
    const reading = findOrderButtons(panel())
    expect(controlCenter(reading.green!, { x: 400, y: 200, width: 92, height: 300 })).toEqual({ x: 446, y: 250 })
  })
})
