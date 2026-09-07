import { randomUUID } from 'node:crypto'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { defaultCalibration, type ConfigurationResult, type Platform } from '@quant-screen-trader/shared-types'
import type { PlatformBrowserManager } from '../electron/main/platform-browser'
import { createPlaceholderSlots } from '../src/renderer/src/features/slots/createPlaceholderSlots'

vi.mock('../electron/main/market-ocr', () => ({ TesseractOCRProvider: class { stop = vi.fn(async () => {}); parseText = vi.fn(async () => ({ confidence: 0 })) } }))
const { MarketManager } = await import('../electron/main/market-manager')
function config(platform: Platform, count = 9): ConfigurationResult {
  const stamp = new Date().toISOString(), id = randomUUID()
  return { configuration: { platform, slots: createPlaceholderSlots(platform).map(s => ({ ...s, enabled: s.id <= count, assetName: `ASSET ${s.id}` })) },
    calibrations: [{ id, name: 'Fixture', platform, createdAt: stamp, updatedAt: stamp,
      slots: defaultCalibration(), zoomFactor: 1, referenceBrowserWidth: 900, referenceBrowserHeight: 600 }],
    activeCalibrationId: id, presets: [] }
}
let manager: InstanceType<typeof MarketManager> | undefined
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2026-09-07T12:30:00Z')) })
afterEach(() => { manager?.stop(); vi.useRealTimers(); vi.unstubAllGlobals() })
it('observes 18 isolated slots with bounded latest-per-slot queue and stops disabled slots', async () => {
  const read = vi.fn(async (c: { assetName: string; slotId: number }) => ({ asset: c.assetName, price: String(c.slotId), confidence: 1 }))
  const surface = { available: true, paused: false, revision: 0, bounds: { x: 0, y: 0, width: 900, height: 600 } }
  const browsers = { observationSurface: () => surface, readSlotDOM: read } as unknown as PlatformBrowserManager
  vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})))
  manager = new MarketManager(browsers, { host: '127.0.0.1', port: 8765, healthUrl: 'http://127.0.0.1:8765/health' })
  for (const platform of ['capitalbear', 'iqoption'] as const) { manager.configure(config(platform)); manager.command({ platform, operation: 'start', intervalMs: 250 }) }
  await vi.advanceTimersByTimeAsync(3000)
  const snapshot = manager.command({ platform: 'capitalbear', operation: 'state' })
  expect(snapshot.queueDepth).toBeLessThanOrEqual(18)
  expect(snapshot.dropped).toBeGreaterThan(0)
  expect(snapshot.slots.every(s => s.observation?.price === s.slotId && s.observation.platform === 'capitalbear')).toBe(true)
  manager.configure(config('capitalbear', 0)); read.mockClear()
  await vi.advanceTimersByTimeAsync(1000)
  expect(read.mock.calls.every(([c]) => (c as { platform?: string }).platform === 'iqoption')).toBe(true)
  surface.paused = true; read.mockClear()
  await vi.advanceTimersByTimeAsync(1000)
  expect(read).not.toHaveBeenCalled()
})
it('discards captures across configuration, resize and navigation changes', async () => {
  let finish!: (value: { asset: string; price: string; confidence: number }) => void
  const read = vi.fn(() => new Promise<{ asset: string; price: string; confidence: number }>(r => { finish = r }))
  const surface = { available: true, paused: false, revision: 0, bounds: { x: 0, y: 0, width: 900, height: 600 } }
  manager = new MarketManager({ observationSurface: () => surface, readSlotDOM: read } as unknown as PlatformBrowserManager,
    { host: '127.0.0.1', port: 8765, healthUrl: 'http://127.0.0.1:8765/health' })
  manager.configure(config('capitalbear', 1)); manager.command({ platform: 'capitalbear', operation: 'start' })
  await vi.advanceTimersByTimeAsync(100)
  manager.configure(config('capitalbear', 1))
  surface.revision++
  await vi.advanceTimersByTimeAsync(100)
  expect(read).toHaveBeenCalledTimes(1)
  finish({ asset: 'ASSET 1', price: '1', confidence: 1 })
  await vi.advanceTimersByTimeAsync(1)
  expect(manager.command({ platform: 'capitalbear', operation: 'state' }).slots[0]?.observation).toBeNull()
})
