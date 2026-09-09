import { randomUUID } from 'node:crypto'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { defaultCalibration, type AssetDetectionResult, type ConfigurationResult } from '@quant-screen-trader/shared-types'
import { emptyAsset } from '../electron/main/asset-detector'
import type { PlatformBrowserManager } from '../electron/main/platform-browser'
import { createPlaceholderSlots } from '../src/renderer/src/features/slots/createPlaceholderSlots'
vi.mock('../electron/main/market-ocr', () => ({ TesseractOCRProvider: class { parseText = vi.fn(); stop = vi.fn(async () => {}) } }))
const { AssetSyncManager } = await import('../electron/main/asset-sync')
let manager: InstanceType<typeof AssetSyncManager> | undefined
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2026-09-08T00:00:00Z')) })
afterEach(() => { manager?.stop(); vi.useRealTimers() })
function configuration(): ConfigurationResult {
  const id = randomUUID(), stamp = new Date().toISOString()
  return { configuration: { platform: 'capitalbear', slots: createPlaceholderSlots('capitalbear') }, presets: [], activeCalibrationId: id,
    calibrations: [{ id, platform: 'capitalbear', name: 'Chart regions', createdAt: stamp, updatedAt: stamp, zoomFactor: .7,
      referenceBrowserWidth: 900, referenceBrowserHeight: 600, slots: defaultCalibration() }] }
}
function result(count = 9): AssetDetectionResult {
  return { platform: 'capitalbear', durationMs: 1, overallConfidence: .98,
    slots: Array.from({ length: 9 }, (_, i) => ({ ...emptyAsset('capitalbear', i + 1), source: 'OCR',
      confidence: .98, state: i < count ? 'DETECTED' : 'NOT_FOUND', assetName: i < count ? `Asset ${i + 1} OTC` : null })) }
}
function setup(capture: () => Promise<AssetDetectionResult>, data = configuration()) {
  const save = vi.fn(async (_platform, before, slots) => ({ ...before, configuration: { ...before.configuration, slots } }))
  manager = new AssetSyncManager({ observationSurface: () => ({ available: true, paused: false, bounds: { width: 900, height: 600 } }),
    captureAssetTabs: capture } as unknown as PlatformBrowserManager, save)
  manager.configure(data)
  return save
}
it('applies one coherent top-tab scan, leaving uncertain identities disabled', async () => {
  const scan = result(); scan.slots[1] = { ...emptyAsset('capitalbear', 2, 'UNCERTAIN'), source: 'OCR', confidence: .4 }
  const save = setup(async () => scan)
  const state = await manager!.command({ platform: 'capitalbear', operation: 'sync' })
  expect(state.applied).toBe(8)
  expect(save.mock.calls[0]?.[2][1].enabled).toBe(false)
  expect(save.mock.calls[0]?.[2][0].assetName).toBe('Asset 1 OTC')
})
it('does not apply in-flight detection after a configuration change', async () => {
  let finish!: (value: AssetDetectionResult) => void
  const save = setup(() => new Promise(resolve => { finish = resolve }))
  const pending = manager!.command({ platform: 'capitalbear', operation: 'sync' })
  manager!.configure(configuration()); finish(result()); await pending
  expect(save).not.toHaveBeenCalled()
})
it('does not synchronize on startup; Auto Sync requires repeated stable scans', async () => {
  const capture = vi.fn(async () => result()), save = setup(capture)
  await vi.advanceTimersByTimeAsync(5000)
  expect(capture).not.toHaveBeenCalled()
  await manager!.command({ platform: 'capitalbear', operation: 'auto', enabled: true })
  await vi.advanceTimersByTimeAsync(5000)
  expect(save).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(2000)
  expect(save).toHaveBeenCalledOnce()
})
it('clears absent AUTO slots to empty identities and preserves MANUAL slots', async () => {
  const data = configuration()
  data.configuration.slots[5] = { ...data.configuration.slots[5]!, enabled: true, assetName: 'Old', assetMode: 'AUTO' }
  data.configuration.slots[6] = { ...data.configuration.slots[6]!, enabled: true, assetName: 'Manual', assetMode: 'MANUAL' }
  const save = setup(async () => result(3), data)
  await manager!.command({ platform: 'capitalbear', operation: 'sync' })
  expect(save.mock.calls[0]?.[2][5]).toMatchObject({ enabled: false, assetName: '', displayName: undefined })
  expect(save.mock.calls[0]?.[2][6]).toMatchObject({ enabled: true, assetName: 'Manual' })
})
it('reports uncertain geometry and never saves a shifted partial mapping', async () => {
  const save = setup(async () => { throw new Error('TAB_GEOMETRY_UNCERTAIN: one interior tab is missing') })
  expect((await manager!.command({ platform: 'capitalbear', operation: 'sync' })).error).toContain('TAB_GEOMETRY_UNCERTAIN')
  expect(save).not.toHaveBeenCalled()
})
