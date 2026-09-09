import { describe, expect, it } from 'vitest'
import { calibrationToChartGrid, defaultCalibration, deriveChartGrid, normalizedToPixel } from '@quant-screen-trader/shared-types'
import { CapitalBearChartGridResolver, IQOptionChartGridResolver, ManualChartGridResolver } from '../electron/main/chart-grid'

describe('chart grid geometry', () => {
  it.each([new CapitalBearChartGridResolver(), new IQOptionChartGridResolver()])('resolves an inner normalized AUTO grid for $platform', resolver => {
    const grid = resolver.resolve({ width: 1320, height: 594 })
    expect(grid.source).toBe('AUTO')
    expect(grid.bounds).toEqual({ x: .05, y: .12, width: .95, height: .78 })
    expect(grid.bounds).not.toEqual({ x: 0, y: 0, width: 1, height: 1 })
    expect(grid.bounds.x).toBeGreaterThan(0)
    expect(grid.bounds.y).toBeGreaterThan(0)
    expect(grid.bounds.y + grid.bounds.height).toBeLessThan(1)
    expect(grid.slots.map(slot => slot.slotId)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9])
    expect(grid.slots[3]!.chartBounds.x).toBe(grid.slots[0]!.chartBounds.x)
    expect(grid.slots[3]!.chartBounds.y).toBeGreaterThan(grid.slots[0]!.chartBounds.y)
  })
  it('scales with current browser dimensions without changing persisted normalized coordinates', () => {
    const grid = new IQOptionChartGridResolver().resolve({ width: 1000, height: 600 })
    expect(normalizedToPixel(grid.bounds, 1000, 600)).toEqual({ x: 50, y: 72, width: 950, height: 468 })
    expect(normalizedToPixel(grid.bounds, 2000, 1200)).toEqual({ x: 100, y: 144, width: 1900, height: 936 })
  })
  it('derives nine slots from one manual outer rectangle', () => {
    const bounds = { x: .1, y: .2, width: .81, height: .6 }
    const grid = new ManualChartGridResolver('capitalbear').resolve(bounds)
    expect(grid.source).toBe('MANUAL')
    expect(grid.bounds).toEqual(bounds)
    expect(grid.slots).toHaveLength(9)
    expect(grid.slots[8]!.chartBounds).toMatchObject({ x: .64, y: .6, width: .27 })
    expect(grid.slots[8]!.chartBounds.height).toBeCloseTo(.2)
  })
  it('retains irregular legacy slot bounds safely while adding separate price regions', () => {
    const slots = defaultCalibration().map(slot => slot.id === 1 ? { ...slot, bounds: { ...slot.bounds, x: .06 } } : slot)
    const grid = calibrationToChartGrid('capitalbear', slots, 'LEGACY')
    expect(grid.slots[0]!.chartBounds).toEqual(slots[0]!.bounds)
    expect(grid.slots[0]!.priceBounds).toBeDefined()
  })
  it('rejects an invalid manual outer rectangle', () => {
    expect(() => deriveChartGrid('iqoption', { x: .5, y: .5, width: .6, height: .2 }, 'MANUAL')).toThrow()
  })
})
