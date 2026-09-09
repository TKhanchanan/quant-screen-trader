import { describe, expect, it } from 'vitest'
import { calibrationToChartGrid, defaultCalibration, deriveChartGrid, normalizedToPixel } from '@quant-screen-trader/shared-types'
import { canvasPriceGeometry, detectCanvasGrid, IQOptionChartGridResolver, ManualChartGridResolver } from '../electron/main/chart-grid'

function gridImage(scale = 1) {
  const width = 1200 * scale, height = 800 * scale, grayscale = new Uint8Array(width * height).fill(28)
  for (let y = 80 * scale; y < 680 * scale; y++) for (let x = 60 * scale; x < 1140 * scale; x++) {
    const gutter = (x - 60 * scale) % (360 * scale) < 4 * scale || (y - 80 * scale) % (200 * scale) < 4 * scale
    grayscale[y * width + x] = gutter ? 16 : (x + y) % (25 * scale) < 2 * scale ? 110 : 48
  }
  return { width, height, grayscale }
}
describe('chart grid geometry', () => {
  it('requires actual separator and cell evidence, rather than browser dimensions', () => {
    expect(() => detectCanvasGrid({ width: 1200, height: 800, grayscale: new Uint8Array(1200 * 800) })).toThrow('CANVAS_GEOMETRY_UNCERTAIN')
    const grid = new IQOptionChartGridResolver().resolve(gridImage())
    expect(grid.confidence).toBeGreaterThanOrEqual(.95)
    expect(grid.bounds.x).toBeCloseTo(.05, 2)
    expect(grid.bounds.y).toBeCloseTo(.1, 2)
    expect(grid.bounds.width).toBeCloseTo(.9, 2)
    expect(grid.bounds.height).toBeCloseTo(.75, 2)
    expect(grid.bounds).not.toEqual({ x: 0, y: 0, width: 1, height: 1 })
    expect(grid.slots.map(s => s.slotId)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9])
    expect(grid.slots[3]!.chartBounds.x).toBe(grid.slots[0]!.chartBounds.x)
  })
  it('finds the grid when a broker panel fills the surface below the charts', () => {
    // The charts occupy the upper two thirds; an expanded portfolio panel fills the rest. Column
    // separators do not reach into that panel, so support must be measured over the grid's own band.
    const width = 1200, height = 1000, grayscale = new Uint8Array(width * height).fill(28)
    for (let y = 80; y < 680; y++) for (let x = 60; x < 1140; x++) {
      const gutter = (x - 60) % 360 < 4 || (y - 80) % 200 < 4
      grayscale[y * width + x] = gutter ? 16 : (x + y) % 25 < 2 ? 110 : 48
    }
    const detection = detectCanvasGrid({ width, height, grayscale })
    expect(detection.bounds.y).toBeCloseTo(.08, 2)
    expect(detection.bounds.height).toBeCloseTo(.6, 2)
    expect(detection.confidence).toBeGreaterThanOrEqual(.95)
  })
  it('resolves equivalent normalized grid geometry after resize', () => {
    const small = detectCanvasGrid(gridImage()), large = detectCanvasGrid(gridImage(2))
    for (const key of ['x', 'y', 'width', 'height'] as const) expect(small.bounds[key]).toBeCloseTo(large.bounds[key], 2)
    expect(normalizedToPixel(small.bounds, 2400, 1600).width).toBeCloseTo(small.bounds.width * 2400)
  })
  it.each(['capitalbear', 'iqoption'] as const)('excludes each %s order panel and searches only the right-side canvas callout', platform => {
    const cell = { x: .1, y: .2, width: .3, height: .2 }
    const { chartBounds, priceBounds } = canvasPriceGeometry(platform, cell, 1500, .7)
    expect(priceBounds.x).toBeGreaterThan(chartBounds.x + chartBounds.width * .7)
    expect(priceBounds.x + priceBounds.width).toBeCloseTo(chartBounds.x + chartBounds.width)
    expect(priceBounds.x + priceBounds.width).toBeLessThan(cell.x + cell.width - .05)
    expect(priceBounds.y).toBeGreaterThan(cell.y)
    expect(priceBounds.y + priceBounds.height).toBeLessThan(cell.y + cell.height)
  })
  it('derives nine row-major cells from one manual outer rectangle', () => {
    const grid = new ManualChartGridResolver('capitalbear').resolve({ x: .1, y: .2, width: .81, height: .6 })
    expect(grid.slots).toHaveLength(9)
    expect(grid.slots[8]!.chartBounds).toMatchObject({ x: .64, y: .6, width: .27 })
    expect(grid.slots[8]!.chartBounds.height).toBeCloseTo(.2)
    expect(() => deriveChartGrid('iqoption', { x: .5, y: .5, width: .6, height: .2 }, 'MANUAL')).toThrow()
  })
  it('keeps unverified defaults at zero confidence and retains manual bounds', () => {
    expect(deriveChartGrid('iqoption', { x: .1, y: .2, width: .8, height: .6 }, 'MANUAL').confidence).toBe(1)
    const slots = defaultCalibration()
    expect(calibrationToChartGrid('capitalbear', slots).slots[0]!.chartBounds).toEqual(slots[0]!.bounds)
  })
})
