import { randomUUID } from 'node:crypto'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { defaultCalibration, type ConfigurationResult } from '@quant-screen-trader/shared-types'
import { mapChartLabels } from '../electron/main/asset-detector'
import type { PlatformBrowserManager } from '../electron/main/platform-browser'
import { createPlaceholderSlots } from '../src/renderer/src/features/slots/createPlaceholderSlots'
const { parse } = vi.hoisted(() => ({ parse: vi.fn() }))
vi.mock('../electron/main/market-ocr', () => ({ TesseractOCRProvider: class { parseText = parse; stop = vi.fn(async () => {}) } }))
const { AssetSyncManager } = await import('../electron/main/asset-sync')
let manager: InstanceType<typeof AssetSyncManager> | undefined
beforeEach(() => { vi.useFakeTimers(); parse.mockReset(); vi.setSystemTime(new Date('2026-09-08T00:00:00Z')) })
afterEach(() => { manager?.stop(); vi.useRealTimers() })
function configuration(inset = true): ConfigurationResult {
  const id = randomUUID(), stamp = new Date().toISOString()
  return { configuration: { platform: 'capitalbear', slots: createPlaceholderSlots('capitalbear') }, presets: [], activeCalibrationId: id,
    calibrations: [{ id, platform: 'capitalbear', name: 'Chart regions', createdAt: stamp, updatedAt: stamp, zoomFactor: 1,
      referenceBrowserWidth: 900, referenceBrowserHeight: 600,
      slots: defaultCalibration().map(s => ({ ...s, bounds: inset ? { ...s.bounds, y: .1 + s.bounds.y * .8, height: s.bounds.height * .8 } : s.bounds })) }] }
}
it('uses calibrated OCR for all missing labels on explicit sync and preserves low-confidence values', async () => {
  const capture = vi.fn(async (c: { slotId: number }) => ({ width: 1, height: 1, grayscale: new Uint8Array([c.slotId]) }))
  parse.mockImplementation(async (image: { grayscale: Uint8Array }) => ({ asset: `Instrument ${image.grayscale[0]} (OTC)`, confidence: image.grayscale[0] === 2 ? .54 : .98 }))
  const save = vi.fn(async (_p, before, slots) => ({ ...before, configuration: { ...before.configuration, slots } }))
  manager = new AssetSyncManager({ observationSurface: () => ({ available: true, paused: false, bounds: { width: 900, height: 600 } }),
    detectAssets: async () => mapChartLabels('capitalbear', []), captureSlot: capture } as unknown as PlatformBrowserManager, save)
  manager.configure(configuration())
  const result = await manager.command({ platform: 'capitalbear', operation: 'sync' })
  expect(capture).toHaveBeenCalledTimes(9)
  expect(result.applied).toBe(8)
  expect(result.detection?.slots[1]?.state).toBe('UNCERTAIN')
  expect(result.detection?.slots[0]?.assetName).toBe('Instrument 1 OTC')
  expect(save.mock.calls[0]?.[2][1].enabled).toBe(false)
})
it('does not OCR the default browser grid or apply an in-flight result after a profile change', async () => {
  let finish!: (v: ReturnType<typeof mapChartLabels>) => void
  const capture = vi.fn()
  const save = vi.fn()
  manager = new AssetSyncManager({ observationSurface: () => ({ available: true, paused: false, bounds: { width: 900, height: 600 } }),
    detectAssets: vi.fn(async () => mapChartLabels('capitalbear', [])), captureSlot: capture } as unknown as PlatformBrowserManager, save)
  manager.configure(configuration(false))
  const result = await manager.command({ platform: 'capitalbear', operation: 'sync' })
  expect(capture).not.toHaveBeenCalled(); expect(result.error).toContain('default full-browser grid')
  manager.stop()
  manager = new AssetSyncManager({ observationSurface: () => ({ available: true, paused: false, bounds: { width: 900, height: 600 } }),
    detectAssets: () => new Promise(r => { finish = r }), captureSlot: capture } as unknown as PlatformBrowserManager, save)
  manager.configure(configuration(false))
  const pending = manager.command({ platform: 'capitalbear', operation: 'sync' })
  manager.configure(configuration())
  finish(mapChartLabels('capitalbear', [])); await pending
  expect(save).not.toHaveBeenCalled()
})
it('requires three consistent Auto Sync OCR attempts and ignores transient names', async () => {
  const names = ['EUR/USD OTC', 'GBP/USD OTC', 'EUR/USD OTC', 'EUR/USD OTC', 'EUR/USD OTC']
  parse.mockImplementation(async () => ({ asset: names.shift() ?? 'EUR/USD OTC', confidence: .98 }))
  const save = vi.fn(async (_p, before, slots) => ({ ...before, configuration: { ...before.configuration, slots } }))
  const capture = vi.fn(async () => ({ width: 1, height: 1, grayscale: new Uint8Array([0]) }))
  manager = new AssetSyncManager({ observationSurface: () => ({ available: true, paused: false, bounds: { width: 900, height: 600 } }),
    detectAssets: async () => mapChartLabels('capitalbear', []), captureSlot: capture } as unknown as PlatformBrowserManager, save)
  const data = configuration()
  data.configuration.slots = data.configuration.slots.map(s => ({ ...s, assetMode: s.id === 1 ? 'AUTO' : 'MANUAL' }))
  manager.configure(data)
  await manager.command({ platform: 'capitalbear', operation: 'auto', enabled: true })
  await vi.advanceTimersByTimeAsync(10000)
  expect(save).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(4000)
  expect(save).toHaveBeenCalledTimes(1)
  expect(save.mock.calls[0]?.[2][0].assetName).toBe('EUR/USD OTC')
  expect(capture).toHaveBeenCalledTimes(5)
})
